#!/usr/bin/env bash

set -Eeuo pipefail

REPO_ROOT="$(cd -P "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)"
TEST_ROOT="$(mktemp -d "${TMPDIR:-/tmp}/learning-hub-review-e2e.XXXXXX")"
BROWSER_ARTIFACTS_DIR="${BROWSER_ARTIFACTS_DIR:-$(mktemp -d "${TMPDIR:-/tmp}/learning-hub-review-artifacts.XXXXXX")}"
export BROWSER_ARTIFACTS_DIR
source "$REPO_ROOT/tests/helpers/browser.sh"

WORK_REPO="$TEST_ROOT/repo"
BARE_REMOTE="$TEST_ROOT/remote.git"
SERVER_LOG="$BROWSER_ARTIFACTS_DIR/server.log"
AGENT_LOG="$BROWSER_ARTIFACTS_DIR/fake-agent.jsonl"
SERVER_PID=""

cleanup() {
  browser_stop
  if [[ -n "$SERVER_PID" ]]; then
    kill "$SERVER_PID" >/dev/null 2>&1 || true
    wait "$SERVER_PID" >/dev/null 2>&1 || true
  fi
  rm -rf "$TEST_ROOT"
}
trap cleanup EXIT

fail() {
  echo "review E2E failed: $*" >&2
  browser_capture_diagnostics || true
  echo "artifacts: $BROWSER_ARTIFACTS_DIR" >&2
  exit 1
}

if [[ ! -f "$REPO_ROOT/scripts/review-blueprint.py" ]]; then
  fail "scripts/review-blueprint.py is not available"
fi

mkdir -p "$WORK_REPO"
cp -R "$REPO_ROOT/." "$WORK_REPO"
rm -rf "$WORK_REPO/.git"
# AgentRunner requires its instruction file. The fake agent ignores this
# throwaway prompt; keeping it out of the source tree avoids adding test-only
# behavior to the production agent instructions.
printf '%s\n' 'Read .review/context.json and write .review/result.json.' \
  >"$WORK_REPO/scripts/review_server/review_prompt.md"
(
  cd "$WORK_REPO"
  git init -q -b main
  git config user.name "Learning Hub Test"
  git config user.email "test@localhost"
  git add .
  git commit -qm "E2E baseline"
)
git init -q --bare "$BARE_REMOTE"
git -C "$WORK_REPO" remote add origin "$BARE_REMOTE"
git -C "$WORK_REPO" push -q -u origin main

BASE_HEAD="$(git -C "$WORK_REPO" rev-parse HEAD)"
ORIGINAL_HASH="$(shasum -a 256 "$WORK_REPO/topics/example-blueprint.html" | awk '{print $1}')"
PORT="$(python3 - <<'PY'
import socket
with socket.socket() as sock:
    sock.bind(("127.0.0.1", 0))
    print(sock.getsockname()[1])
PY
)"

export LEARNING_HUB_AGENT_COMMAND="python3 $WORK_REPO/tests/fixtures/fake-agent.py"
export LEARNING_HUB_FAKE_SEQUENCE="$WORK_REPO/tests/fixtures/review-sequence.json"
export LEARNING_HUB_FAKE_AGENT_LOG="$AGENT_LOG"
export LEARNING_HUB_TEST_SAME_ORIGIN=1
python3 "$WORK_REPO/scripts/review-blueprint.py" --repo "$WORK_REPO" --port "$PORT" >"$SERVER_LOG" 2>&1 &
SERVER_PID=$!

for _attempt in {1..80}; do
  if curl -fsS "http://127.0.0.1:$PORT/" >/dev/null 2>&1; then
    break
  fi
  if ! kill -0 "$SERVER_PID" >/dev/null 2>&1; then
    fail "review server exited during startup"
  fi
  sleep 0.25
done
curl -fsS "http://127.0.0.1:$PORT/" >/dev/null || fail "review server did not become ready"

browser_open "http://localhost:$PORT/topics/example-blueprint.html"
browser_wait_js "document.querySelector('[data-testid=\"review-root\"]') && document.querySelector('#lh-blueprint-frame')" "review shell"
browser_axi resize 1440 1000 >/dev/null
browser_wait_js "document.querySelector('#lh-blueprint-frame').contentDocument && document.querySelector('#lh-blueprint-frame').contentDocument.querySelector('#s02 > p')" "blueprint iframe"
browser_screenshot "01-empty-review"

