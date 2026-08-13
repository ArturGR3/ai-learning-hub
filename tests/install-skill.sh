#!/usr/bin/env bash

set -euo pipefail

REPO_ROOT="$(cd -P "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)"
INSTALLER="$REPO_ROOT/scripts/install-skill.sh"
SOURCE="$(cd -P "$REPO_ROOT/skills/learn" && pwd -P)"
TEST_ROOT="$(mktemp -d "${TMPDIR:-/tmp}/ai-learning-hub-install.XXXXXX")"

cleanup() {
  chmod -R u+w "$TEST_ROOT" 2>/dev/null || true
  rm -rf "$TEST_ROOT"
}

fail() {
  echo "FAIL: $*" >&2
  exit 1
}

assert_linked_to_source() {
  local target="$1"
  local resolved

  [ -L "$target" ] || fail "$target is not a symlink"
  resolved="$(cd -P "$target" && pwd -P)"
  [ "$resolved" = "$SOURCE" ] || fail "$target resolves to $resolved"
}

trap cleanup EXIT

fresh_home="$TEST_ROOT/fresh-home"
mkdir -p "$fresh_home"
AI_LEARNING_HUB_HOME="$fresh_home" "$INSTALLER" --yes >/dev/null
assert_linked_to_source "$fresh_home/.agents/skills/learn"
assert_linked_to_source "$fresh_home/.claude/skills/learn"

second_output="$(AI_LEARNING_HUB_HOME="$fresh_home" "$INSTALLER" --yes)"
case "$second_output" in
  *"Already installed for Claude Code, Codex, and OpenCode"*) ;;
  *) fail "second install was not reported as already installed" ;;
esac

conflict_home="$TEST_ROOT/conflict-home"
mkdir -p "$conflict_home/.agents/skills/learn" "$conflict_home/.claude/skills" "$conflict_home/old-skill"
printf 'keep me\n' >"$conflict_home/.agents/skills/learn/marker"
printf 'old skill\n' >"$conflict_home/old-skill/SKILL.md"
ln -s "$conflict_home/old-skill" "$conflict_home/.claude/skills/learn"

AI_LEARNING_HUB_HOME="$conflict_home" "$INSTALLER" --yes >/dev/null
assert_linked_to_source "$conflict_home/.agents/skills/learn"
assert_linked_to_source "$conflict_home/.claude/skills/learn"

agents_backup="$(find "$conflict_home/.ai-learning-hub/backups/skills/agents" -maxdepth 1 -name 'learn-*' -print -quit)"
claude_backup="$(find "$conflict_home/.ai-learning-hub/backups/skills/claude" -maxdepth 1 -name 'learn-*' -print -quit)"
[ -f "$agents_backup/marker" ] || fail "existing Codex/OpenCode skill was not preserved"
[ -L "$claude_backup" ] || fail "existing Claude Code skill link was not preserved"
[ "$(readlink "$claude_backup")" = "$conflict_home/old-skill" ] || fail "Claude Code backup changed target"

if find "$conflict_home/.agents/skills" "$conflict_home/.claude/skills" -mindepth 2 -name SKILL.md -print -quit | grep -q .; then
  fail "a backup remains inside a client skill discovery directory"
fi

rollback_home="$TEST_ROOT/rollback-home"
mkdir -p "$rollback_home/.agents/skills/learn" "$rollback_home/.claude/skills"
printf 'restore me\n' >"$rollback_home/.agents/skills/learn/marker"
chmod u-w "$rollback_home/.claude/skills"

if AI_LEARNING_HUB_HOME="$rollback_home" "$INSTALLER" --yes >/dev/null 2>&1; then
  fail "installer succeeded despite an unwritable target directory"
fi

[ -f "$rollback_home/.agents/skills/learn/marker" ] || fail "rollback did not restore existing skill"
[ ! -L "$rollback_home/.agents/skills/learn" ] || fail "rollback left the new Codex/OpenCode link"

echo "install-skill tests passed"
