"""Atomic, revision-checked persistence for blueprint review sessions."""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import secrets
import threading
from typing import Any, Callable


class StoreError(Exception):
    def __init__(self, code: str, message: str, *, status: int = 422, details: dict[str, Any] | None = None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status
        self.details = details or {}


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


class SessionStore:
    VERSION = 1

    def __init__(self, git_dir: str | os.PathLike[str]):
        self.root = Path(git_dir) / "learning-hub-review" / "v1" / "sessions"
        self.root.mkdir(mode=0o700, parents=True, exist_ok=True)
        self._lock = threading.RLock()

    @staticmethod
    def new_id(prefix: str = "") -> str:
        return prefix + secrets.token_urlsafe(18).replace("-", "").replace("_", "")

    def _path(self, sid: str) -> Path:
        if not isinstance(sid, str) or not sid or not sid.isalnum() or len(sid) > 80:
            raise StoreError("session_not_found", "Session not found", status=404)
        return self.root / f"{sid}.json"

    def _read_unlocked(self, sid: str) -> dict[str, Any]:
        path = self._path(sid)
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError as exc:
            raise StoreError("session_not_found", "Session not found", status=404) from exc
        except (OSError, json.JSONDecodeError) as exc:
            raise StoreError("session_unavailable", "Stored session is unreadable", status=500) from exc
        if not isinstance(data, dict) or data.get("schemaVersion") != self.VERSION or data.get("id") != sid:
            raise StoreError("session_unavailable", "Stored session has an unsupported format", status=500)
        return data

    def get(self, sid: str) -> dict[str, Any]:
        with self._lock:
            return deepcopy(self._read_unlocked(sid))

    def find_latest(self, path: str) -> dict[str, Any] | None:
        with self._lock:
            latest = self._find_latest_unlocked(path)
            return deepcopy(latest) if latest else None

    def _find_latest_unlocked(self, path: str) -> dict[str, Any] | None:
        matches: list[dict[str, Any]] = []
        for file in self.root.glob("*.json"):
            try:
                data = json.loads(file.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            if data.get("schemaVersion") == self.VERSION and data.get("path") == path:
                matches.append(data)
        if not matches:
            return None
        return max(
            matches,
            key=lambda item: (
                item.get("updatedAt", ""),
                int(item.get("revision", 0)),
                item.get("id", ""),
            ),
        )

    def create(
        self,
        *,
        path: str,
        base_head: str,
        base_branch: str | None,
        source_hash: str,
        original: str,
    ) -> dict[str, Any]:
        stamp = now()
        session: dict[str, Any] = {
            "schemaVersion": self.VERSION,
            "id": self.new_id("s"),
            "path": path,
            "baseHead": base_head,
            "baseBranch": base_branch,
            "sourceHash": source_hash,
            "original": original,
            "revision": 1,
            "createdAt": stamp,
            "updatedAt": stamp,
            "comments": [],
            "rounds": [],
            "decisions": {},
            "commentGeneration": 0,
            "decisionGeneration": 0,
            "candidate": None,
            "candidateCommentGeneration": None,
            "candidateDecisionGeneration": None,
            "activeJob": None,
            "finalized": None,
        }
        with self._lock:
            self._write_unlocked(session)
        return deepcopy(session)

    def find_or_create(
        self,
        *,
        path: str,
        base_head: str,
        base_branch: str | None,
        source_hash: str,
        original: str,
    ) -> dict[str, Any]:
        """Return the current review or create one in the same lock scope."""
        with self._lock:
            current = self._find_latest_unlocked(path)
            if (
                current
                and not current.get("finalized")
                and current.get("sourceHash") == source_hash
            ):
                return deepcopy(current)
            return self.create(
                path=path,
                base_head=base_head,
                base_branch=base_branch,
                source_hash=source_hash,
                original=original,
            )

    def update(
        self,
        sid: str,
        expected_revision: int | None,
        operation: Callable[[dict[str, Any]], None],
        *,
        require_revision: bool = True,
    ) -> dict[str, Any]:
        with self._lock:
            session = self._read_unlocked(sid)
            if require_revision and expected_revision is None:
                raise StoreError("revision_required", "If-Match revision is required", status=428)
            current = session["revision"]
            if expected_revision is not None and expected_revision != current:
                raise StoreError(
                    "revision_conflict",
                    f"Expected revision {expected_revision}, but the session is at {current}",
                    status=409,
                    details={"currentRevision": current},
                )
            operation(session)
            session["revision"] = current + 1
            session["updatedAt"] = now()
            self._write_unlocked(session)
            return deepcopy(session)

    def recover_abandoned_jobs(self) -> int:
        """Release persisted jobs that cannot exist after process startup.

        Agent jobs are intentionally process-local. A new ReviewApplication has
        no live jobs, so any activeJob left on disk belongs to a process that no
        longer owns this store. Recovery advances the revision so an old browser
        tab cannot mutate the newly recovered session with a stale If-Match.
        """
        recovered = 0
        with self._lock:
            for file in self.root.glob("*.json"):
                try:
                    session = json.loads(file.read_text(encoding="utf-8"))
                except (OSError, json.JSONDecodeError):
                    continue
                if (
                    not isinstance(session, dict)
                    or session.get("schemaVersion") != self.VERSION
                    or not session.get("activeJob")
                ):
                    continue
                active = session.get("activeJob")
                owner_pid = active.get("ownerPid") if isinstance(active, dict) else None
                if isinstance(owner_pid, int) and owner_pid > 0:
                    try:
                        os.kill(owner_pid, 0)
                    except (OSError, ProcessLookupError):
                        pass
                    else:
                        continue
                session["activeJob"] = None
                session["revision"] = int(session.get("revision", 0)) + 1
                session["updatedAt"] = now()
                self._write_unlocked(session)
                recovered += 1
        return recovered

    def update_job(self, sid: str, job_id: str, operation: Callable[[dict[str, Any]], None]) -> dict[str, Any] | None:
        """Apply a job result only while that exact job still owns the session."""
        with self._lock:
            session = self._read_unlocked(sid)
            active = session.get("activeJob")
            if not isinstance(active, dict) or active.get("id") != job_id:
                return None
            operation(session)
            session["activeJob"] = None
            session["revision"] += 1
            session["updatedAt"] = now()
            self._write_unlocked(session)
            return deepcopy(session)

    def _write_unlocked(self, session: dict[str, Any]) -> None:
        path = self._path(session["id"])
        temporary = path.with_name(f".{path.name}.{secrets.token_hex(6)}.tmp")
        payload = json.dumps(session, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
        try:
            descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, path)
            try:
                directory = os.open(self.root, os.O_RDONLY)
                try:
                    os.fsync(directory)
                finally:
                    os.close(directory)
            except OSError:
                pass
        except OSError as exc:
            try:
                temporary.unlink()
            except OSError:
                pass
            raise StoreError("session_unavailable", "Could not persist the review session", status=500) from exc
