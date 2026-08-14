#!/usr/bin/env bash

set -Eeuo pipefail

REPO_ROOT="$(cd -P "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)"

if [[ "${LEARNING_HUB_RUN_CODEX_SMOKE:-0}" != "1" ]]; then
  echo "codex smoke skipped (set LEARNING_HUB_RUN_CODEX_SMOKE=1 to run one paid agent invocation)"
  exit 0
fi

if ! command -v codex >/dev/null 2>&1; then
  echo "codex smoke requested, but codex is not installed" >&2
  exit 1
fi

SMOKE_ROOT="$(mktemp -d "${TMPDIR:-/tmp}/learning-hub-codex-smoke.XXXXXX")"
cleanup() {
  rm -rf "$SMOKE_ROOT"
}
trap cleanup EXIT

cp -R "$REPO_ROOT/." "$SMOKE_ROOT/repo"
rm -rf "$SMOKE_ROOT/repo/.git"
printf '%s\n' 'Read .review/context.json. Improve only the requested blueprint and write .review/result.json.' \
  >"$SMOKE_ROOT/repo/scripts/review_server/review_prompt.md"
(
  cd "$SMOKE_ROOT/repo"
  git init -q -b main
  git config user.name "Learning Hub Test"
  git config user.email "test@localhost"
  git add .
  git commit -qm "Test fixture"
)

python3 - "$SMOKE_ROOT/repo" <<'PY'
from pathlib import Path
import threading
import sys

root = Path(sys.argv[1])
sys.path.insert(0, str(root))
from scripts.review_server.agent_runner import AgentRunner
from scripts.review_server.repository import Repository

repository = Repository(root)
path, original, digest = repository.read_blueprint("topics/example-blueprint.html")
session = {
    "id": "codex-smoke",
    "path": path,
    "baseHead": repository.head(),
    "baseBranch": repository.branch(),
    "sourceHash": digest,
    "original": original,
    "candidate": None,
    "comments": [{
        "id": "smoke-comment",
        "body": "Clarify that one sample mean is only one possible result.",
        "anchor": {"kind": "text", "sectionId": "s02", "quote": "the thing you are holding is not really a number"},
    }],
    "decisions": {},
    "rounds": [],
}
result = AgentRunner(repository).run(session, operation="revise", agent="codex", cancel=threading.Event())
assert "<html" in result["candidate"].lower()
assert isinstance(result["comments"], list)
print("codex smoke passed with one agent invocation")
PY
