"""Run disposable review agents and validate their complete file manifest."""

from __future__ import annotations

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
        return {
            "operation": operation,
            "path": session["path"],
            "original": session["original"],
            "candidate": session.get("candidate"),
            "comments": session.get("comments", []),
            "decisions": session.get("decisions", {}),
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
            disk_candidate = blueprint.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as exc:
            raise AgentError("invalid_agent_result", "Candidate is not readable UTF-8 HTML") from exc
        baseline = session.get("candidate") or session["original"]
        if candidate is None and disk_candidate != baseline:
            candidate = disk_candidate
        if not isinstance(candidate, str) or not candidate:
            raise AgentError("invalid_agent_result", "Agent result must include a non-empty candidate")
        if len(candidate.encode("utf-8")) > MAX_BLUEPRINT_BYTES:
            raise AgentError("invalid_agent_result", "Candidate exceeds the 2 MiB limit")
        if "<html" not in candidate.lower() or "</html>" not in candidate.lower():
            raise AgentError("invalid_agent_result", "Candidate must be a complete HTML document")

        blueprint.write_text(candidate, encoding="utf-8")
        try:
            _run(["python3", "scripts/validate.py"], cwd=root)
        except RepositoryError as exc:
            raise AgentError("invalid_agent_result", f"Candidate validation failed: {exc.message}") from exc

        comments = result.get("comments", [])
        if operation == "revise" and not session.get("rounds"):
            if not isinstance(comments, list):
                raise AgentError("invalid_agent_result", "Initial result comments must be an array")
        elif comments is None:
            comments = []
        if not isinstance(comments, list):
            raise AgentError("invalid_agent_result", "Result comments must be an array")
        return {"candidate": candidate, "comments": comments, "summary": str(result.get("summary", ""))[:1000]}
