"""Loopback-only HTTP application for blueprint review."""

from __future__ import annotations

from collections import Counter
from copy import deepcopy
import hashlib
from html.parser import HTMLParser
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import mimetypes
import os
from pathlib import Path
import re
import secrets
import sys
import threading
import traceback
from typing import Any
from urllib.parse import parse_qs, quote, urlsplit

from .agent_runner import AgentError, AgentRunner, Cancelled
from .repository import Repository, RepositoryError
from .review_diff import semantic_changes, summary_for_comment
from .session_store import SessionStore, StoreError, now


MAX_REQUEST_BYTES = 512 * 1024


class _DocumentLocator(HTMLParser):
    """Locate real document tags while ignoring comments and raw script text."""

    def __init__(self, content: str):
        super().__init__(convert_charrefs=False)
        self.content = content
        self._line_starts = [0]
        self._line_starts.extend(match.end() for match in re.finditer(r"\n", content))
        self.html_tag: tuple[int, int] | None = None
        self.head_end: int | None = None
        self.body_end: int | None = None
        self.scripts: list[tuple[int, int]] = []
        self._script_start: int | None = None
        self.feed(content)
        self.close()

    def _absolute(self) -> int:
        line, column = self.getpos()
        return self._line_starts[line - 1] + column

    def _start_span(self) -> tuple[int, int]:
        start = self._absolute()
        return start, start + len(self.get_starttag_text() or "")

    def _end_span(self) -> tuple[int, int]:
        start = self._absolute()
        closing = self.content.find(">", start)
        return start, len(self.content) if closing < 0 else closing + 1

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        normalized = tag.lower()
        start, end = self._start_span()
        if normalized == "html" and self.html_tag is None:
            self.html_tag = (start, end)
        elif normalized == "head" and self.head_end is None:
            self.head_end = end
        elif normalized == "script":
            self._script_start = start

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() == "script":
            self.scripts.append(self._start_span())

    def handle_endtag(self, tag: str) -> None:
        normalized = tag.lower()
        start, end = self._end_span()
        if normalized == "script" and self._script_start is not None:
            self.scripts.append((self._script_start, end))
            self._script_start = None
        elif normalized == "body" and self.body_end is None:
            self.body_end = start


def document_csp(port: int, nonce: str) -> str:
    local = f"http://127.0.0.1:{port} http://localhost:{port}"
    return (
        "default-src 'none'; "
        f"base-uri {local}; "
        f"style-src 'unsafe-inline' {local}; "
        f"font-src {local}; "
        f"script-src 'nonce-{nonce}'; "
        f"img-src data: {local}; "
        "connect-src 'none'; "
        "form-action 'none'; "
        "frame-src 'none'; "
        "child-src 'none'; "
        "object-src 'none'; "
        "media-src 'none'; "
        "worker-src 'none'; "
        "manifest-src 'none'; "
        f"frame-ancestors {local}"
    )


_SESSION_ROUTE = re.compile(r"^/api/v1/sessions/([A-Za-z0-9]+)$")
_DOCUMENT_ROUTE = re.compile(r"^/api/v1/sessions/([A-Za-z0-9]+)/document$")
_COMMENTS_ROUTE = re.compile(r"^/api/v1/sessions/([A-Za-z0-9]+)/comments$")
_COMMENT_ROUTE = re.compile(r"^/api/v1/sessions/([A-Za-z0-9]+)/comments/([A-Za-z0-9]+)$")
_REVISIONS_ROUTE = re.compile(r"^/api/v1/sessions/([A-Za-z0-9]+)/revisions$")
_DECISION_ROUTE = re.compile(r"^/api/v1/sessions/([A-Za-z0-9]+)/decisions/([A-Za-z0-9]+)$")
_RECONCILE_ROUTE = re.compile(r"^/api/v1/sessions/([A-Za-z0-9]+)/reconcile$")
_FINALIZE_ROUTE = re.compile(r"^/api/v1/sessions/([A-Za-z0-9]+)/finalize$")
_JOB_ROUTE = re.compile(r"^/api/v1/jobs/([A-Za-z0-9]+)$")


class ApiError(Exception):
    def __init__(self, code: str, message: str, *, status: int = 422):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status


