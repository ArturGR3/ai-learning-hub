"""Run disposable review agents and validate their complete file manifest."""

from __future__ import annotations

from html.parser import HTMLParser
import json
import os
from pathlib import Path
import shlex
import shutil
import signal
import subprocess
import tempfile
import threading
import time
from typing import Any, Callable

from .repository import MAX_BLUEPRINT_BYTES, Repository, RepositoryError, _run


MAX_RESULT_BYTES = 2 * 1024 * 1024

_VOID_ELEMENTS = frozenset(
    {
        "area",
        "base",
        "br",
        "col",
        "embed",
        "hr",
        "img",
        "input",
        "link",
        "meta",
        "param",
        "source",
        "track",
        "wbr",
    }
)


class _HtmlNode:
    __slots__ = ("attrs", "children", "parent", "tag")

    def __init__(self, tag: str, attrs: list[tuple[str, str | None]], parent: _HtmlNode | None):
        self.tag = tag
        self.attrs = dict(attrs)
        self.parent = parent
        self.children: list[_HtmlNode | str] = []


class _CandidateParser(HTMLParser):
    """Build the small DOM subset needed to verify review target anchors."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.root = _HtmlNode("document", [], None)
        self._stack = [self.root]

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        node = _HtmlNode(tag.lower(), attrs, self._stack[-1])
        self._stack[-1].children.append(node)
        if node.tag not in _VOID_ELEMENTS:
            self._stack.append(node)

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        node = _HtmlNode(tag.lower(), attrs, self._stack[-1])
        self._stack[-1].children.append(node)

    def handle_endtag(self, tag: str) -> None:
        normalized = tag.lower()
        for index in range(len(self._stack) - 1, 0, -1):
            if self._stack[index].tag == normalized:
                del self._stack[index:]
                return

    def handle_data(self, data: str) -> None:
        self._stack[-1].children.append(data)


class _CandidateIndex:
    """Mirror the iframe's deterministic block keys for server-side checks."""

    def __init__(self, candidate: str):
        parser = _CandidateParser()
        parser.feed(candidate)
        parser.close()
        self.blocks: dict[str, dict[str, str]] = {}
        self.ids: dict[str, dict[str, str]] = {}
        counts: dict[str, int] = {}
        for node in self._walk(parser.root):
            identifier = node.attrs.get("id")
            if identifier and identifier not in self.ids:
                self.ids[identifier] = {
                    "tag": node.tag,
                    "text": self._text(node),
                }
            if not self._is_indexed(node) or self._inside_review_ui(node):
                continue
            section_id = self._section_id(node)
            kind = self._block_kind(node)
            stem = f"{section_id}/{kind}"
            counts[stem] = counts.get(stem, 0) + 1
            key = f"{stem}/{counts[stem]}"
            self.blocks[key] = {
                "kind": kind,
                "sectionId": section_id,
                "text": self._text(node),
            }

    @classmethod
    def _walk(cls, node: _HtmlNode):
        pending = [child for child in reversed(node.children) if isinstance(child, _HtmlNode)]
        while pending:
            current = pending.pop()
            yield current
            pending.extend(child for child in reversed(current.children) if isinstance(child, _HtmlNode))

    @classmethod
    def _text(cls, node: _HtmlNode) -> str:
        parts: list[str] = []
        pending: list[_HtmlNode | str] = [node]
        while pending:
            item = pending.pop()
            if isinstance(item, str):
                parts.append(item)
                continue
            if item.tag in {"script", "style"} or "data-lh-review-ui" in item.attrs:
                continue
            pending.extend(reversed(item.children))
        return "".join(parts)

    @staticmethod
    def _classes(node: _HtmlNode) -> set[str]:
        return set((node.attrs.get("class") or "").split())

    @classmethod
    def _is_indexed(cls, node: _HtmlNode) -> bool:
        classes = cls._classes(node)
        if node.tag in {"header", "footer"} or classes.intersection(
            {"legend", "toc", "diagram", "formula", "where", "key", "cross-refs"}
        ):
            return True
        if node.tag not in {"h2", "h3", "p", "li"}:
            return False
        parent = node.parent
        while parent is not None:
            if parent.tag == "section":
                return True
            parent = parent.parent
        return False

    @classmethod
    def _block_kind(cls, node: _HtmlNode) -> str:
        classes = cls._classes(node)
        if "diagram" in classes:
            return "diagram"
        if "formula" in classes:
            return "formula"
        if "key" in classes:
            return "key"
        return node.tag

    @staticmethod
    def _section_id(node: _HtmlNode) -> str:
        parent = node.parent
        while parent is not None:
            if parent.tag == "section" and parent.attrs.get("id"):
                return str(parent.attrs["id"])
            parent = parent.parent
        return "document"

    @staticmethod
    def _inside_review_ui(node: _HtmlNode) -> bool:
        current: _HtmlNode | None = node
        while current is not None:
            if "data-lh-review-ui" in current.attrs:
                return True
            current = current.parent
        return False


