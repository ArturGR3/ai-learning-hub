#!/usr/bin/env bash
# file-blueprint.sh — validate, build, log, commit, and push a new or edited blueprint.
#
# Usage: ./scripts/file-blueprint.sh topics/<slug>.html "<log-message>" [ingest|refine|lint]
#
# What this script does:
#   1. Validates the blueprint against conventions (validate.py)
#   2. Rebuilds index.html locally (build-index.py)
#   3. Appends log.md with the provided message
#   4. Stages the blueprint, log.md, any other modified topics/assets, and the
#      rebuilt index.html — index.html is generated but committed, so a fresh
#      clone opens with no tooling installed
#   5. Commits with a matching message style ("Add" for new files, "Refine" for edits)
#   6. Pushes, if this clone has a remote to push to
#
# Everything runs locally. There is no CI: the repo is complete as committed.

set -euo pipefail

if [ $# -lt 2 ]; then
  echo "Usage: ./scripts/file-blueprint.sh topics/<slug>.html \"<log-message>\" [ingest|refine|lint]"
  echo ""
  echo "Example: ./scripts/file-blueprint.sh topics/simple-regression.html \"simple regression — line-fitting through inference\""
  exit 1
fi

BLUEPRINT="$1"
LOG_MSG="$2"
LOG_TYPE="${3:-ingest}"
REPO_ROOT="$(git rev-parse --show-toplevel)"

cd "$REPO_ROOT"

# Is this a new blueprint or an edit? Must be answered before staging —
# once `git add` runs, a brand-new file is tracked and looks like an edit.
if git ls-files --error-unmatch "$BLUEPRINT" >/dev/null 2>&1; then
  VERB="Refine"
else
  VERB="Add"
fi

# 1. Validate
echo "=== Validating blueprints ==="
python3 scripts/validate.py

# 2. Rebuild the index locally
echo "=== Rebuilding index.html ==="
python3 scripts/build-index.py

# 3. Append log.md
TODAY=$(date +%Y-%m-%d)
LOG_ENTRY="## [${TODAY}] ${LOG_TYPE} | ${LOG_MSG}"

echo "" >> log.md
echo "$LOG_ENTRY" >> log.md
echo "=== Appended to log.md ==="
echo "$LOG_ENTRY"

# 4. Stage sources and the rebuilt index
git add "$BLUEPRINT" log.md index.html

# Stage any other modified blueprints (back-link edits in related topics)
git add topics/*.html 2>/dev/null || true

# Stage shared stylesheet changes (e.g. promoting inline CSS to blueprint.css)
git add assets/*.css 2>/dev/null || true

# Stage .gitignore if modified (housekeeping)
git add .gitignore 2>/dev/null || true

# 5. Commit — "Add" for new blueprints, "Refine" for edits to existing ones
BLUEPRINT_NAME=$(basename "$BLUEPRINT" .html)
git commit -m "${VERB} ${BLUEPRINT_NAME}.html - ${LOG_MSG}"

# 6. Push — a hub with no remote is still a complete hub, so a missing or
# unreachable remote is a note, not a failure. The commit is the durable part.
if ! git remote | grep -q .; then
  echo "=== No git remote configured - skipping push ==="
elif ! git push; then
  echo "=== Push failed - the commit is safe locally; push when you can ==="
fi

echo ""
echo "=== Done ==="
echo "Blueprint filed. index.html is rebuilt and committed; the hub is complete on disk."