class ReviewApplication:
    """Review state, jobs, and response shaping independent of HTTP parsing."""

    def __init__(self, repository: Repository):
        self.repository = repository
        repository.acquire_server_lock()
        try:
            self.store = SessionStore(repository.git_dir)
            self.store.recover_abandoned_jobs()
            self.runner = AgentRunner(repository)
            self.token = secrets.token_urlsafe(32)
            self.test_same_origin = os.environ.get("LEARNING_HUB_TEST_SAME_ORIGIN") == "1"
            self.jobs: dict[str, dict[str, Any]] = {}
            self._jobs_lock = threading.RLock()
        except Exception:
            repository.release_server_lock()
            raise

    def bootstrap(self, path: str) -> dict[str, Any]:
        base_head = self.repository.head()
        base_branch = self.repository.branch()
        normalized, _text, digest = self.repository.read_blueprint(path)
        session = None
        pending_push = self.store.find_pending_push(normalized, base_branch=base_branch)
        if pending_push:
            finalized = pending_push.get("finalized") or {}
            commit = str(finalized.get("commit") or "")
            if self.repository.head_contains(commit) and not self.repository.remote_contains(commit, base_branch):
                session = pending_push
        if session is None:
            session = self.store.find_latest(
                normalized,
                base_head=base_head,
                base_branch=base_branch,
                source_hash=digest,
            )
            if session and session.get("finalized"):
                session = None
        return {
            "path": normalized,
            "token": self.token,
            "agents": [item for item in self.runner.agents() if item.get("available")],
            "session": self.public_session(session) if session else None,
        }

    def create_session(self, path: str) -> dict[str, Any]:
        base_head = self.repository.head()
        base_branch = self.repository.branch()
        normalized, original, digest = self.repository.read_blueprint(path)
        created = self.store.find_or_create(
            path=normalized,
            base_head=base_head,
            base_branch=base_branch,
            source_hash=digest,
            original=original,
        )
        return self.public_session(created)

    def public_session(self, session: dict[str, Any] | None) -> dict[str, Any] | None:
        if session is None:
            return None
        result = deepcopy(session)
        change_groups = semantic_changes(session["original"], session["candidate"]) if session.get("candidate") else []
        for comment in result.get("comments", []):
            summary = summary_for_comment(comment, change_groups)
            if summary:
                comment["changeSummary"] = summary
        result.pop("original", None)
        result.pop("candidate", None)
        sid = result["id"]
        result["originalUrl"] = f"/api/v1/sessions/{quote(sid)}/document?view=original"
        result["candidateUrl"] = f"/api/v1/sessions/{quote(sid)}/document?view=candidate"
        result["candidateAvailable"] = bool(session.get("candidate"))
        result["complete"] = bool(session.get("finalized"))
        active = result.get("activeJob")
        if isinstance(active, dict):
            with self._jobs_lock:
                live = self.jobs.get(str(active.get("id")))
            if live:
                result["activeJob"] = self.public_job(live)

        comments = result.get("comments", [])
        has_candidate = result["candidateAvailable"]
        pending = False
        all_resolved = bool(comments)
        for comment in comments:
            if self._comment_is_closed(comment):
                continue
            decision = comment.get("decision")
            note = str(comment.get("note") or "").strip()
            if comment.get("status") == "queued" or decision in {"no", "maybe"}:
                pending = True
            if decision == "maybe" and not note:
                pending = True
            if decision != "yes":
                all_resolved = False
        comment_generation_current = (
            session.get("candidateCommentGeneration") == session.get("commentGeneration")
        )
        decision_generation_current = (
            session.get("candidateDecisionGeneration") == session.get("decisionGeneration")
        )
        generations_current = comment_generation_current and decision_generation_current
        result["needsReconciliation"] = bool(has_candidate and (pending or not generations_current))
        result["canRevise"] = bool(comments and not active and not result["complete"])
        result["canFinalize"] = bool(
            has_candidate
            and all_resolved
            and generations_current
            and not active
            and not result["complete"]
        )
        if result["canFinalize"]:
            blocked = self.repository.finalize_blocked_reason(session)
            if blocked:
                result["canFinalize"] = False
                result["finalizeBlockedReason"] = blocked
        return result

    @staticmethod
    def _comment(session: dict[str, Any], cid: str) -> dict[str, Any]:
        for comment in session.get("comments", []):
            if comment.get("id") == cid:
                return comment
        raise ApiError("comment_not_found", "Comment not found", status=404)

    @staticmethod
    def _comment_is_closed(comment: dict[str, Any]) -> bool:
        return bool(comment.get("closed")) or comment.get("status") == "closed"

    @staticmethod
    def _valid_anchor(value: Any) -> dict[str, Any]:
        if not isinstance(value, dict):
            raise ApiError("invalid_anchor", "Comment anchor must be an object")
        kind = value.get("kind")
        if kind not in {"text", "diagram"}:
            raise ApiError("invalid_anchor", "Comment anchor must target text or a diagram")
        quote_text = value.get("quote", "")
        if not isinstance(quote_text, str) or len(quote_text) > 12000:
            raise ApiError("invalid_anchor", "Comment anchor quote is invalid")
        selector = value.get("selector")
        if selector is not None and not isinstance(selector, dict):
            raise ApiError("invalid_anchor", "Comment anchor selector must be an object")
        if kind == "text" and isinstance(selector, dict):
            block_key = selector.get("blockKey")
            end_block_key = selector.get("endBlockKey", block_key)
            if selector.get("multiBlock") is True or (
                isinstance(block_key, str)
                and isinstance(end_block_key, str)
                and block_key != end_block_key
            ):
                raise ApiError(
                    "unsupported_selection",
                    "Text selections must stay within one review block",
                )
        encoded = json.dumps(value, ensure_ascii=False).encode("utf-8")
        if len(encoded) > 64 * 1024:
            raise ApiError("invalid_anchor", "Comment anchor is too large")
        return deepcopy(value)

    @classmethod
    def _candidate_mapping(
        cls,
        mapping: Any,
        source_anchor: Any,
    ) -> tuple[bool, dict[str, Any], list[dict[str, Any]]]:
        """Validate an agent mapping without guessing a candidate location."""
        source_kind = source_anchor.get("kind") if isinstance(source_anchor, dict) else "text"
        unmapped_anchor = {
            "kind": source_kind if source_kind in {"text", "diagram"} else "text",
            "quote": "",
            "mapped": False,
            "unmapped": True,
        }
        if not isinstance(mapping, dict) or mapping.get("status") not in {"mapped", "unmapped"}:
            return False, unmapped_anchor, []
        if mapping["status"] == "unmapped":
            return False, unmapped_anchor, []
        targets = mapping.get("targets")
        if not isinstance(targets, list) or not targets:
            return False, unmapped_anchor, []
        validated: list[dict[str, Any]] = []
        try:
            for target_value in targets:
                target = cls._valid_anchor(target_value)
                selector = target.get("selector")
                block_key = selector.get("blockKey") if isinstance(selector, dict) else None
                has_location = bool(block_key or target.get("diagramId") or target.get("sectionId"))
                if not has_location:
                    return False, unmapped_anchor, []
                target["mapped"] = True
                target.pop("unmapped", None)
                validated.append(target)
        except ApiError:
            return False, unmapped_anchor, []
        return True, deepcopy(validated[0]), validated

    @staticmethod
    def _valid_body(value: Any) -> str:
        if not isinstance(value, str) or not value.strip():
            raise ApiError("invalid_comment", "Comment text is required")
        text = value.strip()
        if len(text) > 12000:
            raise ApiError("invalid_comment", "Comment text is too long")
        return text

    def add_comment(self, sid: str, revision: int | None, payload: dict[str, Any]) -> dict[str, Any]:
        body = self._valid_body(payload.get("body"))
        anchor = self._valid_anchor(payload.get("anchor"))

        def operation(session: dict[str, Any]) -> None:
            if session.get("finalized") or session.get("activeJob"):
                raise ApiError("session_busy", "Comments cannot change while this review is busy", status=409)
            comment = {
                "id": SessionStore.new_id("c"),
                "body": body,
                "anchor": anchor,
                "originalAnchor": anchor,
                "candidateAnchor": None,
                "mapped": True,
                "status": "queued",
                "decision": None,
                "note": "",
                "needsDecision": False,
                "createdAt": now(),
            }
            session.setdefault("comments", []).append(comment)
            session["commentGeneration"] = int(session.get("commentGeneration", 0)) + 1

        return self.public_session(self.store.update(sid, revision, operation)) or {}

    def update_comment(self, sid: str, cid: str, revision: int | None, payload: dict[str, Any]) -> dict[str, Any]:
        body = self._valid_body(payload.get("body"))
        anchor_value = payload.get("anchor")
        anchor = self._valid_anchor(anchor_value) if anchor_value is not None else None

        def operation(session: dict[str, Any]) -> None:
            if session.get("candidate") or session.get("activeJob") or session.get("finalized"):
                raise ApiError("comment_locked", "Only queued comments can be edited", status=409)
            comment = self._comment(session, cid)
            comment["body"] = body
            if anchor is not None:
                comment["anchor"] = anchor
                comment["originalAnchor"] = anchor

        return self.public_session(self.store.update(sid, revision, operation)) or {}

    def delete_comment(self, sid: str, cid: str, revision: int | None) -> dict[str, Any]:
        def operation(session: dict[str, Any]) -> None:
            if session.get("candidate") or session.get("activeJob") or session.get("finalized"):
                raise ApiError("comment_locked", "Only queued comments can be removed", status=409)
            comment = self._comment(session, cid)
            session["comments"].remove(comment)

        return self.public_session(self.store.update(sid, revision, operation)) or {}

    def decide(
        self,
        sid: str,
        cid: str,
        revision: int | None,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        decision = payload.get("decision")
        note_value = payload.get("note", "")
        if decision not in {"yes", "no", "maybe"}:
            raise ApiError("invalid_decision", "Decision must be yes, no, or maybe")
        if not isinstance(note_value, str) or len(note_value) > 12000:
            raise ApiError("invalid_decision", "Decision feedback is invalid")
        note = note_value.strip()
        if decision == "maybe" and not note:
            raise ApiError("feedback_required", "Maybe requires feedback")

        def operation(session: dict[str, Any]) -> None:
            if not session.get("candidate") or session.get("activeJob") or session.get("finalized"):
                raise ApiError("decision_unavailable", "This comment is not ready for a decision", status=409)
            comment = self._comment(session, cid)
            if comment.get("status") == "queued" or comment.get("needsDecision") is False:
                raise ApiError("decision_unavailable", "This comment has not been revised yet", status=409)
            comment["decision"] = decision
            comment["note"] = note
            session.setdefault("decisions", {})[cid] = {
                "decision": decision,
                "note": note,
                "round": len(session.get("rounds", [])),
                "decidedAt": now(),
            }
            session["decisionGeneration"] = int(session.get("decisionGeneration", 0)) + 1
            if decision == "yes":
                # Approval does not require another candidate edit. Advancing
                # the reconciled generation here keeps the direct
                # candidate -> Yes -> Finalize path while No and Maybe still
                # require a new agent pass.
                session["candidateDecisionGeneration"] = session["decisionGeneration"]

        return self.public_session(self.store.update(sid, revision, operation)) or {}

    def start_revision(
        self,
        sid: str,
        revision: int | None,
        *,
        operation: str,
        agent: str | None,
    ) -> dict[str, Any]:
        if operation not in {"revise", "reconcile"}:
            raise ApiError("invalid_operation", "Invalid revision operation")
        session = self.store.get(sid)
        selected_agent = agent or str(session.get("lastAgent") or "codex")
        available = {record["id"] for record in self.runner.agents() if record.get("available")}
        if selected_agent not in available:
            raise ApiError("agent_unavailable", f"Agent is not available: {selected_agent}", status=503)
        if session.get("activeJob"):
            raise ApiError("session_busy", "A revision is already running", status=409)
        if session.get("finalized"):
            raise ApiError("session_complete", "This review is already complete", status=409)
        if not session.get("comments"):
            raise ApiError("comments_required", "Add at least one comment before revising")
        if operation == "revise" and session.get("candidate"):
            raise ApiError("candidate_exists", "Use reconciliation for an existing candidate", status=409)
        if operation == "reconcile":
            if not session.get("candidate"):
                raise ApiError("candidate_required", "There is no candidate to reconcile", status=409)
            for comment in session.get("comments", []):
                if self._comment_is_closed(comment):
                    continue
                if comment.get("needsDecision") is True:
                    decision = comment.get("decision")
                    if decision not in {"yes", "no", "maybe"}:
                        raise ApiError("decisions_required", "Decide every revised comment first", status=409)
                    if decision == "maybe" and not str(comment.get("note") or "").strip():
                        raise ApiError("feedback_required", "Maybe requires feedback", status=409)
            has_unresolved_comments = any(
                not self._comment_is_closed(item)
                and (item.get("status") == "queued" or item.get("decision") in {"no", "maybe"})
                for item in session.get("comments", [])
            )
            generations_stale = (
                session.get("candidateCommentGeneration") != session.get("commentGeneration")
                or session.get("candidateDecisionGeneration") != session.get("decisionGeneration")
            )
            if not has_unresolved_comments and not generations_stale:
                raise ApiError("nothing_to_reconcile", "There are no unresolved comments", status=409)

        job_id = SessionStore.new_id("j")
        public = {
            "id": job_id,
            "sessionId": sid,
            "status": "queued",
            "message": "Preparing the revision...",
            "operation": operation,
            "agent": selected_agent,
            "createdAt": now(),
            "lastActivityAt": now(),
            "timeoutSeconds": int(self.runner.timeout),
        }
        job = dict(public)
        job["cancel"] = threading.Event()
        with self._jobs_lock:
            self.jobs[job_id] = job

        def mark_active(stored: dict[str, Any]) -> None:
            if stored.get("activeJob"):
                raise ApiError("session_busy", "A revision is already running", status=409)
            stored["activeJob"] = {
                "id": job_id,
                "status": "queued",
                "operation": operation,
                "ownerPid": os.getpid(),
            }
            stored["lastAgent"] = selected_agent

        try:
            updated = self.store.update(sid, revision, mark_active)
        except Exception:
            with self._jobs_lock:
                self.jobs.pop(job_id, None)
            raise
        thread = threading.Thread(
            target=self._run_job,
            args=(job_id, updated, operation, selected_agent),
            daemon=True,
            name=f"learning-hub-{job_id[:12]}",
        )
        thread.start()
        return {"job": self.public_job(job)}

    def _run_job(self, job_id: str, session: dict[str, Any], operation: str, agent: str) -> None:
        self._set_job(
            job_id,
            status="running",
            message="The agent is revising the blueprint...",
            lastActivityAt=now(),
        )
        with self._jobs_lock:
            cancel = self.jobs[job_id]["cancel"]
        try:
            result = self.runner.run(
                session,
                operation=operation,
                agent=agent,
                cancel=cancel,
                on_activity=lambda message: self._set_job(
                    job_id,
                    message=message,
                    lastActivityAt=now(),
                ),
            )
            if cancel.is_set():
                raise Cancelled()

            def apply_result(stored: dict[str, Any]) -> None:
                stored["candidate"] = result["candidate"]
                stored["candidateCommentGeneration"] = int(stored.get("commentGeneration", 0))
                stored["candidateDecisionGeneration"] = int(stored.get("decisionGeneration", 0))
                mappings = {
                    str(item.get("commentId") or item.get("id")): item
                    for item in result.get("comments", [])
                    if isinstance(item, dict) and (item.get("commentId") or item.get("id"))
                }
                for comment in stored.get("comments", []):
                    if self._comment_is_closed(comment):
                        continue
                    cid = str(comment.get("id"))
                    was_active = operation == "revise" or comment.get("status") == "queued" or comment.get("decision") in {"no", "maybe"}
                    if not was_active:
                        if operation == "reconcile" and comment.get("decision") == "yes":
                            mapped, target, targets = self._candidate_mapping(
                                mappings.get(cid),
                                comment.get("originalAnchor") or comment.get("anchor"),
                            )
                            comment["candidateAnchor"] = target
                            comment["candidateTargets"] = targets
                            if mapped:
                                comment["anchor"] = target
                            comment["mapped"] = mapped
                            comment["changed"] = mapped
                        comment["needsDecision"] = False
                        continue
                    mapped, target, targets = self._candidate_mapping(
                        mappings.get(cid),
                        comment.get("originalAnchor") or comment.get("anchor"),
                    )
                    comment["candidateAnchor"] = target
                    comment["candidateTargets"] = targets
                    if mapped:
                        comment["anchor"] = target
                    comment["mapped"] = mapped
                    if operation == "reconcile" and comment.get("decision") == "no" and mapped:
                        comment["status"] = "closed"
                        comment["closed"] = True
                        comment["needsDecision"] = False
                    else:
                        comment["status"] = "review"
                        comment["decision"] = None
                        comment["note"] = ""
                        comment["needsDecision"] = True
                    comment["changed"] = mapped
                stored.setdefault("rounds", []).append(
                    {
                        "id": SessionStore.new_id("r"),
                        "operation": operation,
                        "agent": agent,
                        "summary": result.get("summary", ""),
                        "completedAt": now(),
                    }
                )

            applied = self.store.update_job(session["id"], job_id, apply_result)
            if applied is None:
                raise Cancelled()
            self._set_job(job_id, status="completed", message="Candidate revision is ready.", progress=1.0)
        except Cancelled:
            self.store.update_job(session["id"], job_id, lambda _stored: None)
            self._set_job(job_id, status="cancelled", message="Revision cancelled.", progress=1.0)
        except (AgentError, RepositoryError, StoreError, ApiError) as exc:
            self.store.update_job(session["id"], job_id, lambda _stored: None)
            self._set_job(
                job_id,
                status="failed",
                message=getattr(exc, "message", str(exc)),
                error=getattr(exc, "message", str(exc)),
                code=getattr(exc, "code", "revision_failed"),
                progress=1.0,
            )
        except Exception:
            traceback.print_exc()
            self.store.update_job(session["id"], job_id, lambda _stored: None)
            self._set_job(
                job_id,
                status="failed",
                message="The local revision job failed.",
                error="The local revision job failed.",
                code="revision_failed",
                progress=1.0,
            )

    def _set_job(self, job_id: str, **values: Any) -> None:
        with self._jobs_lock:
            job = self.jobs.get(job_id)
            if job:
                job.update(values)
                job["updatedAt"] = now()

    @staticmethod
    def public_job(job: dict[str, Any]) -> dict[str, Any]:
        return {key: deepcopy(value) for key, value in job.items() if key != "cancel"}

    def get_job(self, job_id: str) -> dict[str, Any]:
        with self._jobs_lock:
            job = self.jobs.get(job_id)
            if not job:
                raise ApiError("job_not_found", "Revision job not found", status=404)
            return self.public_job(job)

    def cancel_job(self, job_id: str) -> dict[str, Any]:
        with self._jobs_lock:
            job = self.jobs.get(job_id)
            if not job:
                raise ApiError("job_not_found", "Revision job not found", status=404)
            if job.get("status") in {"completed", "failed", "cancelled"}:
                return self.public_job(job)
            job["cancel"].set()
            job.update(status="cancelling", message="Cancelling the revision...", updatedAt=now())
            return self.public_job(job)

    def finalize(self, sid: str, revision: int | None) -> dict[str, Any]:
        result_holder: dict[str, Any] = {}

        def finalize_locked(stored: dict[str, Any]) -> None:
            # SessionStore.update holds its lock across this callback. Every
            # comment, decision, and revision mutation uses that same lock, so
            # none can invalidate the checked revision after repository filing.
            public = self.public_session(stored) or {}
            if not public.get("canFinalize"):
                raise ApiError("review_incomplete", "Every comment must be approved and reconciled before finalizing", status=409)
            decisions = [
                f"{item.get('id')}: {str(item.get('body') or '')[:180]}"
                for item in stored.get("comments", [])
                if item.get("decision") == "yes"
            ]
            result = self.repository.finalize(
                stored,
                str(stored.get("candidate") or ""),
                log_message=f"{Path(stored['path']).stem} - approved {len(decisions)} review note(s)",
                commit_details=decisions,
            )
            stored["finalized"] = {**result, "completedAt": now()}
            result_holder.update(result)

        finalized = self.store.update(sid, revision, finalize_locked)
        payload = self.public_session(finalized) or {}
        payload["finalizeResult"] = result_holder
        return payload


def _bridge_script(view: str, nonce: str) -> str:
    """Return the fixed iframe annotation bridge. No document data is interpolated."""
    bridge = """<style id="lh-bridge-style">
[data-lh-block-key]{scroll-margin-top:24px}.diagram{position:relative}.lh-review-mark{background:#fff4a8;color:inherit;border-bottom:1px solid #c8102e;padding:0}.lh-review-mark.is-active{border-bottom-width:2px}.diagram.lh-review-diagram{outline:2px solid #c8102e;outline-offset:3px}.lh-diagram-review-button{position:absolute;z-index:8;top:8px;right:8px;min-width:44px;min-height:44px;border:1px solid #1a1a1a;background:#fdfdfb;color:#c8102e;font:600 10px 'JetBrains Mono',monospace;cursor:pointer}.lh-review-pin{position:absolute;z-index:7;top:8px;left:8px;width:24px;height:24px;display:grid;place-items:center;border-radius:50%;background:#c8102e;color:white;font:600 10px 'JetBrains Mono',monospace}
</style>
<script nonce="__LH_REVIEW_NONCE__">
(function(){
'use strict';
var SOURCE='learning-hub-review';
var page=document.querySelector('.page')||document.body;
var currentView=__LH_REVIEW_VIEW__;
function post(type,payload){parent.postMessage(Object.assign({source:SOURCE,type:type},payload||{}),'*');}
function sectionName(node){var section=node.closest&&node.closest('section[id]');return section?section.id:'document';}
function typeName(node){if(node.classList.contains('diagram'))return'diagram';if(node.classList.contains('formula'))return'formula';if(node.classList.contains('key'))return'key';return node.tagName.toLowerCase();}
function indexBlocks(){
 var counts={};
 page.querySelectorAll('header,.legend,.toc,section h2,section h3,section p,section li,.diagram,.formula,.where,.key,.cross-refs,footer').forEach(function(node){
  if(node.closest('[data-lh-review-ui]'))return;
  var scope=sectionName(node),type=typeName(node),stem=scope+'/'+type,key=stem+'/'+((counts[stem]||0)+1);counts[stem]=(counts[stem]||0)+1;node.dataset.lhBlockKey=key;
 });
}
function closestBlock(node){return node&&node.nodeType===3?node.parentElement.closest('[data-lh-block-key]'):node&&node.closest&&node.closest('[data-lh-block-key]');}
function textOffset(block,node,offset){var range=document.createRange();range.selectNodeContents(block);try{range.setEnd(node,offset);}catch(_error){return 0;}return range.toString().length;}
function selectionPayload(){
 var selection=getSelection();if(!selection||selection.isCollapsed||!selection.rangeCount)return{collapsed:true};
 var range=selection.getRangeAt(0),start=closestBlock(range.startContainer),end=closestBlock(range.endContainer);
 if(!start||!page.contains(start)||!end||!page.contains(end))return{collapsed:true};
 var exact=selection.toString();if(!exact.trim())return{collapsed:true};
 if(start!==end){var multiRect=range.getBoundingClientRect();return{collapsed:false,text:exact,quote:exact,unsupported:true,rect:{left:multiRect.left,right:multiRect.right,top:multiRect.top,bottom:multiRect.bottom},anchor:{kind:'text',quote:exact,sectionId:sectionName(start),selector:{blockKey:start.dataset.lhBlockKey,endBlockKey:end.dataset.lhBlockKey,multiBlock:true,exact:exact},versionId:currentView}};}
 var startOffset=textOffset(start,range.startContainer,range.startOffset),endOffset=start===end?textOffset(start,range.endContainer,range.endOffset):startOffset+exact.length;
 var all=start.textContent||'',rect=range.getBoundingClientRect();
 return{collapsed:false,text:exact,quote:exact,rect:{left:rect.left,right:rect.right,top:rect.top,bottom:rect.bottom},anchor:{kind:'text',quote:exact,sectionId:sectionName(start),selector:{blockKey:start.dataset.lhBlockKey,start:startOffset,end:endOffset,exact:exact,prefix:all.slice(Math.max(0,startOffset-32),startOffset),suffix:all.slice(endOffset,endOffset+32)},versionId:currentView}};
}
function announceSelection(){post('selection',selectionPayload());}
function diagramAnchor(diagram){var caption=diagram.querySelector('.caption'),rect=diagram.getBoundingClientRect();return{kind:'diagram',quote:(caption&&caption.textContent.trim())||('Diagram in '+sectionName(diagram)),diagramId:diagram.dataset.lhBlockKey,sectionId:sectionName(diagram),selector:{blockKey:diagram.dataset.lhBlockKey},versionId:currentView,rect:{left:rect.left,right:rect.right,top:rect.top,bottom:rect.bottom}};}
function addDiagramButtons(){page.querySelectorAll('.diagram').forEach(function(diagram){if(diagram.querySelector(':scope > .lh-diagram-review-button'))return;var button=document.createElement('button');button.type='button';button.className='lh-diagram-review-button';button.dataset.lhReviewUi='true';button.dataset.testid='diagram-comment';button.textContent='+ Comment';button.setAttribute('aria-label','Comment on '+diagramAnchor(diagram).quote);button.addEventListener('click',function(event){event.preventDefault();event.stopPropagation();post('diagram',{diagram:diagramAnchor(diagram),action:'activate'});});diagram.append(button);});}
function clearHighlights(){document.querySelectorAll('mark.lh-review-mark').forEach(function(mark){mark.replaceWith.apply(mark,Array.from(mark.childNodes));});document.querySelectorAll('.lh-review-diagram').forEach(function(node){node.classList.remove('lh-review-diagram');node.querySelectorAll(':scope > .lh-review-pin').forEach(function(pin){pin.remove();});});}
function targetBlock(anchor){var selector=anchor&&anchor.selector||{},key=selector.blockKey||anchor.diagramId;if(key){try{return page.querySelector('[data-lh-block-key="'+CSS.escape(key)+'"]');}catch(_error){return null;}}if(anchor&&anchor.sectionId)return document.getElementById(anchor.sectionId);return null;}
function textNodes(block){var walker=document.createTreeWalker(block,NodeFilter.SHOW_TEXT,{acceptNode:function(node){return node.parentElement.closest('[data-lh-review-ui],script,style')?NodeFilter.FILTER_REJECT:NodeFilter.FILTER_ACCEPT;}}),nodes=[],node;while((node=walker.nextNode()))nodes.push(node);return nodes;}
function anchorStart(joined,selector,exact){if(Number.isInteger(selector.start)&&Number.isInteger(selector.end)){return selector.end>=selector.start&&joined.slice(selector.start,selector.end)===exact?selector.start:-1;}var prefix=selector.prefix,suffix=selector.suffix;if(prefix!==undefined||suffix!==undefined){var position=-1;while((position=joined.indexOf(exact,position+1))>=0){var before=joined.slice(0,position),after=joined.slice(position+exact.length);if((prefix===undefined||before.endsWith(prefix))&&(suffix===undefined||after.startsWith(suffix)))return position;}return-1;}return joined.indexOf(exact);}
function markText(block,anchor,id,targetIndex){var selector=anchor&&anchor.selector||{},exact=selector.exact||anchor.quote||'';if(!block||!exact)return null;var nodes=textNodes(block),joined=nodes.map(function(node){return node.data;}).join(''),start=anchorStart(joined,selector,exact);if(start<0)return null;var end=start+exact.length,cursor=0,first=null,last=null,so=0,eo=0;nodes.forEach(function(node){var next=cursor+node.data.length;if(first===null&&start>=cursor&&start<=next){first=node;so=start-cursor;}if(last===null&&end>=cursor&&end<=next){last=node;eo=end-cursor;}cursor=next;});if(!first||!last)return null;var range=document.createRange();range.setStart(first,so);range.setEnd(last,eo);var mark=document.createElement('mark');mark.className='lh-review-mark';mark.dataset.commentId=id;mark.dataset.targetIndex=String(targetIndex);mark.tabIndex=0;try{range.surroundContents(mark);}catch(_error){var fragment=range.extractContents();mark.append(fragment);range.insertNode(mark);}mark.addEventListener('click',function(){post('highlight',{commentId:id,targetIndex:targetIndex});});return mark;}
function renderHighlights(comments){clearHighlights();(comments||[]).forEach(function(item,index){if(item.mapped===false)return;var anchor=item.anchor||{},block=targetBlock(anchor),number=Number(item.number)||index+1,targetIndex=Number.isInteger(Number(item.targetIndex))?Number(item.targetIndex):0;if(anchor.kind==='diagram'){if(!block)return;block.classList.add('lh-review-diagram');var pin=document.createElement('button');pin.type='button';pin.className='lh-review-pin';pin.dataset.lhReviewUi='true';pin.dataset.commentId=item.commentId;pin.dataset.targetIndex=String(targetIndex);pin.textContent=String(number);pin.setAttribute('aria-label','Open comment '+String(number));pin.addEventListener('click',function(event){event.stopPropagation();post('highlight',{commentId:item.commentId,targetIndex:targetIndex});});block.append(pin);return;}markText(block,anchor,item.commentId,targetIndex);});}
function target(data){if(data.fragment){var fragment=document.getElementById(String(data.fragment).replace(/^#/,''));if(fragment)fragment.scrollIntoView({block:'start'});return;}var anchor=data.anchor||data,block=targetBlock(anchor),targetIndex=Number(data.targetIndex),markSelector=data.commentId?'mark[data-comment-id="'+CSS.escape(String(data.commentId))+'"]':'',mark=Number.isInteger(targetIndex)&&targetIndex>=0&&markSelector?document.querySelector(markSelector+'[data-target-index="'+String(targetIndex)+'"]'):markSelector&&document.querySelector(markSelector);var node=mark||block;if(node){document.querySelectorAll('.lh-review-mark.is-active').forEach(function(item){item.classList.remove('is-active');});if(mark)mark.classList.add('is-active');node.scrollIntoView({block:'center',behavior:matchMedia('(prefers-reduced-motion: reduce)').matches?'auto':'smooth'});}}
indexBlocks();addDiagramButtons();
document.addEventListener('selectionchange',function(){setTimeout(announceSelection,0);});
page.addEventListener('click',function(event){var diagram=event.target.closest&&event.target.closest('.diagram');if(diagram&&!event.target.closest('a,button,input,textarea,select')&&getSelection().isCollapsed)post('diagram',{diagram:diagramAnchor(diagram),action:'activate'});var link=event.target.closest&&event.target.closest('a[href]');if(link){event.preventDefault();post('internal-link',{href:link.href});}});
addEventListener('message',function(event){if(event.source!==parent||!event.data||event.data.source!==SOURCE)return;var data=event.data;if(data.type==='highlights')renderHighlights(data.comments);else if(data.type==='target')target(data);});
if(typeof ResizeObserver==='function')new ResizeObserver(function(){post('height',{height:document.documentElement.scrollHeight});}).observe(document.body);
post('height',{height:document.documentElement.scrollHeight});post('ready',{view:currentView});
}());
</script>"""
    return bridge.replace("__LH_REVIEW_NONCE__", nonce, 1).replace(
        "__LH_REVIEW_VIEW__",
        json.dumps(view),
        1,
    )


def _trust_original_scripts(content: str, original: str, nonce: str) -> str:
    """Authorize only script blocks already present in the trusted source snapshot."""
    original_locator = _DocumentLocator(original)
    trusted = Counter(original[start:end] for start, end in original_locator.scripts)
    locator = _DocumentLocator(content)
    edits: list[tuple[int, int, str]] = []
    for start, end in locator.scripts:
        block = content[start:end]
        if trusted[block] < 1:
            continue
        trusted[block] -= 1
        opening_end = content.find(">", start, end)
        if opening_end < 0:
            continue
        opening_end += 1
        opening = content[start:opening_end]
        nonced = re.sub(
            r"^<script\b",
            f'<script nonce="{nonce}"',
            opening,
            count=1,
            flags=re.IGNORECASE,
        )
        edits.append((start, opening_end, nonced))
    return _apply_document_edits(content, edits)


def _apply_document_edits(content: str, edits: list[tuple[int, int, str]]) -> str:
    for start, end, replacement in sorted(edits, key=lambda item: item[0], reverse=True):
        content = content[:start] + replacement + content[end:]
    return content


def inject_document(content: str, *, original: str, view: str, nonce: str) -> bytes:
    content = _trust_original_scripts(content, original, nonce)
    locator = _DocumentLocator(content)
    base = '<base href="/topics/">\n'
    bridge = _bridge_script(view, nonce)
    edits: list[tuple[int, int, str]] = []
    if locator.head_end is not None:
        edits.append((locator.head_end, locator.head_end, "\n" + base))
    else:
        edits.append((0, 0, base))
    if locator.html_tag is not None:
        start, end = locator.html_tag
        tag = content[start:end]
        tag = re.sub(
            r"\sdata-lh-review-view(?:\s*=\s*(?:\"[^\"]*\"|'[^']*'|[^\s>]+))?",
            "",
            tag,
            flags=re.IGNORECASE,
        )
        replacement = tag[:-1] + f' data-lh-review-view="{view}">'
        edits.append((start, end, replacement))
    if locator.body_end is not None:
        edits.append((locator.body_end, locator.body_end, bridge + "\n"))
    else:
        edits.append((len(content), len(content), bridge))
    content = _apply_document_edits(content, edits)
    return content.encode("utf-8")


def shell_html(path: str, token: str, *, test_same_origin: bool = False) -> bytes:
    bootstrap = json.dumps(
        {"path": path, "token": token, "bootstrapUrl": "/api/v1/bootstrap"},
        ensure_ascii=False,
    ).replace("<", "\\u003c")
    title = Path(path).stem.replace("-", " ").title()
    sandbox = "allow-scripts allow-same-origin" if test_same_origin else "allow-scripts"
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title} | Learning Hub review</title>
<link rel="stylesheet" href="/assets/review.css">
</head>
<body>
<div id="lh-review-root">
  <iframe id="lh-blueprint-frame" sandbox="{sandbox}" title="Blueprint under review"></iframe>
