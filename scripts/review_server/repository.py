"""Repository access and transactional blueprint filing.

This module is deliberately the only place where the review service reads or
writes the live repository. Paths supplied by browsers are treated as hostile.
"""

from __future__ import annotations

import hashlib
import fcntl
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import subprocess
import tempfile
import threading
from typing import Any


MAX_BLUEPRINT_BYTES = 2 * 1024 * 1024
_BRANCH_RE = re.compile(r"^(?![-.])(?!.*(?:\.\.|@\{|//))[A-Za-z0-9._/-]+(?<![/.])$")


class RepositoryError(Exception):
    """A safe, user-facing repository operation error."""

    def __init__(self, code: str, message: str, *, status: int = 422):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status


def _run(
    args: list[str],
    *,
    cwd: Path,
    timeout: float = 120,
    check: bool = True,
    env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    try:
        result = subprocess.run(
            args,
            cwd=str(cwd),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=timeout,
            check=False,
            env=env,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise RepositoryError("repository_command_failed", f"Could not run {args[0]}: {exc}") from exc
    if check and result.returncode:
        detail = (result.stderr or result.stdout).strip()
        if len(detail) > 1200:
            detail = detail[-1200:]
        raise RepositoryError(
            "repository_command_failed",
            f"{args[0]} failed" + (f": {detail}" if detail else ""),
        )
    return result


class Repository:
    """A validated Learning Hub repository."""

    def __init__(self, root: str | os.PathLike[str]):
        candidate = Path(root).resolve()
        top = _run(["git", "rev-parse", "--show-toplevel"], cwd=candidate).stdout.strip()
        self.root = Path(top).resolve()
        if self.root != candidate:
            candidate = self.root
        self.git_dir = Path(
            _run(["git", "rev-parse", "--path-format=absolute", "--git-dir"], cwd=self.root).stdout.strip()
        ).resolve()
        self._finalize_lock = threading.RLock()
        self._server_lock_file: Any = None

    @classmethod
    def discover(cls, start: str | os.PathLike[str] | None = None) -> "Repository":
        return cls(start or os.getcwd())

    def git_text(self, *args: str, check: bool = True) -> str:
        return _run(["git", *args], cwd=self.root, check=check).stdout.strip()

    def head(self) -> str:
        return self.git_text("rev-parse", "HEAD")

    def branch(self) -> str | None:
        value = self.git_text("symbolic-ref", "--quiet", "--short", "HEAD", check=False)
        return value or None

    def remote_contains(self, commit: str, branch: str | None) -> bool:
        """Return whether the local origin-tracking ref contains a filed commit."""
        if not re.fullmatch(r"[0-9a-f]{40}", str(commit)):
            return False
        if not branch or not _BRANCH_RE.fullmatch(branch):
            return False
        remote_ref = f"refs/remotes/origin/{branch}"
        result = _run(
            ["git", "merge-base", "--is-ancestor", commit, remote_ref],
            cwd=self.root,
            check=False,
        )
        return result.returncode == 0

    def head_contains(self, commit: str) -> bool:
        if not re.fullmatch(r"[0-9a-f]{40}", str(commit)):
            return False
        result = _run(
            ["git", "merge-base", "--is-ancestor", commit, "HEAD"],
            cwd=self.root,
            check=False,
        )
        return result.returncode == 0

    def acquire_server_lock(self) -> None:
        """Hold the repository-wide review server lock until this process exits."""
        if self._server_lock_file is not None:
            return
        lock_dir = self.git_dir / "learning-hub-review" / "v1"
        try:
            lock_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
            handle = (lock_dir / "server.lock").open("a+", encoding="utf-8")
            try:
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as exc:
                handle.close()
                raise RepositoryError(
                    "review_server_running",
                    "Another Learning Hub review server is already running for this repository",
                    status=409,
                ) from exc
            handle.seek(0)
            handle.truncate()
            handle.write(f"pid={os.getpid()}\n")
            handle.flush()
        except RepositoryError:
            raise
        except OSError as exc:
            raise RepositoryError(
                "review_server_lock_failed",
                "Could not acquire the Learning Hub review server lock",
                status=500,
            ) from exc
        self._server_lock_file = handle

    def release_server_lock(self) -> None:
        handle = self._server_lock_file
        if handle is None:
            return
        self._server_lock_file = None
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        finally:
            handle.close()

    @staticmethod
    def hash_bytes(content: bytes) -> str:
        return hashlib.sha256(content).hexdigest()

    def validate_blueprint_path(self, value: Any, *, must_exist: bool = True) -> tuple[str, Path]:
        if not isinstance(value, str) or not value:
            raise RepositoryError("invalid_path", "Blueprint path must be a non-empty string")
        if "\x00" in value or "\\" in value:
            raise RepositoryError("invalid_path", "Blueprint path contains forbidden characters")
        pure = PurePosixPath(value)
        if pure.is_absolute() or len(pure.parts) != 2 or pure.parts[0] != "topics":
            raise RepositoryError("invalid_path", "Only topics/<name>.html may be reviewed")
        name = pure.parts[1]
        if name in {".", ".."} or not name.endswith(".html") or name.startswith("."):
            raise RepositoryError("invalid_path", "Only visible topics/*.html files may be reviewed")
        normalized = pure.as_posix()
        path = self.root / normalized
        topics = (self.root / "topics").resolve()
        try:
            resolved = path.resolve(strict=must_exist)
        except (OSError, RuntimeError) as exc:
            raise RepositoryError("blueprint_not_found", f"Blueprint does not exist: {normalized}", status=404) from exc
        if resolved.parent != topics:
            raise RepositoryError("invalid_path", "Blueprint path escapes topics/")
        if must_exist:
            if path.is_symlink() or not resolved.is_file():
                raise RepositoryError("invalid_path", "Blueprint must be a regular, non-symlink file")
            if resolved.stat().st_size > MAX_BLUEPRINT_BYTES:
                raise RepositoryError("blueprint_too_large", "Blueprint exceeds the 2 MiB limit")
        return normalized, resolved

    def read_blueprint(self, value: Any) -> tuple[str, str, str]:
        normalized, path = self.validate_blueprint_path(value)
        try:
            raw = path.read_bytes()
            text = raw.decode("utf-8")
        except (OSError, UnicodeDecodeError) as exc:
            raise RepositoryError("invalid_blueprint", "Blueprint must be readable UTF-8 HTML") from exc
        return normalized, text, self.hash_bytes(raw)

    def read_public_file(self, url_path: str) -> tuple[bytes, Path]:
        """Read index.html or an assets/ file, and nothing else."""
        if "\x00" in url_path or "\\" in url_path:
            raise RepositoryError("not_found", "File not found", status=404)
        clean = url_path.lstrip("/")
        if clean in {"", "index.html"}:
            path = self.root / "index.html"
        else:
            pure = PurePosixPath(clean)
            if pure.is_absolute() or not pure.parts or pure.parts[0] != "assets" or ".." in pure.parts:
                raise RepositoryError("not_found", "File not found", status=404)
            path = self.root.joinpath(*pure.parts)
            assets = (self.root / "assets").resolve()
            try:
                resolved = path.resolve(strict=True)
            except (OSError, RuntimeError) as exc:
                raise RepositoryError("not_found", "File not found", status=404) from exc
            if resolved != assets and assets not in resolved.parents:
                raise RepositoryError("not_found", "File not found", status=404)
            path = resolved
        if path.is_symlink() or not path.is_file():
            raise RepositoryError("not_found", "File not found", status=404)
        try:
            return path.read_bytes(), path
        except OSError as exc:
            raise RepositoryError("not_found", "File not found", status=404) from exc

    def source_is_current(self, session: dict[str, Any], *, require_head: bool = False) -> bool:
        try:
            _, _, digest = self.read_blueprint(session["path"])
        except (RepositoryError, KeyError):
            return False
        if digest != session.get("sourceHash"):
            return False
        if require_head and self.head() != session.get("baseHead"):
            return False
        return True

    def assert_source_current(self, session: dict[str, Any], *, require_head: bool = False) -> None:
        if not self.source_is_current(session, require_head=require_head):
            raise RepositoryError(
                "stale_source",
                "The blueprint or repository changed after this review started. Start a new review.",
                status=409,
            )

    def _assert_clean_at_base(self, session: dict[str, Any]) -> None:
        self.assert_source_current(session, require_head=True)
        if self.branch() != session.get("baseBranch"):
            raise RepositoryError("stale_source", "The checked-out branch changed after this review started", status=409)
        status = self.git_text("status", "--porcelain=v1", "--untracked-files=normal")
        if status:
            raise RepositoryError(
                "working_tree_not_clean",
                "Finalize requires a clean working tree so the live repository can fast-forward safely",
                status=409,
            )

    def finalize_blocked_reason(self, session: dict[str, Any]) -> str | None:
        try:
            self._assert_clean_at_base(session)
        except RepositoryError as exc:
            return exc.message
        return None

    def finalize(
        self,
        session: dict[str, Any],
        candidate: str,
        log_message: str | None = None,
        commit_details: list[str] | None = None,
    ) -> dict[str, Any]:
        """File a candidate in a disposable clone, then fast-forward the live repo."""
        if not isinstance(candidate, str) or not candidate:
            raise RepositoryError("candidate_unavailable", "There is no candidate to finalize")
        encoded = candidate.encode("utf-8")
        if len(encoded) > MAX_BLUEPRINT_BYTES:
            raise RepositoryError("candidate_too_large", "Candidate exceeds the 2 MiB limit")
        path, _ = self.validate_blueprint_path(session.get("path"))
        branch = session.get("baseBranch")
        if branch is not None and (not isinstance(branch, str) or not _BRANCH_RE.fullmatch(branch)):
            raise RepositoryError("invalid_branch", "The review was created on an invalid branch")

        with self._finalize_lock:
            self._assert_clean_at_base(session)
            remote_url = self.git_text("remote", "get-url", "origin", check=False)
            if remote_url and not branch:
                raise RepositoryError("detached_head", "Cannot push a finalized review from detached HEAD")
            with tempfile.TemporaryDirectory(prefix="learning-hub-finalize-") as temporary:
                clone = Path(temporary) / "repo"
                _run(["git", "clone", "--no-hardlinks", "--no-checkout", str(self.root), str(clone)], cwd=Path(temporary))
                _run(["git", "remote", "remove", "origin"], cwd=clone, check=False)
                _run(["git", "checkout", "--detach", session["baseHead"]], cwd=clone)
                if branch:
                    _run(["git", "branch", "-f", branch, session["baseHead"]], cwd=clone)
                    _run(["git", "switch", branch], cwd=clone)

                target = clone / path
                target.write_text(candidate, encoding="utf-8")
                _run(["python3", "scripts/validate.py"], cwd=clone)
                _run(["python3", "scripts/build-index.py"], cwd=clone)

                summary = (log_message or f"{Path(path).stem} - reviewed blueprint").strip()
                if not summary or "\n" in summary or "\r" in summary:
                    raise RepositoryError("invalid_log_message", "Log message must be one non-empty line")
                from datetime import date

                log_path = clone / "log.md"
                with log_path.open("a", encoding="utf-8") as handle:
                    handle.write(f"\n## [{date.today().isoformat()}] refine | {summary}\n")
                _run(["git", "add", "--", path, "index.html", "log.md"], cwd=clone)
                changed = {
                    line[3:]
                    for line in self._git_status_lines(clone)
                    if len(line) >= 4
                }
                allowed = {path, "index.html", "log.md"}
                unexpected = changed - allowed
                if unexpected or path not in changed or "log.md" not in changed:
                    raise RepositoryError(
                        "unexpected_finalize_changes",
                        "Finalize produced an unexpected file manifest"
                        + (f": {', '.join(sorted(unexpected))}" if unexpected else ""),
                    )
                _run(["python3", "scripts/validate.py"], cwd=clone)
                env = os.environ.copy()
                env.setdefault("GIT_AUTHOR_NAME", "Learning Hub Review")
                env.setdefault("GIT_AUTHOR_EMAIL", "review@localhost")
                env.setdefault("GIT_COMMITTER_NAME", env["GIT_AUTHOR_NAME"])
                env.setdefault("GIT_COMMITTER_EMAIL", env["GIT_AUTHOR_EMAIL"])
                verb = "Refine"
                message = f"{verb} {Path(path).name} - {summary}"
                commit_command = ["git", "commit", "-m", message]
                details = [item.strip()[:400] for item in (commit_details or []) if item.strip()]
                if details:
                    commit_command.extend(["-m", "Review decisions:\n\n" + "\n".join(f"- {item}" for item in details)])
                _run(commit_command, cwd=clone, env=env)
                commit = _run(["git", "rev-parse", "HEAD"], cwd=clone).stdout.strip()
                parent = _run(["git", "rev-parse", "HEAD^"], cwd=clone).stdout.strip()
                if parent != session["baseHead"]:
                    raise RepositoryError("non_fast_forward", "Finalized commit is not based on the reviewed HEAD")
                committed = set(
                    _run(["git", "diff-tree", "--no-commit-id", "--name-only", "-r", "HEAD"], cwd=clone).stdout.splitlines()
                )
                if not committed.issubset(allowed) or path not in committed:
                    raise RepositoryError("unexpected_finalize_changes", "Finalized commit contains unexpected files")
                # Fetching the candidate only adds an unreachable object to the
                # live repository. Recheck the branch and worktree after that
                # preparation, before either visible repository can move.
                _run(["git", "fetch", str(clone), commit], cwd=self.root)
                self._assert_clean_at_base(session)

                # Keep the live checkout as the first visible mutation. A
                # failed remote push then leaves a normal local commit that can
                # be pushed manually, rather than publishing a commit that the
                # checked-out repository never adopted.
                _run(["git", "merge", "--ff-only", commit], cwd=self.root)
                pushed = False
                push_error = None
                if remote_url:
                    try:
                        _run(
                            ["git", "push", "origin", f"{commit}:refs/heads/{branch}"],
                            cwd=self.root,
                            timeout=180,
                        )
                        pushed = True
                    except RepositoryError as exc:
                        # The local fast-forward is the durable filing boundary.
                        # A failed push is reported without rolling back or
                        # leaving the review session live and retryable against
                        # a base commit that no longer exists in the checkout.
                        push_error = exc.message
                result = {"commit": commit, "pushed": pushed, "path": path}
                if push_error:
                    result["pushError"] = push_error
                return result

    @staticmethod
    def _git_status_lines(repo: Path) -> list[str]:
        output = _run(
            ["git", "status", "--porcelain=v1", "--untracked-files=all"],
            cwd=repo,
        ).stdout
        return [line for line in output.splitlines() if line]
