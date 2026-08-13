#!/usr/bin/env bash

set -Eeuo pipefail

REPO_ROOT="$(cd -P "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)"

require_text() {
  local file="$1"
  local expected="$2"

  if ! grep -Fq -- "$expected" "$REPO_ROOT/$file"; then
    echo "FAIL: $file does not contain: $expected" >&2
    exit 1
  fi
}

reject_text() {
  local file="$1"
  local unexpected="$2"

  if grep -Fq -- "$unexpected" "$REPO_ROOT/$file"; then
    echo "FAIL: $file still contains: $unexpected" >&2
    exit 1
  fi
}

require_text skills/learn/SKILL.md "explain how they currently understand the topic"
require_text skills/learn/SKILL.md "voice input"
require_text skills/learn/SKILL.md "one at a time"
require_text skills/learn/SKILL.md "summarize the starting point and gaps"
require_text AGENTS.md "Use them by default"

reject_text AGENTS.md "Who the user is"
reject_text SETUP.md "Personalize how the hub teaches you"
reject_text README.md "personalizing the teaching"

echo "teaching contract tests passed"
