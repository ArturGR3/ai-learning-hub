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

require_text README.md "[setup guide](SETUP.md)"
require_text README.md "read SETUP.md and follow it"

require_text AGENTS.md "For first-time setup, read and follow [SETUP.md](SETUP.md)"

require_text SETUP.md "gh repo clone YOUR_ACCOUNT/YOUR_HUB_NAME"
require_text SETUP.md "gh repo create YOUR_HUB_NAME"
require_text SETUP.md "./scripts/install-skill.sh --yes"
require_text SETUP.md "python3 scripts/validate.py"
require_text SETUP.md "one question at a time"

echo "setup contract tests passed"