browser_do "const frame = document.querySelector('#lh-blueprint-frame'); const doc = frame.contentDocument; const paragraph = doc.querySelector('#s02 > p'); const range = doc.createRange(); range.selectNodeContents(paragraph); const selection = doc.getSelection(); selection.removeAllRanges(); selection.addRange(range); doc.dispatchEvent(new MouseEvent('mouseup', {bubbles:true, view:frame.contentWindow}))"
browser_wait_js "!document.querySelector('[data-testid=\"selection-comment\"]').hidden" "text selection action"
browser_click_testid "selection-comment"
browser_wait_js "document.querySelector('[data-testid=\"comment-editor\"]')" "text composer"
browser_fill_testid "comment-editor" "Clarify that this is one possible estimate, not a fixed truth."
browser_do "document.querySelector('[data-testid=\"comment-editor\"]').dispatchEvent(new KeyboardEvent('keydown', {key:'Enter', metaKey:true, bubbles:true}))"
browser_wait_js "document.querySelectorAll('[data-testid=\"comment-card\"]').length === 1" "persisted text comment"

browser_do "const frame = document.querySelector('#lh-blueprint-frame'); const doc = frame.contentDocument; doc.getSelection().removeAllRanges(); const diagramButton = doc.querySelector('[data-testid=\"diagram-comment\"]'); const diagram = doc.querySelector('#s03 .diagram'); (diagramButton || diagram).click()"
browser_wait_js "document.querySelector('[data-testid=\"comment-editor\"]')" "diagram composer"
browser_fill_testid "comment-editor" "Make the square-root trade-off explicit in this diagram."
browser_do "document.querySelector('[data-testid=\"comment-editor\"]').dispatchEvent(new KeyboardEvent('keydown', {key:'Enter', ctrlKey:true, bubbles:true}))"
browser_wait_js "document.querySelectorAll('[data-testid=\"comment-card\"]').length === 2" "persisted diagram comment"
browser_screenshot "02-two-comments"

browser_do "location.reload()"
browser_wait_js "document.querySelectorAll('[data-testid=\"comment-card\"]').length === 2" "queue restored after reload"

browser_axi resize 900 900 >/dev/null
browser_do "const root = document.documentElement; if (root.scrollWidth > root.clientWidth) throw new Error('900px horizontal overflow')"
browser_screenshot "02a-off-canvas"
browser_axi resize 390 844 >/dev/null
browser_do "document.querySelector('[data-action=\"toggle-rail\"]').click()"
browser_do "const root = document.documentElement; if (root.scrollWidth > root.clientWidth) throw new Error('390px horizontal overflow')"
browser_screenshot "02b-mobile-rail"
browser_axi resize 1440 1000 >/dev/null

[[ "$(shasum -a 256 "$WORK_REPO/topics/example-blueprint.html" | awk '{print $1}')" == "$ORIGINAL_HASH" ]] || fail "blueprint changed before revision"
[[ -z "$(git -C "$WORK_REPO" status --porcelain)" ]] || fail "worktree changed before finalization"

browser_click_testid "submit-comments"
browser_wait_js "document.querySelector('[data-testid=\"candidate\"]:not(:disabled)')" "first candidate" 160
browser_wait_js "document.querySelector('#lh-blueprint-frame').contentDocument.querySelector('meta[name=\"review-fixture-round\"][content=\"1\"]')" "first candidate document" 160
browser_wait_js "document.querySelectorAll('[data-testid=\"comment-card\"] fieldset').length === 2" "first decisions"
browser_wait_js "document.querySelector('[data-action=\"toggle-changes\"]')" "change comparison"
browser_do "document.querySelector('[data-action=\"toggle-changes\"]').click()"
browser_wait_js "document.querySelector('.lh-change-panel.is-before') && document.querySelector('.lh-change-panel.is-after') && document.querySelector('[data-action=\"jump-change\"]')" "expanded change comparison"
browser_screenshot "03-first-candidate"

COMMENT_IDS="$(python3 - "$AGENT_LOG" <<'PY'
import json
from pathlib import Path
import sys

record = json.loads(Path(sys.argv[1]).read_text().splitlines()[0])
print(",".join(record["commentIds"]))
PY
)"
IFS=',' read -r TEXT_ID DIAGRAM_ID <<<"$COMMENT_IDS"
[[ -n "$TEXT_ID" && -n "$DIAGRAM_ID" && "$TEXT_ID" != "$DIAGRAM_ID" ]] || fail "could not read comment IDs"

