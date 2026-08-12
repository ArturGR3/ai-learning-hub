#!/usr/bin/env bash
# install-skill.sh - make /learn available in every Claude Code session.
#
# Usage: ./scripts/install-skill.sh [--yes]
#
# The skill lives in this repo at skills/learn/. Claude Code looks for skills in
# ~/.claude/skills/, so this script symlinks one into the other:
#
#     ~/.claude/skills/learn  ->  <this repo>/skills/learn
#
# A symlink rather than a copy, for one reason: the skill and the conventions it
# points at are then the same checkout. Pull the repo and the skill is current.
# It also means /learn resolves this hub's path from the link itself, instead of
# searching the disk for something that looks like a hub.
#
# This is the only thing in the whole setup that writes outside the repo, so it
# says exactly what it will do and waits for a yes.

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SOURCE="$REPO_ROOT/skills/learn"
SKILLS_DIR="$HOME/.claude/skills"
TARGET="$SKILLS_DIR/learn"

ASSUME_YES=false
if [ "${1:-}" = "--yes" ] || [ "${1:-}" = "-y" ]; then
  ASSUME_YES=true
fi

if [ ! -f "$SOURCE/SKILL.md" ]; then
  echo "ERROR: $SOURCE/SKILL.md not found - run this from inside the hub repo."
  exit 1
fi

# Already installed and pointing here? Nothing to do.
if [ -L "$TARGET" ] && [ "$(readlink -f "$TARGET")" = "$(readlink -f "$SOURCE")" ]; then
  echo "Already installed: $TARGET -> $SOURCE"
  exit 0
fi

echo ""
echo "  What this installs"
echo "  ------------------"
echo "  The /learn skill, so any Claude Code session on this machine can teach a"
echo "  topic, capture what a work session taught you, and file the result here as"
echo "  a blueprint. Without it you can still do all of that - you just have to"
echo "  point the agent at this repo by hand every time."
echo ""
echo "  What it changes on your machine"
echo "  -------------------------------"
echo "  Creates one symlink:"
echo ""
echo "      $TARGET"
echo "        -> $SOURCE"
echo ""
echo "  Nothing else. No files copied, no settings edited, nothing downloaded."
echo "  To uninstall:  rm $TARGET"
echo ""

# Something is already there and it is not ours.
if [ -e "$TARGET" ] || [ -L "$TARGET" ]; then
  echo "  Heads up: $TARGET already exists."
  if [ -L "$TARGET" ]; then
    echo "  It is a symlink to: $(readlink "$TARGET")"
  else
    echo "  It is a real directory - possibly a /learn skill of your own."
  fi
  echo "  Installing replaces it. Back it up first if you want to keep it."
  echo ""
fi

if [ "$ASSUME_YES" = false ]; then
  printf "  Install? [y/N] "
  # /dev/tty first, so the prompt still works when the script is piped in.
  # Fall back to stdin, and treat "no input at all" as no.
  if ! { read -r REPLY </dev/tty; } 2>/dev/null; then
    read -r REPLY || REPLY=""
  fi
  case "$REPLY" in
    y|Y|yes|YES) ;;
    *) echo "  Cancelled. Nothing was changed."; exit 0 ;;
  esac
fi

mkdir -p "$SKILLS_DIR"
rm -rf "$TARGET"
ln -s "$SOURCE" "$TARGET"

echo ""
echo "  Installed: $TARGET -> $SOURCE"
echo "  Restart Claude Code - skills load at session start - then run /learn."
echo ""
