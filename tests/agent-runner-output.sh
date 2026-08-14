#!/usr/bin/env bash

set -Eeuo pipefail

REPO_ROOT="$(cd -P "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)"
TEST_ROOT="$(mktemp -d "${TMPDIR:-/tmp}/learning-hub-agent-output.XXXXXX")"

cleanup() {
  rm -rf "$TEST_ROOT"
}
trap cleanup EXIT

cp -R "$REPO_ROOT/." "$TEST_ROOT/repo"
rm -rf "$TEST_ROOT/repo/.git"
(
  cd "$TEST_ROOT/repo"
  git init -q -b main
  git config user.name "Learning Hub Test"
  git config user.email "test@localhost"
  git add .
  git commit -qm "Agent output fixture"
)

export LEARNING_HUB_AGENT_COMMAND="python3 $TEST_ROOT/repo/tests/fixtures/fake-agent.py"
export LEARNING_HUB_FAKE_SEQUENCE="$TEST_ROOT/repo/tests/fixtures/review-sequence.json"
export LEARNING_HUB_FAKE_VERBOSE_BYTES=1048576

python3 - "$TEST_ROOT/repo" <<'PY'
from pathlib import Path
import sys
import threading

root = Path(sys.argv[1])
sys.path.insert(0, str(root / "scripts"))

from review_server.agent_runner import AgentRunner
from review_server.repository import Repository

repository = Repository(root)
path, original, digest = repository.read_blueprint("topics/example-blueprint.html")
session = {
    "id": "output-regression",
    "path": path,
    "baseHead": repository.head(),
    "baseBranch": repository.branch(),
    "sourceHash": digest,
    "original": original,
    "candidate": None,
    "comments": [
        {
            "id": "output-comment",
            "body": "Clarify that this estimate is one possible result.",
            "anchor": {
                "kind": "text",
                "sectionId": "s02",
                "quote": "the thing you are holding is not really a number",
            },
        }
    ],
    "decisions": {},
    "rounds": [],
}
activities = []
result = AgentRunner(repository, timeout=10).run(
    session,
    operation="revise",
    agent="codex",
    cancel=threading.Event(),
    on_activity=activities.append,
)
assert result["candidate"]
print("agent runner large-output regression passed")
PY
