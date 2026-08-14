#!/usr/bin/env python3
"""Deterministic regression tests for review server repository hardening."""

from __future__ import annotations

from copy import deepcopy
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from typing import Any
import unittest


REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_DIR = REPO_ROOT / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from review_server.http_api import ReviewApplication  # noqa: E402
from review_server.agent_runner import AgentError, AgentRunner  # noqa: E402
from review_server.repository import Repository, RepositoryError  # noqa: E402


def run(*args: str, cwd: Path) -> str:
    result = subprocess.run(
        list(args),
        cwd=cwd,
        check=False,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    if result.returncode:
        raise AssertionError(
            f"Command failed ({result.returncode}): {' '.join(args)}\n"
            f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
        )
    return result.stdout.strip()


def mapping(
    comment_id: str,
    exact: str = "The size of the pot does not change the spoonful",
) -> dict[str, Any]:
    return {
        "commentId": comment_id,
        "status": "mapped",
        "targets": [
            {
                "kind": "text",
                "sectionId": "s01",
                "quote": exact,
                "selector": {
                    "blockKey": "s01/h2/1",
                    "exact": exact,
                },
            }
        ],
    }


class ScriptedRunner:
    timeout = 5

    def __init__(self, results: list[dict[str, Any]]):
        self.results = results
        self.contexts: list[dict[str, Any]] = []

    @staticmethod
    def agents() -> list[dict[str, Any]]:
        return [{"id": "codex", "name": "Codex", "available": True}]

    def run(
        self,
        session: dict[str, Any],
        *,
        operation: str,
        agent: str,
        cancel: threading.Event,
        on_activity: Any = None,
    ) -> dict[str, Any]:
        del agent, cancel, on_activity
        self.contexts.append(AgentRunner._context(session, operation))
        result = deepcopy(self.results.pop(0))
        result["comments"] = AgentRunner._validate_comment_mappings(
            session,
            operation,
            result["candidate"],
            result["comments"],
        )
        return result


def wait_for_job(application: ReviewApplication, job_id: str) -> dict[str, Any]:
    deadline = time.monotonic() + 2
    while time.monotonic() < deadline:
        job = application.get_job(job_id)
        if job["status"] in {"completed", "failed", "cancelled"}:
            return job
        time.sleep(0.01)
    raise AssertionError(f"Review job did not finish: {job_id}")


class ReviewServerHardeningTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(prefix="learning-hub-hardening-")
        self.root = Path(self.temporary.name) / "repo"
        (self.root / "topics").mkdir(parents=True)
        (self.root / "scripts").mkdir()
        shutil.copy2(REPO_ROOT / "topics" / "example-blueprint.html", self.root / "topics")
        shutil.copy2(REPO_ROOT / "scripts" / "validate.py", self.root / "scripts")
        shutil.copy2(REPO_ROOT / "scripts" / "build-index.py", self.root / "scripts")
        (self.root / "log.md").write_text("# Learning log\n", encoding="utf-8")
        run("python3", "scripts/build-index.py", cwd=self.root)
        run("git", "init", "-q", "-b", "main", cwd=self.root)
        run("git", "config", "user.name", "Review Test", cwd=self.root)
        run("git", "config", "user.email", "review-test@localhost", cwd=self.root)
        run("git", "add", ".", cwd=self.root)
        run("git", "commit", "-qm", "baseline", cwd=self.root)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_repository_server_lock_is_exclusive_and_releasable(self) -> None:
        first = Repository(self.root)
        second = Repository(self.root)
        first.acquire_server_lock()
        try:
            with self.assertRaises(RepositoryError) as raised:
                second.acquire_server_lock()
            self.assertEqual(raised.exception.code, "review_server_running")
        finally:
            first.release_server_lock()

        second.acquire_server_lock()
        second.release_server_lock()

    def test_head_change_creates_a_fresh_session_for_unchanged_blueprint(self) -> None:
        repository = Repository(self.root)
        application = ReviewApplication(repository)
        try:
            first = application.create_session("topics/example-blueprint.html")
            first_head = first["baseHead"]
            blueprint_before = (self.root / "topics" / "example-blueprint.html").read_bytes()

            (self.root / "notes.txt").write_text("unrelated commit\n", encoding="utf-8")
            run("git", "add", "notes.txt", cwd=self.root)
            run("git", "commit", "-qm", "unrelated change", cwd=self.root)

            second = application.create_session("topics/example-blueprint.html")
            self.assertNotEqual(second["id"], first["id"])
            self.assertNotEqual(second["baseHead"], first_head)
            self.assertEqual(second["baseHead"], run("git", "rev-parse", "HEAD", cwd=self.root))
            self.assertEqual(
                (self.root / "topics" / "example-blueprint.html").read_bytes(),
                blueprint_before,
            )
            bootstrap = application.bootstrap("topics/example-blueprint.html")
            self.assertEqual(bootstrap["session"]["id"], second["id"])
        finally:
            repository.release_server_lock()

    def test_closed_no_comment_is_absent_from_later_reconcile_and_cannot_be_reapplied(self) -> None:
        repository = Repository(self.root)
        application = ReviewApplication(repository)
        try:
            created = application.create_session("topics/example-blueprint.html")
            stored = application.store.get(created["id"])
            candidate = stored["original"]
            source_anchor = mapping("unused")["targets"][0]

            def prepare(session: dict[str, Any]) -> None:
                session["candidate"] = candidate
                session["commentGeneration"] = 2
                session["candidateCommentGeneration"] = 2
                session["decisionGeneration"] = 2
                session["candidateDecisionGeneration"] = 0
                session["comments"] = [
                    {
                        "id": "cNo",
                        "body": "Remove this treatment.",
                        "anchor": deepcopy(source_anchor),
                        "originalAnchor": deepcopy(source_anchor),
                        "candidateAnchor": deepcopy(source_anchor),
                        "candidateTargets": [deepcopy(source_anchor)],
                        "mapped": True,
                        "status": "review",
                        "decision": "no",
                        "note": "",
                        "needsDecision": True,
                    },
                    {
                        "id": "cLater",
                        "body": "Clarify a separate point.",
                        "anchor": deepcopy(source_anchor),
                        "originalAnchor": deepcopy(source_anchor),
                        "candidateAnchor": deepcopy(source_anchor),
                        "candidateTargets": [deepcopy(source_anchor)],
                        "mapped": True,
                        "status": "review",
                        "decision": "maybe",
                        "note": "Keep working on this point.",
                        "needsDecision": True,
                    },
                ]
                session["decisions"] = {
                    "cNo": {"decision": "no", "note": ""},
                    "cLater": {"decision": "maybe", "note": "Keep working on this point."},
                }

            ready = application.store.update(created["id"], created["revision"], prepare)
            runner = ScriptedRunner(
                [
                    {
                        "candidate": candidate,
                        "comments": [mapping("cNo"), mapping("cLater")],
                        "summary": "Close the rejected treatment and revise the other point.",
                    },
                    {
                        "candidate": candidate,
                        "comments": [mapping("cLater")],
                        "summary": "Revise only the remaining point.",
                    },
                ]
            )
            application.runner = runner

            first = application.start_revision(
                created["id"], ready["revision"], operation="reconcile", agent="codex"
            )
            self.assertEqual(wait_for_job(application, first["job"]["id"])["status"], "completed")
            after_first = application.store.get(created["id"])
            closed = next(item for item in after_first["comments"] if item["id"] == "cNo")
            self.assertEqual(closed["status"], "closed")
            self.assertIs(closed["closed"], True)

            remaining = next(item for item in after_first["comments"] if item["id"] == "cLater")
            decided = application.decide(
                created["id"],
                "cLater",
                after_first["revision"],
                {"decision": "maybe", "note": "Clarify the separate point."},
            )
            before_second = application.store.get(created["id"])
            closed_before_second = deepcopy(
                next(item for item in before_second["comments"] if item["id"] == "cNo")
            )
            with self.assertRaisesRegex(AgentError, "inactive or unknown comment: cNo"):
                AgentRunner._validate_comment_mappings(
                    before_second,
                    "reconcile",
                    candidate,
                    [mapping("cNo"), mapping("cLater")],
                )

            second = application.start_revision(
                created["id"], decided["revision"], operation="reconcile", agent="codex"
            )
            self.assertEqual(wait_for_job(application, second["job"]["id"])["status"], "completed")
            after_second = application.store.get(created["id"])
            closed_after_second = next(item for item in after_second["comments"] if item["id"] == "cNo")
            self.assertEqual(closed_after_second, closed_before_second)
            self.assertEqual(
                [comment["id"] for comment in runner.contexts[1]["comments"]],
                ["cLater"],
            )
            self.assertNotIn("cNo", runner.contexts[1]["decisions"])
        finally:
            repository.release_server_lock()

    def test_later_reconcile_marks_a_stale_accepted_mapping_unmapped(self) -> None:
        repository = Repository(self.root)
        application = ReviewApplication(repository)
        try:
            created = application.create_session("topics/example-blueprint.html")
            stored = application.store.get(created["id"])
            old_heading = "The size of the pot does not change the spoonful"
            new_heading = "The spoonful does not depend on the size of the pot"
            candidate = stored["original"].replace(
                f"<h2>{old_heading}</h2>",
                f"<h2>{new_heading}</h2>",
                1,
            )
            accepted_anchor = mapping("unused", old_heading)["targets"][0]
            active_anchor = mapping("unused", old_heading)["targets"][0]

            def prepare(session: dict[str, Any]) -> None:
                session["candidate"] = stored["original"]
                session["commentGeneration"] = 2
                session["candidateCommentGeneration"] = 2
                session["decisionGeneration"] = 2
                session["candidateDecisionGeneration"] = 0
                session["comments"] = [
                    {
                        "id": "cYes",
                        "body": "Keep the accepted explanation.",
                        "anchor": deepcopy(accepted_anchor),
                        "originalAnchor": deepcopy(accepted_anchor),
                        "candidateAnchor": deepcopy(accepted_anchor),
                        "candidateTargets": [deepcopy(accepted_anchor)],
                        "mapped": True,
                        "status": "review",
                        "decision": "yes",
                        "note": "",
                        "needsDecision": True,
                    },
                    {
                        "id": "cLater",
                        "body": "Clarify an overlapping explanation.",
                        "anchor": deepcopy(active_anchor),
                        "originalAnchor": deepcopy(active_anchor),
                        "candidateAnchor": deepcopy(active_anchor),
                        "candidateTargets": [deepcopy(active_anchor)],
                        "mapped": True,
                        "status": "review",
                        "decision": "maybe",
                        "note": "Make the heading more direct.",
                        "needsDecision": True,
                    },
                ]
                session["decisions"] = {
                    "cYes": {"decision": "yes", "note": ""},
                    "cLater": {"decision": "maybe", "note": "Make the heading more direct."},
                }

            ready = application.store.update(created["id"], created["revision"], prepare)
            runner = ScriptedRunner(
                [
                    {
                        "candidate": candidate,
                        "comments": [mapping("cLater", new_heading)],
                        "summary": "Revise the overlapping heading.",
                    }
                ]
            )
            application.runner = runner

            started = application.start_revision(
                created["id"], ready["revision"], operation="reconcile", agent="codex"
            )
            self.assertEqual(wait_for_job(application, started["job"]["id"])["status"], "completed")
            after = application.store.get(created["id"])
            accepted = next(item for item in after["comments"] if item["id"] == "cYes")
            self.assertEqual(accepted["decision"], "yes")
            self.assertEqual(accepted["status"], "review")
            self.assertIs(accepted["mapped"], False)
            self.assertEqual(accepted["candidateTargets"], [])
            self.assertIs(accepted["needsDecision"], False)
        finally:
            repository.release_server_lock()

    def test_pending_push_lookup_keeps_failures_separate_by_branch(self) -> None:
        repository = Repository(self.root)
        application = ReviewApplication(repository)
        try:
            commit = run("git", "rev-parse", "HEAD", cwd=self.root)
            main = application.create_session("topics/example-blueprint.html")

            def mark_failed_push(label: str):
                def operation(session: dict[str, Any]) -> None:
                    session["finalized"] = {
                        "commit": commit,
                        "pushed": False,
                        "pushError": f"{label} push failed",
                    }

                return operation

            application.store.update(main["id"], main["revision"], mark_failed_push("main"))
            run("git", "checkout", "-qb", "branch-b", cwd=self.root)
            branch_b = application.create_session("topics/example-blueprint.html")
            failed_b = application.store.update(
                branch_b["id"], branch_b["revision"], mark_failed_push("branch-b")
            )
            application.store.update(failed_b["id"], failed_b["revision"], lambda _session: None)
            run("git", "checkout", "-q", "main", cwd=self.root)

            bootstrap = application.bootstrap("topics/example-blueprint.html")
            self.assertEqual(bootstrap["session"]["id"], main["id"])
            self.assertEqual(bootstrap["session"]["finalized"]["pushError"], "main push failed")
        finally:
            repository.release_server_lock()

    def test_push_failure_finalizes_the_local_commit_and_reports_error(self) -> None:
        missing_remote = Path(self.temporary.name) / "missing-remote.git"
        run("git", "remote", "add", "origin", str(missing_remote), cwd=self.root)
        repository = Repository(self.root)
        application = ReviewApplication(repository)
        try:
            created = application.create_session("topics/example-blueprint.html")
            base_head = created["baseHead"]
            stored = application.store.get(created["id"])
            candidate = stored["original"].replace(
                "Why can asking 1,000 people tell you about 300 million?",
                "How can asking 1,000 people tell you about 300 million?",
                1,
            )
            self.assertNotEqual(candidate, stored["original"])

            def make_ready(session: dict[str, object]) -> None:
                session["candidate"] = candidate
                session["commentGeneration"] = 1
                session["candidateCommentGeneration"] = 1
                session["decisionGeneration"] = 1
                session["candidateDecisionGeneration"] = 1
                session["comments"] = [
                    {
                        "id": "cApproved",
                        "body": "Use a direct question.",
                        "status": "review",
                        "decision": "yes",
                        "note": "",
                        "needsDecision": True,
                    }
                ]

            ready = application.store.update(created["id"], created["revision"], make_ready)
            response = application.finalize(created["id"], ready["revision"])
            final_head = run("git", "rev-parse", "HEAD", cwd=self.root)

            self.assertNotEqual(final_head, base_head)
            self.assertEqual(response["finalizeResult"]["commit"], final_head)
            self.assertIs(response["finalizeResult"]["pushed"], False)
            self.assertTrue(response["finalizeResult"]["pushError"])
            self.assertTrue(response["complete"])
            self.assertEqual(response["finalized"]["commit"], final_head)
            self.assertEqual(response["finalized"]["pushError"], response["finalizeResult"]["pushError"])
            self.assertEqual(run("git", "rev-list", "--count", f"{base_head}..{final_head}", cwd=self.root), "1")
            self.assertEqual(run("git", "status", "--porcelain", cwd=self.root), "")

            pending = application.bootstrap("topics/example-blueprint.html")
            self.assertEqual(pending["session"]["id"], created["id"])
            self.assertEqual(pending["session"]["finalized"]["pushError"], response["finalizeResult"]["pushError"])

            run("git", "init", "-q", "--bare", str(missing_remote), cwd=self.root)
            run("git", "push", "-qu", "origin", "main", cwd=self.root)
            after_push = application.bootstrap("topics/example-blueprint.html")
            self.assertIsNone(after_push["session"])
        finally:
            repository.release_server_lock()


if __name__ == "__main__":
    unittest.main(verbosity=2)