class AgentError(Exception):
    def __init__(self, code: str, message: str, *, status: int = 422):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status


class Cancelled(AgentError):
    def __init__(self):
        super().__init__("job_cancelled", "The agent job was cancelled")


class AgentRunner:
    """Launch one agent in an isolated, remote-free copy of the repository.

    A fixture command can be supplied through LEARNING_HUB_AGENT_COMMAND. It is
    parsed with shlex and is never run through a shell. Placeholders
    ``{prompt_file}``, ``{result_file}``, ``{repo}``, and ``{blueprint}`` are
    replaced. Without placeholders, the prompt text is appended as the final
    argument. The same paths and operation are also exported as
    LEARNING_HUB_REVIEW_* environment variables for simple test fixtures.
    """

    def __init__(self, repository: Repository, *, timeout: float = 600):
        self.repository = repository
        self.timeout = timeout
        self.prompt_path = Path(__file__).with_name("review_prompt.md")

    def agents(self) -> list[dict[str, Any]]:
        override = os.environ.get("LEARNING_HUB_AGENT_COMMAND")
        if override:
            # The UI intentionally exposes only supported production adapters.
            # Tests replace the Codex command, not the public agent identity.
            return [{"id": "codex", "name": "Codex", "available": bool(shlex.split(override)), "fixture": True}]
        return [self._availability(name) for name in ("codex", "claude", "opencode")]

    def _availability(self, name: str) -> dict[str, Any]:
        executable = shutil.which(name)
        record: dict[str, Any] = {"id": name, "name": name.title(), "available": False}
        if not executable:
            record["reason"] = "not installed"
            return record
        try:
            result = subprocess.run(
                [executable, "--version"],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                timeout=5,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired):
            record["reason"] = "version check failed"
            return record
        version = (result.stdout or result.stderr).strip().splitlines()
        if result.returncode or not version:
            record["reason"] = "version check failed"
            return record
        record.update({"available": True, "version": version[0][:160]})
        return record

    def run(
        self,
        session: dict[str, Any],
        *,
        operation: str,
        agent: str,
        cancel: threading.Event,
        on_activity: Callable[[str], None] | None = None,
    ) -> dict[str, Any]:
        if operation not in {"revise", "reconcile"}:
            raise AgentError("invalid_operation", "Unsupported agent operation")
        try:
            prompt_template = self.prompt_path.read_text(encoding="utf-8")
        except FileNotFoundError as exc:
            raise AgentError(
                "review_agent_unavailable",
                "Review agent instructions are unavailable: scripts/review_server/review_prompt.md is missing",
                status=503,
            ) from exc
        except OSError as exc:
            raise AgentError("review_agent_unavailable", "Review agent instructions cannot be read", status=503) from exc

        self.repository.assert_source_current(session)
        if cancel.is_set():
            raise Cancelled()
        with tempfile.TemporaryDirectory(prefix="learning-hub-review-agent-") as temporary:
            root = Path(temporary) / "repo"
            _run(["git", "clone", "--no-hardlinks", "--no-checkout", str(self.repository.root), str(root)], cwd=Path(temporary))
            _run(["git", "remote", "remove", "origin"], cwd=root, check=False)
            _run(["git", "checkout", "--detach", session["baseHead"]], cwd=root)
            blueprint = root / session["path"]
            blueprint.write_text(session.get("candidate") or session["original"], encoding="utf-8")
            review_dir = root / ".review"
            review_dir.mkdir(mode=0o700)
            result_file = review_dir / "result.json"
            context_file = review_dir / "context.json"
            prompt_file = review_dir / "prompt.md"
            context = self._context(session, operation)
            context_file.write_text(json.dumps(context, indent=2, ensure_ascii=False), encoding="utf-8")
            prompt = self._make_prompt(prompt_template, context)
            prompt_file.write_text(prompt, encoding="utf-8")

            command = self._command(agent, prompt, root, prompt_file, result_file, blueprint)
            env = os.environ.copy()
            env.update(
                {
                    "LEARNING_HUB_REVIEW_OPERATION": operation,
                    "LEARNING_HUB_REVIEW_REPO": str(root),
                    "LEARNING_HUB_REVIEW_BLUEPRINT": str(blueprint),
                    "LEARNING_HUB_REVIEW_PROMPT": str(prompt_file),
                    "LEARNING_HUB_REVIEW_CONTEXT": str(context_file),
                    "LEARNING_HUB_REVIEW_RESULT": str(result_file),
                }
            )
            if not os.environ.get("LEARNING_HUB_AGENT_COMMAND"):
                self._harden_agent_environment(env, agent, session["path"])
            self._execute(
                command,
                cwd=root,
                env=env,
                cancel=cancel,
                result_file=result_file,
                on_activity=on_activity,
            )
            return self._validate_result(root, session, operation, result_file, blueprint)

    @staticmethod
    def _context(session: dict[str, Any], operation: str) -> dict[str, Any]:
        comments = [
            comment
            for comment in session.get("comments", [])
            if isinstance(comment, dict) and not AgentRunner._comment_is_closed(comment)
        ]
        visible_ids = {
            str(comment["id"])
            for comment in comments
            if comment.get("id") is not None and str(comment.get("id"))
        }
        stored_decisions = session.get("decisions", {})
        decisions = (
            {
                identifier: decision
                for identifier, decision in stored_decisions.items()
                if str(identifier) in visible_ids
            }
            if isinstance(stored_decisions, dict)
            else {}
        )
        return {
            "operation": operation,
            "path": session["path"],
            "original": session["original"],
            "candidate": session.get("candidate"),
            "comments": comments,
            "decisions": decisions,
            "rules": {
                "networkResearchAllowed": True,
                "externalWritesAllowed": False,
                "gitRemoteAllowed": False,
                "onlyBlueprintMayChange": True,
                "resultPath": ".review/result.json",
            },
        }

    @staticmethod
    def _make_prompt(template: str, context: dict[str, Any]) -> str:
        return (
            template.rstrip()
            + "\n\n## Machine-readable review context\n\n"
            + "Read `.review/context.json` for the complete JSON context. "
            + "Write the requested result to `.review/result.json`.\n\n"
            + "```json\n"
            + json.dumps(context, ensure_ascii=False, indent=2)
            + "\n```\n"
        )

    def _command(
        self,
        agent: str,
        prompt: str,
        root: Path,
        prompt_file: Path,
        result_file: Path,
        blueprint: Path,
    ) -> list[str]:
        override = os.environ.get("LEARNING_HUB_AGENT_COMMAND")
        if override:
            parts = shlex.split(override)
            if not parts:
                raise AgentError("agent_unavailable", "LEARNING_HUB_AGENT_COMMAND is empty", status=503)
            replacements = {
                "{prompt_file}": str(prompt_file),
                "{result_file}": str(result_file),
                "{repo}": str(root),
                "{blueprint}": str(blueprint),
            }
            found = False
            command: list[str] = []
            for part in parts:
                for marker, replacement in replacements.items():
                    if marker in part:
                        found = True
                        part = part.replace(marker, replacement)
                command.append(part)
            if not found:
                command.append(f"Read and follow the review instructions at {prompt_file}.")
            return command

        available = {record["id"]: record for record in self.agents()}
        if agent not in available or not available[agent].get("available"):
            raise AgentError("agent_unavailable", f"Agent is not available: {agent}", status=503)
        if agent == "codex":
            return [
                "codex",
                "exec",
                "--ephemeral",
                "--ignore-user-config",
                "--sandbox",
                "workspace-write",
                "--skip-git-repo-check",
                "-C",
                str(root),
                "Read and follow .review/prompt.md, then write .review/result.json.",
            ]
        if agent == "claude":
            relative_blueprint = blueprint.relative_to(root).as_posix()
            allowed_tools = ",".join(
                [
                    "Read(./**)",
                    f"Edit({relative_blueprint})",
                    f"Write({relative_blueprint})",
                    "Write(.review/result.json)",
                    "Bash(python3 scripts/validate.py)",
                ]
            )
            return [
                "claude",
                "--print",
                "--safe-mode",
                "--no-session-persistence",
                "--no-chrome",
                "--disable-slash-commands",
                "--tools",
                "Read,Edit,Write,Bash",
                "--allowedTools",
                allowed_tools,
                "--mcp-config",
                "{}",
                "--strict-mcp-config",
                "Read and follow .review/prompt.md, then write .review/result.json.",
            ]
        if agent == "opencode":
            return [
                "opencode",
                "run",
                "--pure",
                "Read and follow .review/prompt.md, then write .review/result.json.",
            ]
        raise AgentError("agent_unavailable", f"Unsupported agent: {agent}", status=503)

    @staticmethod
    def _harden_agent_environment(env: dict[str, str], agent: str, blueprint_path: str) -> None:
        if agent != "opencode":
            return
        permissions: dict[str, Any] = {
            "*": "deny",
            "read": "allow",
            "glob": "allow",
            "grep": "allow",
            "list": "allow",
            "edit": {
                "*": "deny",
                blueprint_path: "allow",
                ".review/result.json": "allow",
            },
            "bash": {
                "*": "deny",
                "python3 scripts/validate.py": "allow",
            },
            "external_directory": "deny",
            "task": "deny",
            "skill": "deny",
            "webfetch": "deny",
            "websearch": "deny",
            "question": "deny",
        }
        encoded = json.dumps(permissions, separators=(",", ":"))
        env["OPENCODE_PERMISSION"] = encoded
        env["OPENCODE_CONFIG_CONTENT"] = json.dumps(
            {"permission": permissions},
            separators=(",", ":"),
        )
        env["OPENCODE_AUTO_SHARE"] = "false"
        env["OPENCODE_DISABLE_AUTOUPDATE"] = "true"

    def _execute(
        self,
        command: list[str],
        *,
        cwd: Path,
        env: dict[str, str],
        cancel: threading.Event,
        result_file: Path,
        on_activity: Callable[[str], None] | None,
    ) -> None:
        # A verbose subprocess can fill an unread PIPE and deadlock after it
        # has already written its result. File-backed capture never blocks it.
        with tempfile.TemporaryFile(mode="w+t", encoding="utf-8") as stdout_file, tempfile.TemporaryFile(
            mode="w+t", encoding="utf-8"
        ) as stderr_file:
            try:
                process = subprocess.Popen(
                    command,
                    cwd=str(cwd),
                    env=env,
                    stdin=subprocess.DEVNULL,
                    stdout=stdout_file,
                    stderr=stderr_file,
                    text=True,
                    shell=False,
                    start_new_session=True,
                )
            except OSError as exc:
                raise AgentError("agent_unavailable", f"Could not start agent: {exc}", status=503) from exc
            deadline = time.monotonic() + self.timeout
            sizes = (0, 0)
            result_seen = False
            last_notice = 0.0
            while process.poll() is None:
                if cancel.is_set():
                    self._terminate(process)
                    raise Cancelled()
                if time.monotonic() >= deadline:
                    self._terminate(process)
                    raise AgentError("agent_timeout", "The review agent exceeded its time limit")
                next_sizes = (os.fstat(stdout_file.fileno()).st_size, os.fstat(stderr_file.fileno()).st_size)
                seen_now = result_file.is_file()
                moment = time.monotonic()
                if on_activity and seen_now and not result_seen:
                    on_activity("The agent produced a candidate and is finishing...")
                    last_notice = moment
                elif on_activity and next_sizes != sizes and moment - last_notice >= 1:
                    on_activity("The agent is actively working...")
                    last_notice = moment
                sizes = next_sizes
                result_seen = seen_now
                cancel.wait(0.05)
            stdout_file.seek(0)
            stderr_file.seek(0)
            stdout = stdout_file.read()
            stderr = stderr_file.read()
            if process.returncode:
                detail = (stderr or stdout or "").strip()
                if len(detail) > 1600:
                    detail = detail[-1600:]
                raise AgentError("agent_failed", "The review agent failed" + (f": {detail}" if detail else ""))

    @staticmethod
    def _terminate(process: subprocess.Popen[str]) -> None:
        try:
            os.killpg(process.pid, signal.SIGTERM)
            process.wait(timeout=2)
            return
        except (OSError, subprocess.TimeoutExpired):
            pass
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except OSError:
            pass
        try:
            # Reap the process after the uncatchable signal. Without this wait,
            # cancellation can leave a zombie until the review server exits.
            process.wait(timeout=2)
        except (OSError, subprocess.TimeoutExpired):
            # A process stuck in uninterruptible kernel I/O cannot be reaped by
            # the caller. The timeout keeps server shutdown bounded.
            pass

    @staticmethod
    def _comment_is_closed(comment: dict[str, Any]) -> bool:
        return bool(comment.get("closed")) or comment.get("status") == "closed"

    @classmethod
    def _active_comment_ids(cls, session: dict[str, Any], operation: str) -> list[str]:
        active: list[str] = []
        for comment in session.get("comments", []):
            if not isinstance(comment, dict):
                continue
            if cls._comment_is_closed(comment):
                continue
            if operation != "revise" and comment.get("status") != "queued" and comment.get("decision") not in {
                "no",
                "maybe",
            }:
                continue
            identifier = comment.get("id")
            if identifier is None or not str(identifier):
                raise AgentError("invalid_agent_result", "An active comment has no identifier")
            active.append(str(identifier))
        if len(active) != len(set(active)):
            raise AgentError("invalid_agent_result", "Active comment identifiers are not unique")
        return active

    @classmethod
    def _accepted_comment_ids(cls, session: dict[str, Any], operation: str) -> list[str]:
        if operation != "reconcile":
            return []
        accepted: list[str] = []
        for comment in session.get("comments", []):
            if (
                not isinstance(comment, dict)
                or cls._comment_is_closed(comment)
                or comment.get("decision") != "yes"
            ):
                continue
            identifier = comment.get("id")
            if identifier is None or not str(identifier):
                raise AgentError("invalid_agent_result", "An accepted comment has no identifier")
            accepted.append(str(identifier))
        if len(accepted) != len(set(accepted)):
            raise AgentError("invalid_agent_result", "Accepted comment identifiers are not unique")
        return accepted

    @staticmethod
    def _validate_target_shape(target: Any, comment_id: str) -> dict[str, Any]:
        if not isinstance(target, dict):
            raise AgentError("invalid_agent_result", f"Mapping target for comment {comment_id} must be an object")
        kind = target.get("kind")
        if kind not in {"text", "diagram"}:
            raise AgentError(
                "invalid_agent_result",
                f"Mapping target for comment {comment_id} must target text or a diagram",
            )
        for name in ("sectionId", "diagramId", "quote", "exact", "blockKey"):
            if name in target and (not isinstance(target[name], str) or not target[name]):
                raise AgentError(
                    "invalid_agent_result",
                    f"Mapping target field {name} for comment {comment_id} must be a non-empty string",
                )
        selector = target.get("selector", {})
        if not isinstance(selector, dict):
            raise AgentError("invalid_agent_result", f"Mapping selector for comment {comment_id} must be an object")
        target = dict(target)
        selector = dict(selector)
        if target.get("blockKey") and not selector.get("blockKey"):
            selector["blockKey"] = target["blockKey"]
        if selector:
            target["selector"] = selector
        for name in ("blockKey", "exact"):
            if name in selector and (not isinstance(selector[name], str) or not selector[name]):
                raise AgentError(
                    "invalid_agent_result",
                    f"Mapping selector field {name} for comment {comment_id} must be a non-empty string",
                )
        for name in ("prefix", "suffix"):
            if name in selector and not isinstance(selector[name], str):
                raise AgentError(
                    "invalid_agent_result",
                    f"Mapping selector field {name} for comment {comment_id} must be a string",
                )
        for name in ("start", "end"):
            if name in selector and (
                not isinstance(selector[name], int) or isinstance(selector[name], bool) or selector[name] < 0
            ):
                raise AgentError(
                    "invalid_agent_result",
                    f"Mapping selector field {name} for comment {comment_id} must be a non-negative integer",
                )
        if ("start" in selector) != ("end" in selector) or (
            "start" in selector and selector["end"] < selector["start"]
        ):
            raise AgentError("invalid_agent_result", f"Mapping selector offsets for comment {comment_id} are invalid")
        evidence = selector.get("exact") or target.get("exact") or target.get("quote")
        if evidence:
            target.setdefault("quote", evidence)
            if kind == "text":
                selector.setdefault("exact", evidence)
                target["selector"] = selector
        return target

    @staticmethod
    def _target_exists(index: _CandidateIndex, target: dict[str, Any]) -> bool:
        selector = target.get("selector", {})
        selector_key = selector.get("blockKey")
        diagram_id = target.get("diagramId")
        if selector_key and diagram_id and selector_key != diagram_id:
            return False
        block_key = selector_key or diagram_id
        section_id = target.get("sectionId")
        block = index.blocks.get(block_key) if block_key else None
        if block_key and block is None:
            return False
        if section_id and section_id != "document":
            section = index.ids.get(section_id)
            if section is None or section["tag"] != "section":
                return False
            if block is not None and block["sectionId"] != section_id:
                return False
        elif section_id == "document" and block is not None and block["sectionId"] != "document":
            return False

        if target["kind"] == "diagram":
            if block is None or block["kind"] != "diagram":
                return False
        elif block is not None and block["kind"] == "diagram":
            return False

        if block is not None:
            text = block["text"]
        elif section_id and section_id != "document":
            text = index.ids[section_id]["text"]
        else:
            return False

        evidence = []
        for value in (target.get("quote"), target.get("exact"), selector.get("exact")):
            if value:
                evidence.append(value)
        if not evidence or any(value not in text for value in evidence):
            return False

        exact = selector.get("exact") or target.get("exact") or target.get("quote")
        if "start" in selector:
            start = selector["start"]
            end = selector["end"]
            if end > len(text) or text[start:end] != exact:
                return False
        prefix = selector.get("prefix")
        suffix = selector.get("suffix")
        if prefix is not None or suffix is not None:
            position = -1
            while True:
                position = text.find(exact, position + 1)
                if position < 0:
                    return False
                before = text[:position]
                after = text[position + len(exact) :]
                if (prefix is None or before.endswith(prefix)) and (suffix is None or after.startswith(suffix)):
                    break
        return True

    @classmethod
    def _validate_comment_mappings(
        cls,
        session: dict[str, Any],
        operation: str,
        candidate: str,
        comments: Any,
    ) -> list[dict[str, Any]]:
        if not isinstance(comments, list):
            raise AgentError("invalid_agent_result", "Result comments must be an array")
        active_ids = cls._active_comment_ids(session, operation)
        accepted_ids = cls._accepted_comment_ids(session, operation)
        accepted = set(accepted_ids)
        allowed = set(active_ids) | accepted
        mappings: dict[str, dict[str, Any]] = {}
        for mapping in comments:
            if not isinstance(mapping, dict):
                raise AgentError("invalid_agent_result", "Every comment mapping must be an object")
            raw_identifier = mapping.get("commentId") or mapping.get("id")
            if raw_identifier is None or not str(raw_identifier):
                raise AgentError("invalid_agent_result", "Every comment mapping must include a commentId")
            identifier = str(raw_identifier)
            if identifier not in allowed:
                raise AgentError("invalid_agent_result", f"Agent mapped an inactive or unknown comment: {identifier}")
            if identifier in mappings:
                raise AgentError("invalid_agent_result", f"Agent mapped comment more than once: {identifier}")
            status = mapping.get("status")
            if status not in {"mapped", "unmapped"}:
                raise AgentError(
                    "invalid_agent_result",
                    f"Mapping status for comment {identifier} must be mapped or unmapped",
                )
            targets = mapping.get("targets")
            if not isinstance(targets, list):
                raise AgentError("invalid_agent_result", f"Mapping targets for comment {identifier} must be an array")
            if status == "unmapped":
                if targets:
                    raise AgentError(
                        "invalid_agent_result",
                        f"Unmapped comment {identifier} must not include targets",
                    )
                mappings[identifier] = {"commentId": identifier, "status": "unmapped", "targets": []}
                continue
            if not targets:
                raise AgentError(
                    "invalid_agent_result",
                    f"Mapped comment {identifier} must include one or more targets",
                )
            mappings[identifier] = {
                "commentId": identifier,
                "status": "mapped",
                "targets": [cls._validate_target_shape(target, identifier) for target in targets],
            }

        missing = [identifier for identifier in active_ids if identifier not in mappings]
        if missing:
            raise AgentError(
                "invalid_agent_result",
                "Agent result is missing mappings for active comments: " + ", ".join(missing),
            )

        index = _CandidateIndex(candidate)
        accepted_comments = {
            str(comment.get("id")): comment
            for comment in session.get("comments", [])
            if isinstance(comment, dict) and str(comment.get("id")) in accepted
        }
        for identifier in accepted_ids:
            if identifier in mappings:
                continue
            comment = accepted_comments[identifier]
            stored_targets = comment.get("candidateTargets")
            if not isinstance(stored_targets, list) or not stored_targets:
                stored_anchor = comment.get("candidateAnchor")
                stored_targets = [stored_anchor] if isinstance(stored_anchor, dict) else []
            if not comment.get("mapped") or not stored_targets:
                mappings[identifier] = {"commentId": identifier, "status": "unmapped", "targets": []}
                continue
            try:
                targets = [cls._validate_target_shape(target, identifier) for target in stored_targets]
            except AgentError:
                mappings[identifier] = {"commentId": identifier, "status": "unmapped", "targets": []}
            else:
                mappings[identifier] = {"commentId": identifier, "status": "mapped", "targets": targets}

        validated: list[dict[str, Any]] = []
        for identifier in active_ids + accepted_ids:
            mapping = mappings[identifier]
            if mapping["status"] == "mapped" and not all(
                cls._target_exists(index, target) for target in mapping["targets"]
            ):
                validated.append({"commentId": identifier, "status": "unmapped", "targets": []})
            else:
                validated.append(mapping)
        return validated

    def _validate_result(
        self,
        root: Path,
        session: dict[str, Any],
        operation: str,
        result_file: Path,
        blueprint: Path,
    ) -> dict[str, Any]:
        # A remote created by an agent is an attempted authority escape even if
        # it did not modify a worktree file.
        if _run(["git", "remote"], cwd=root).stdout.strip():
            raise AgentError("unsafe_agent_result", "Agent created a Git remote")
        allowed = {session["path"], ".review/result.json", ".review/context.json", ".review/prompt.md"}
        status = _run(["git", "status", "--porcelain=v1", "--untracked-files=all"], cwd=root).stdout.splitlines()
        changed: set[str] = set()
        for line in status:
            if len(line) < 4:
                continue
            path_text = line[3:]
            if " -> " in path_text:
                path_text = path_text.split(" -> ", 1)[1]
            changed.add(path_text)
            if line[:2].strip() == "D":
                raise AgentError("unsafe_agent_result", f"Agent deleted a file: {path_text}")
        unexpected = changed - allowed
        if unexpected:
            raise AgentError("unsafe_agent_result", f"Agent changed unexpected files: {', '.join(sorted(unexpected))}")
        for name in changed:
            item = root / name
            if item.is_symlink():
                raise AgentError("unsafe_agent_result", f"Agent created a symlink: {name}")
            if item.exists() and item.is_file() and item.stat().st_size > MAX_BLUEPRINT_BYTES:
                raise AgentError("unsafe_agent_result", f"Agent output is too large: {name}")
        if not result_file.is_file() or result_file.is_symlink():
            raise AgentError("invalid_agent_result", "Agent did not write .review/result.json")
        if result_file.stat().st_size > MAX_RESULT_BYTES:
            raise AgentError("invalid_agent_result", "Agent result exceeds the 2 MiB limit")
        try:
            result = json.loads(result_file.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise AgentError("invalid_agent_result", "Agent result is not valid UTF-8 JSON") from exc
        if not isinstance(result, dict):
            raise AgentError("invalid_agent_result", "Agent result must be a JSON object")
        candidate = result.get("candidate")
        try:
            disk_bytes = blueprint.read_bytes()
            disk_candidate = disk_bytes.decode("utf-8")
        except (OSError, UnicodeDecodeError) as exc:
            raise AgentError("invalid_agent_result", "Candidate is not readable UTF-8 HTML") from exc
        baseline = session.get("candidate") or session["original"]
        disk_changed = disk_bytes != baseline.encode("utf-8")
        candidate_bytes: bytes | None = None
        if isinstance(candidate, str):
            try:
                candidate_bytes = candidate.encode("utf-8")
            except UnicodeEncodeError as exc:
                raise AgentError("invalid_agent_result", "Candidate is not valid UTF-8 text") from exc
        if candidate is not None and disk_changed:
            if candidate_bytes != disk_bytes:
                raise AgentError(
                    "invalid_agent_result",
                    "Candidate in .review/result.json does not exactly match the edited blueprint file",
                )
        if candidate is None and disk_changed:
            candidate = disk_candidate
            candidate_bytes = disk_bytes
        if not isinstance(candidate, str) or not candidate:
            raise AgentError("invalid_agent_result", "Agent result must include a non-empty candidate")
        if candidate_bytes is None:
            candidate_bytes = candidate.encode("utf-8")
        if len(candidate_bytes) > MAX_BLUEPRINT_BYTES:
            raise AgentError("invalid_agent_result", "Candidate exceeds the 2 MiB limit")
        if "<html" not in candidate.lower() or "</html>" not in candidate.lower():
            raise AgentError("invalid_agent_result", "Candidate must be a complete HTML document")

        blueprint.write_bytes(candidate_bytes)
        try:
            _run(["python3", "scripts/validate.py"], cwd=root)
        except RepositoryError as exc:
            raise AgentError("invalid_agent_result", f"Candidate validation failed: {exc.message}") from exc

        comments = self._validate_comment_mappings(
            session,
            operation,
            candidate,
            result.get("comments", []),
        )
        return {"candidate": candidate, "comments": comments, "summary": str(result.get("summary", ""))[:1000]}
