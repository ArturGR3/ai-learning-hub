#!/usr/bin/env python3
"""Deterministic validation coverage for review agent results."""

from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "scripts"))

from review_server.agent_runner import AgentError, AgentRunner  # noqa: E402


CANDIDATE = """<!DOCTYPE html>
<html lang="en">
<head><title>Test</title></head>
<body>
<div class="page">
  <section id="s01">
    <h2>A test section</h2>
    <p>Alpha <b>revised</b> fact.</p>
    <div class="diagram">
      <svg viewBox="0 0 10 10"></svg>
      <div class="caption">Flow caption</div>
    </div>
  </section>
</div>
</body>
</html>
"""


def text_mapping(comment_id: str, exact: str = "revised fact") -> dict[str, object]:
    return {
        "commentId": comment_id,
        "status": "mapped",
        "targets": [
            {
                "kind": "text",
                "sectionId": "s01",
                "quote": exact,
                "selector": {
                    "blockKey": "s01/p/1",
                    "start": 6,
                    "end": 18,
                    "exact": exact,
                    "prefix": "Alpha ",
                    "suffix": ".",
                },
            }
        ],
    }


def diagram_mapping(comment_id: str) -> dict[str, object]:
    return {
        "commentId": comment_id,
        "status": "mapped",
        "targets": [
            {
                "kind": "diagram",
                "sectionId": "s01",
                "diagramId": "s01/diagram/1",
                "quote": "Flow caption",
                "selector": {"blockKey": "s01/diagram/1"},
            }
        ],
    }


class CommentMappingValidationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.session = {
            "comments": [
                {"id": "text-comment", "status": "queued", "decision": None},
                {"id": "diagram-comment", "status": "queued", "decision": None},
            ]
        }

    def test_accepts_complete_mappings_with_candidate_evidence(self) -> None:
        text = text_mapping("text-comment")
        text["targets"].append(
            {
                "kind": "text",
                "sectionId": "s01",
                "quote": "A test section",
                "selector": {"blockKey": "s01/h2/1", "exact": "A test section"},
            }
        )
        result = AgentRunner._validate_comment_mappings(
            self.session,
            "revise",
            CANDIDATE,
            [text, diagram_mapping("diagram-comment")],
        )

        self.assertEqual([mapping["status"] for mapping in result], ["mapped", "mapped"])
        self.assertEqual(len(result[0]["targets"]), 2)

    def test_rejects_missing_active_mapping(self) -> None:
        with self.assertRaisesRegex(AgentError, "missing mappings.*diagram-comment"):
            AgentRunner._validate_comment_mappings(
                self.session,
                "revise",
                CANDIDATE,
                [text_mapping("text-comment")],
            )

    def test_rejects_unknown_status(self) -> None:
        invalid = diagram_mapping("diagram-comment")
        invalid["status"] = "complete"
        with self.assertRaisesRegex(AgentError, "must be mapped or unmapped"):
            AgentRunner._validate_comment_mappings(
                self.session,
                "revise",
                CANDIDATE,
                [text_mapping("text-comment"), invalid],
            )

    def test_normalizes_unverifiable_mapping_to_unmapped(self) -> None:
        result = AgentRunner._validate_comment_mappings(
            self.session,
            "revise",
            CANDIDATE,
            [text_mapping("text-comment", "stale quote"), diagram_mapping("diagram-comment")],
        )

        self.assertEqual(
            result[0],
            {"commentId": "text-comment", "status": "unmapped", "targets": []},
        )
        self.assertEqual(result[1]["status"], "mapped")

    def test_normalizes_top_level_exact_for_the_browser_highlight(self) -> None:
        mapping = text_mapping("text-comment")
        target = mapping["targets"][0]
        target.pop("quote")
        target["exact"] = target["selector"].pop("exact")

        result = AgentRunner._validate_comment_mappings(
            self.session,
            "revise",
            CANDIDATE,
            [mapping, diagram_mapping("diagram-comment")],
        )

        normalized = result[0]["targets"][0]
        self.assertEqual(normalized["quote"], "revised fact")
        self.assertEqual(normalized["selector"]["exact"], "revised fact")

    def test_normalizes_multi_target_mapping_when_any_target_is_unverifiable(self) -> None:
        text = text_mapping("text-comment")
        text["targets"].append(
            {
                "kind": "text",
                "sectionId": "s01",
                "quote": "Missing second treatment",
                "selector": {"blockKey": "s01/h2/1", "exact": "Missing second treatment"},
            }
        )

        result = AgentRunner._validate_comment_mappings(
            self.session,
            "revise",
            CANDIDATE,
            [text, diagram_mapping("diagram-comment")],
        )

        self.assertEqual(result[0]["status"], "unmapped")

    def test_reconcile_revalidates_accepted_mapping_when_agent_omits_it(self) -> None:
        accepted_target = text_mapping("accepted")["targets"][0]
        session = {
            "comments": [
                {
                    "id": "accepted",
                    "status": "review",
                    "decision": "yes",
                    "mapped": True,
                    "candidateAnchor": accepted_target,
                    "candidateTargets": [accepted_target],
                },
                {"id": "active", "status": "review", "decision": "maybe"},
            ]
        }
        candidate = CANDIDATE.replace("<b>revised</b> fact", "<b>replacement</b> fact")

        result = AgentRunner._validate_comment_mappings(
            session,
            "reconcile",
            candidate,
            [{"commentId": "active", "status": "unmapped", "targets": []}],
        )

        self.assertEqual([mapping["commentId"] for mapping in result], ["active", "accepted"])
        self.assertEqual(result[1], {"commentId": "accepted", "status": "unmapped", "targets": []})

        preserved = AgentRunner._validate_comment_mappings(
            session,
            "reconcile",
            CANDIDATE,
            [{"commentId": "active", "status": "unmapped", "targets": []}],
        )
        self.assertEqual(preserved[1]["status"], "mapped")

    def test_reconcile_accepts_optional_updated_mapping_for_accepted_comment(self) -> None:
        session = {
            "comments": [
                {"id": "accepted", "status": "review", "decision": "yes"},
                {"id": "active", "status": "review", "decision": "maybe"},
            ]
        }

        result = AgentRunner._validate_comment_mappings(
            session,
            "reconcile",
            CANDIDATE,
            [text_mapping("active"), text_mapping("accepted")],
        )

        by_id = {mapping["commentId"]: mapping for mapping in result}
        self.assertEqual(by_id["accepted"]["status"], "mapped")