browser_click_decision "$TEXT_ID" "yes"
browser_wait_js "document.querySelector('[data-decision=\"$DIAGRAM_ID\"] input[value=\"maybe\"]')" "diagram decision"
browser_click_decision "$DIAGRAM_ID" "maybe"
browser_wait_js "document.querySelector('[data-action=\"decision-note\"][data-comment-id=\"$DIAGRAM_ID\"]')" "Maybe feedback"
browser_do "const node = document.querySelector('[data-action=\"decision-note\"][data-comment-id=\"$DIAGRAM_ID\"]'); node.value = 'Show the four-times-sample to half-error relationship directly.'; node.dispatchEvent(new Event('input', {bubbles:true}))"
browser_do "document.querySelector('[data-action=\"save-maybe\"][data-comment-id=\"$DIAGRAM_ID\"]').click()"
browser_wait_js "!document.querySelector('[data-testid=\"submit-comments\"]').disabled" "second revision enabled"
browser_click_testid "submit-comments"
browser_wait_js "document.querySelector('#lh-blueprint-frame').contentDocument.querySelector('meta[name=\"review-fixture-round\"][content=\"2\"]')" "second candidate" 160
browser_wait_js "document.querySelector('[data-decision=\"$DIAGRAM_ID\"] input[value=\"no\"]')" "second diagram decision" 160

browser_click_decision "$DIAGRAM_ID" "no"
browser_wait_js "!document.querySelector('[data-testid=\"submit-comments\"]').disabled" "reconciliation enabled"
browser_click_testid "submit-comments"
browser_wait_js "document.querySelector('#lh-blueprint-frame').contentDocument.querySelector('meta[name=\"review-fixture-round\"][content=\"3\"]')" "final candidate document" 160
browser_wait_js "document.querySelector('[data-testid=\"approve-final\"]') && !document.querySelector('[data-testid=\"approve-final\"]').disabled" "final candidate" 160
browser_screenshot "04-ready-to-finalize"

[[ "$(shasum -a 256 "$WORK_REPO/topics/example-blueprint.html" | awk '{print $1}')" == "$ORIGINAL_HASH" ]] || fail "blueprint changed before final approval"
[[ "$(git -C "$WORK_REPO" rev-parse HEAD)" == "$BASE_HEAD" ]] || fail "commit created before final approval"

browser_click_testid "approve-final"
browser_axi dialog accept >/dev/null
browser_wait_js "document.querySelector('[data-testid=\"review-complete\"]') || document.readyState === 'complete'" "review finalization" 160

for _attempt in {1..80}; do
  [[ "$(git -C "$WORK_REPO" rev-parse HEAD)" != "$BASE_HEAD" ]] && break
  sleep 0.25
done
FINAL_HEAD="$(git -C "$WORK_REPO" rev-parse HEAD)"
[[ "$FINAL_HEAD" != "$BASE_HEAD" ]] || fail "finalize did not create a commit"
[[ "$(git -C "$WORK_REPO" rev-list --count "$BASE_HEAD..$FINAL_HEAD")" == "1" ]] || fail "finalize created more than one commit"
[[ "$(git --git-dir="$BARE_REMOTE" rev-parse refs/heads/main)" == "$FINAL_HEAD" ]] || fail "finalized commit was not pushed"
[[ "$(shasum -a 256 "$WORK_REPO/topics/example-blueprint.html" | awk '{print $1}')" != "$ORIGINAL_HASH" ]] || fail "finalized blueprint stayed unchanged"
[[ "$(wc -l < "$AGENT_LOG" | tr -d ' ')" == "3" ]] || fail "expected three fake-agent runs"
python3 - "$AGENT_LOG" "$TEXT_ID" "$DIAGRAM_ID" <<'PY'
import json
from pathlib import Path
import sys

records = [json.loads(line) for line in Path(sys.argv[1]).read_text().splitlines()]
assert [record["operation"] for record in records] == ["revise", "reconcile", "reconcile"]
assert sys.argv[2] in records[0]["commentIds"]
assert sys.argv[3] in records[0]["commentIds"]
assert records[1]["decisions"]
assert records[2]["decisions"]
PY

browser_do "const root = document.documentElement; if (root.scrollWidth > root.clientWidth) throw new Error('horizontal overflow: ' + root.scrollWidth + ' > ' + root.clientWidth)"
browser_assert_clean_diagnostics || fail "browser diagnostics are not clean"

echo "review E2E passed"
echo "artifacts: $BROWSER_ARTIFACTS_DIR"