</div>
<script type="application/json" id="lh-review-bootstrap">{bootstrap}</script>
<script src="/assets/review.js"></script>
</body>
</html>
""".encode("utf-8")


class ReviewRequestHandler(BaseHTTPRequestHandler):
    server_version = "LearningHubReview/1"
    protocol_version = "HTTP/1.1"

    @property
    def app(self) -> ReviewApplication:
        return self.server.app  # type: ignore[attr-defined]

    def log_message(self, format: str, *args: Any) -> None:
        if getattr(self.server, "quiet", False):  # type: ignore[attr-defined]
            return
        super().log_message(format, *args)

    def do_GET(self) -> None:
        self._dispatch("GET")

    def do_POST(self) -> None:
        self._dispatch("POST")

    def do_PATCH(self) -> None:
        self._dispatch("PATCH")

    def do_PUT(self) -> None:
        self._dispatch("PUT")

    def do_DELETE(self) -> None:
        self._dispatch("DELETE")

    def _dispatch(self, method: str) -> None:
        try:
            self._assert_loopback_host()
            split = urlsplit(self.path)
            path = split.path
            query = parse_qs(split.query, keep_blank_values=True)
            if method in {"POST", "PATCH", "PUT", "DELETE"}:
                self._authorize_mutation()
            if method == "GET" and path == "/api/v1/bootstrap":
                return self._json(200, self.app.bootstrap(self._one(query, "path")))
            if method == "POST" and path == "/api/v1/sessions":
                return self._json(201, self.app.create_session(self._json_body().get("path")))

            match = _SESSION_ROUTE.fullmatch(path)
            if method == "GET" and match:
                return self._json(200, self.app.public_session(self.app.store.get(match.group(1))))
            match = _DOCUMENT_ROUTE.fullmatch(path)
            if method == "GET" and match:
                session = self.app.store.get(match.group(1))
                view = self._one(query, "view")
                if view not in {"original", "candidate"}:
                    raise ApiError("invalid_view", "Document view must be original or candidate")
                content = session.get(view)
                if view == "candidate" and not content:
                    raise ApiError("candidate_unavailable", "Candidate is not available", status=404)
                nonce = secrets.token_urlsafe(24)
                return self._bytes(
                    200,
                    inject_document(
                        str(content),
                        original=str(session.get("original") or ""),
                        view=view,
                        nonce=nonce,
                    ),
                    "text/html; charset=utf-8",
                    api=True,
                    document_nonce=nonce,
                )
            match = _COMMENTS_ROUTE.fullmatch(path)
            if method == "POST" and match:
                return self._json(201, self.app.add_comment(match.group(1), self._revision(), self._json_body()))
            match = _COMMENT_ROUTE.fullmatch(path)
            if method == "PATCH" and match:
                return self._json(200, self.app.update_comment(match.group(1), match.group(2), self._revision(), self._json_body()))
            if method == "DELETE" and match:
                return self._json(200, self.app.delete_comment(match.group(1), match.group(2), self._revision()))
            match = _REVISIONS_ROUTE.fullmatch(path)
            if method == "POST" and match:
                payload = self._json_body()
                return self._json(202, self.app.start_revision(match.group(1), self._revision(), operation="revise", agent=payload.get("agent")))
            match = _DECISION_ROUTE.fullmatch(path)
            if method == "PUT" and match:
                return self._json(200, self.app.decide(match.group(1), match.group(2), self._revision(), self._json_body()))
            match = _RECONCILE_ROUTE.fullmatch(path)
            if method == "POST" and match:
                self._json_body()
                return self._json(202, self.app.start_revision(match.group(1), self._revision(), operation="reconcile", agent=None))
            match = _FINALIZE_ROUTE.fullmatch(path)
            if method == "POST" and match:
                self._json_body()
                return self._json(200, self.app.finalize(match.group(1), self._revision()))
            match = _JOB_ROUTE.fullmatch(path)
            if method == "GET" and match:
                return self._json(200, self.app.get_job(match.group(1)))
            if method == "DELETE" and match:
                return self._json(200, self.app.cancel_job(match.group(1)))

            if method != "GET":
                raise ApiError("method_not_allowed", "Method not allowed", status=405)
            if path == "/favicon.ico":
                return self._bytes(204, b"", "image/x-icon")
            if re.fullmatch(r"/topics/[^/]+\.html", path):
                logical = path.lstrip("/")
                normalized, _text, _digest = self.app.repository.read_blueprint(logical)
                return self._bytes(
                    200,
                    shell_html(normalized, self.app.token, test_same_origin=self.app.test_same_origin),
                    "text/html; charset=utf-8",
                )
            data, file = self.app.repository.read_public_file(path)
            mime = mimetypes.guess_type(file.name)[0] or "application/octet-stream"
            return self._bytes(
                200,
                data,
                mime
                + (
                    "; charset=utf-8"
                    if mime.startswith("text/") or mime in {"application/javascript", "application/json"}
                    else ""
                ),
                cors=True,
            )
        except (ApiError, RepositoryError, StoreError, AgentError) as exc:
            details = getattr(exc, "details", None)
            payload: dict[str, Any] = {"error": getattr(exc, "code", "request_failed"), "message": getattr(exc, "message", str(exc))}
            if details:
                payload["details"] = details
            self._json(getattr(exc, "status", 422), payload)
        except (BrokenPipeError, ConnectionResetError):
            return
        except Exception:
            traceback.print_exc()
            self._json(500, {"error": "internal_error", "message": "The local review server failed."})

    @staticmethod
    def _one(query: dict[str, list[str]], name: str) -> str:
        values = query.get(name)
        if not values or len(values) != 1 or not values[0]:
            raise ApiError("invalid_request", f"Query parameter is required: {name}")
        return values[0]

    def _json_body(self) -> dict[str, Any]:
        length_text = self.headers.get("Content-Length")
        try:
            length = int(length_text or "0")
        except ValueError as exc:
            raise ApiError("invalid_request", "Content-Length is invalid", status=400) from exc
        if length <= 0 or length > MAX_REQUEST_BYTES:
            raise ApiError("invalid_request", "JSON request body is missing or too large", status=400)
        if "application/json" not in (self.headers.get("Content-Type") or "").lower():
            raise ApiError("invalid_request", "Content-Type must be application/json", status=415)
        try:
            value = json.loads(self.rfile.read(length).decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ApiError("invalid_request", "Request body is not valid JSON", status=400) from exc
        if not isinstance(value, dict):
            raise ApiError("invalid_request", "JSON request body must be an object", status=400)
        return value

    def _revision(self) -> int | None:
        value = self.headers.get("If-Match")
        if value is None:
            return None
        value = value.strip().strip('"')
        try:
            parsed = int(value)
        except ValueError as exc:
            raise ApiError("invalid_revision", "If-Match must be a numeric session revision", status=400) from exc
        return parsed

    def _assert_loopback_host(self) -> None:
        host = self.headers.get("Host", "")
        parsed = urlsplit(f"//{host}")
        hostname = (parsed.hostname or "").lower()
        try:
            port = parsed.port
        except ValueError as exc:
            raise ApiError("invalid_host", "This service accepts its own loopback origin only", status=421) from exc
        if hostname not in {"127.0.0.1", "localhost", "::1"} or port != self.server.server_port:
            raise ApiError("invalid_host", "This service accepts loopback hosts only", status=421)

    def _authorize_mutation(self) -> None:
        supplied = self.headers.get("X-Learning-Hub-Token", "")
        if not secrets.compare_digest(supplied, self.app.token):
            raise ApiError("forbidden", "The local review token is missing or invalid", status=403)
        origin = self.headers.get("Origin")
        if origin:
            parsed = urlsplit(origin)
            hostname = (parsed.hostname or "").lower()
            try:
                port = parsed.port
            except ValueError as exc:
                raise ApiError("forbidden", "Cross-origin review requests are not allowed", status=403) from exc
            if (
                parsed.scheme != "http"
                or hostname not in {"127.0.0.1", "localhost", "::1"}
                or port != self.server.server_port
            ):
                raise ApiError("forbidden", "Cross-origin review requests are not allowed", status=403)

    def _json(self, status: int, payload: Any) -> None:
        data = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        self._bytes(status, data, "application/json; charset=utf-8", api=True)

    def _bytes(
        self,
        status: int,
        data: bytes,
        content_type: str,
        *,
        api: bool = False,
        cors: bool = False,
        document_nonce: str | None = None,
    ) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        if cors:
            self.send_header("Access-Control-Allow-Origin", "*")
        if document_nonce:
            self.send_header(
                "Content-Security-Policy",
                document_csp(self.server.server_port, document_nonce),
            )
            self.send_header("X-DNS-Prefetch-Control", "off")
        if api:
            self.send_header("Cache-Control", "no-store")
        else:
            self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        self.wfile.write(data)


class ReviewHTTPServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, address: tuple[str, int], app: ReviewApplication, *, quiet: bool = False):
        self.app = app
        self.quiet = quiet
        try:
            super().__init__(address, ReviewRequestHandler)
        except Exception:
            app.repository.release_server_lock()
            raise

    def handle_error(self, request: Any, client_address: Any) -> None:
        error = sys.exc_info()[1]
        if isinstance(error, (BrokenPipeError, ConnectionResetError)):
            return
        super().handle_error(request, client_address)

    def server_close(self) -> None:
        try:
            super().server_close()
        finally:
            self.app.repository.release_server_lock()