class CandidateIdentityTests(unittest.TestCase):
    def test_rejects_json_candidate_that_differs_from_edited_blueprint(self) -> None:
        with tempfile.TemporaryDirectory(prefix="agent-runner-validation-") as temporary:
            root = Path(temporary)
            blueprint = root / "topics" / "test.html"
            result_file = root / ".review" / "result.json"
            blueprint.parent.mkdir(parents=True)
            result_file.parent.mkdir()
            baseline = CANDIDATE.replace("revised", "original")
            blueprint.write_text(baseline, encoding="utf-8")
            subprocess.run(["git", "init", "-q", "-b", "main"], cwd=root, check=True)
            subprocess.run(["git", "config", "user.name", "Test"], cwd=root, check=True)
            subprocess.run(["git", "config", "user.email", "test@localhost"], cwd=root, check=True)
            subprocess.run(["git", "add", "topics/test.html"], cwd=root, check=True)
            subprocess.run(["git", "commit", "-qm", "Baseline"], cwd=root, check=True)
            blueprint.write_text(CANDIDATE, encoding="utf-8")
            different = CANDIDATE.replace("revised", "different")
            result_file.write_text(
                json.dumps(
                    {
                        "candidate": different,
                        "comments": [{"commentId": "c1", "status": "unmapped", "targets": []}],
                    }
                ),
                encoding="utf-8",
            )
            session = {
                "path": "topics/test.html",
                "original": baseline,
                "candidate": None,
                "comments": [{"id": "c1", "status": "queued", "decision": None}],
            }

            runner = object.__new__(AgentRunner)
            with self.assertRaisesRegex(AgentError, "does not exactly match"):
                runner._validate_result(root, session, "revise", result_file, blueprint)


if __name__ == "__main__":
    unittest.main()
