#!/usr/bin/env bash
# Install the hub's learn skill for Claude Code, Codex, and OpenCode.
#
# Usage: ./scripts/install-skill.sh [--yes]
#
# The repository remains the single source. The installer creates two links:
#
#   ~/.agents/skills/learn  -> <this repo>/skills/learn  (Codex + OpenCode)
#   ~/.claude/skills/learn  -> <this repo>/skills/learn  (Claude Code)
#
# Existing entries are moved to timestamped backups. If installation fails,
# the script removes links it created and restores entries it moved.

set -Eeuo pipefail

resolve_directory() (
  cd -P "$1" 2>/dev/null
  pwd -P
)

points_to_source() {
  local target="$1"
  local resolved

  [ -L "$target" ] || return 1
  resolved="$(resolve_directory "$target")" || return 1
  [ "$resolved" = "$SOURCE" ]
}

next_backup_path() {
  local client="$1"
  local candidate="$BACKUP_ROOT/$client/learn-$BACKUP_STAMP"
  local suffix=1

  while [ -e "$candidate" ] || [ -L "$candidate" ]; do
    candidate="$BACKUP_ROOT/$client/learn-${BACKUP_STAMP}-${suffix}"
    suffix=$((suffix + 1))
  done

  printf '%s\n' "$candidate"
}

rollback() {
  local exit_code="${1:-1}"
  local index
  local target

  trap - ERR INT TERM
  set +e
  echo ""
  echo "  Installation failed. Restoring the previous setup."

  for ((index = ${#CREATED_TARGETS[@]} - 1; index >= 0; index--)); do
    target="${CREATED_TARGETS[$index]}"
    if [ -L "$target" ]; then
      unlink "$target"
    fi
  done

  for ((index = ${#MOVED_TARGETS[@]} - 1; index >= 0; index--)); do
    target="${MOVED_TARGETS[$index]}"
    if { [ -e "${MOVED_BACKUPS[$index]}" ] || [ -L "${MOVED_BACKUPS[$index]}" ]; } &&
       [ ! -e "$target" ] && [ ! -L "$target" ]; then
      mv "${MOVED_BACKUPS[$index]}" "$target"
    fi
  done

  exit "$exit_code"
}

ASSUME_YES=false
case "${1:-}" in
  "") ;;
  --yes|-y) ASSUME_YES=true ;;
  *)
    echo "Usage: $0 [--yes]" >&2
    exit 2
    ;;
esac

if [ "$#" -gt 1 ]; then
  echo "Usage: $0 [--yes]" >&2
  exit 2
fi

REPO_ROOT="$(cd -P "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)"
SOURCE="$(resolve_directory "$REPO_ROOT/skills/learn")"
INSTALL_HOME="${AI_LEARNING_HUB_HOME:-$HOME}"
BACKUP_ROOT="$INSTALL_HOME/.ai-learning-hub/backups/skills"
TARGETS=(
  "$INSTALL_HOME/.agents/skills/learn"
  "$INSTALL_HOME/.claude/skills/learn"
)
BACKUP_CLIENTS=(
  "agents"
  "claude"
)
LABELS=(
  "Codex and OpenCode"
  "Claude Code"
)
BACKUPS=("" "")
CREATED_TARGETS=()
MOVED_TARGETS=()
MOVED_BACKUPS=()
BACKUP_STAMP="$(date '+%Y%m%d-%H%M%S')"
NEEDS_CHANGES=false

if [ ! -f "$SOURCE/SKILL.md" ]; then
  echo "ERROR: $SOURCE/SKILL.md not found." >&2
  exit 1
fi

for ((index = 0; index < ${#TARGETS[@]}; index++)); do
  target="${TARGETS[$index]}"
  if points_to_source "$target"; then
    continue
  fi

  NEEDS_CHANGES=true
  if [ -e "$target" ] || [ -L "$target" ]; then
    BACKUPS[$index]="$(next_backup_path "${BACKUP_CLIENTS[$index]}")"
  fi
done

if [ "$NEEDS_CHANGES" = false ]; then
  echo "Already installed for Claude Code, Codex, and OpenCode:"
  echo "  ${TARGETS[0]} -> $SOURCE"
  echo "  ${TARGETS[1]} -> $SOURCE"
  exit 0
fi

echo ""
echo "  What this installs"
echo "  ------------------"
echo "  The learn skill for Claude Code, Codex, and OpenCode. It lets an agent"
echo "  teach a topic or capture a work session as a blueprint in this hub."
echo ""
echo "  What it changes on your machine"
echo "  -------------------------------"

for ((index = 0; index < ${#TARGETS[@]}; index++)); do
  target="${TARGETS[$index]}"
  echo "  ${LABELS[$index]}:"
  if points_to_source "$target"; then
    echo "    Already linked: $target"
  elif [ -n "${BACKUPS[$index]}" ]; then
    echo "    Preserve:       $target"
    echo "      as:           ${BACKUPS[$index]}"
    echo "    Create link:    $target"
    echo "      ->            $SOURCE"
  else
    echo "    Create link:    $target"
    echo "      ->            $SOURCE"
  fi
  echo ""
done

echo "  No settings are edited and nothing is downloaded."
echo ""

if [ "$ASSUME_YES" = false ]; then
  printf "  Install? [y/N] "
  if ! { read -r REPLY </dev/tty; } 2>/dev/null; then
    read -r REPLY || REPLY=""
  fi
  case "$REPLY" in
    y|Y|yes|YES) ;;
    *) echo "  Cancelled. Nothing was changed."; exit 0 ;;
  esac
fi

trap 'rollback $?' ERR
trap 'rollback 130' INT
trap 'rollback 143' TERM

for ((index = 0; index < ${#TARGETS[@]}; index++)); do
  target="${TARGETS[$index]}"
  if points_to_source "$target"; then
    continue
  fi

  mkdir -p "$(dirname "$target")"
  if [ -n "${BACKUPS[$index]}" ]; then
    mkdir -p "$(dirname "${BACKUPS[$index]}")"
    mv "$target" "${BACKUPS[$index]}"
    MOVED_TARGETS+=("$target")
    MOVED_BACKUPS+=("${BACKUPS[$index]}")
  fi

  ln -s "$SOURCE" "$target"
  CREATED_TARGETS+=("$target")
done

trap - ERR INT TERM

echo ""
echo "  Installed for Claude Code, Codex, and OpenCode."
echo "  Start a new client session if the learn skill does not appear immediately."
echo ""
echo "  Claude Code: /learn"
echo '  Codex:      $learn'
echo "  OpenCode:   ask it to use the learn skill"
echo ""
