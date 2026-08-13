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

cat > "$TEST_ROOT/AGENTS.md" <<'EOF'
# Learning Hub

## Who the user is

> **Owner: rewrite this section first.** Replace this placeholder.

Placeholder.
EOF

output="$(cd "$TEST_ROOT" && python3 scripts/validate.py)"
if [[ "$output" != *"hub owner is not configured"* ]]; then
  echo "expected an unconfigured-hub warning" >&2
  exit 1
fi

sed -i.bak '/Owner: rewrite this section first/d' "$TEST_ROOT/AGENTS.md"
rm "$TEST_ROOT/AGENTS.md.bak"

output="$(cd "$TEST_ROOT" && python3 scripts/validate.py)"
if [[ "$output" == *"hub owner is not configured"* ]]; then
  echo "did not expect an unconfigured-hub warning" >&2
  exit 1
fi

echo "validate tests passed"
