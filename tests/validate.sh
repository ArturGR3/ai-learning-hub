#!/usr/bin/env bash

set -Eeuo pipefail

REPO_ROOT="$(cd -P "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)"
TEST_ROOT="$(mktemp -d "${TMPDIR:-/tmp}/ai-learning-hub-validate.XXXXXX")"

cleanup() {
  rm -rf "$TEST_ROOT"
}
trap cleanup EXIT

mkdir -p "$TEST_ROOT/scripts" "$TEST_ROOT/topics" "$TEST_ROOT/assets"
cp "$REPO_ROOT/scripts/validate.py" "$TEST_ROOT/scripts/validate.py"
cp "$REPO_ROOT/topics/example-blueprint.html" "$TEST_ROOT/topics/example-blueprint.html"
cp "$REPO_ROOT/index.html" "$TEST_ROOT/index.html"
cp "$REPO_ROOT/assets/blueprint.css" "$TEST_ROOT/assets/blueprint.css"

output="$(cd "$TEST_ROOT" && python3 scripts/validate.py)"
if [[ "$output" == *"hub owner is not configured"* ]]; then
  echo "did not expect an owner-configuration warning" >&2
  exit 1
fi

if [[ "$output" != *"PASSED"* ]]; then
  echo "expected validation to pass" >&2
  exit 1
fi

echo "validate tests passed"
