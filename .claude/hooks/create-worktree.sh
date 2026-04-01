#!/usr/bin/env bash
# WorktreeCreate hook — branches from LOCAL HEAD, worktree OUTSIDE repo.
#
# Two fixes over default Claude Code behavior:
# 1. Branches from local HEAD (not origin/HEAD) so unpushed instruction patches are visible
# 2. Creates worktrees OUTSIDE the repo at /tmp/ so agents can't traverse to
#    reference_experiments/ or other main-repo files (isolation by construction)
set -euo pipefail

INPUT="$(cat)"
NAME="$(printf '%s' "$INPUT" | jq -r '.name')"
CWD="$(printf '%s' "$INPUT" | jq -r '.cwd')"

WORKTREE_DIR="/tmp/claudodidact-worktrees/$NAME"
BRANCH_NAME="worktree-$NAME"

# Clean up stale worktree if exists
if [ -d "$WORKTREE_DIR" ]; then
  git -C "$CWD" worktree remove --force "$WORKTREE_DIR" 2>/dev/null || true
  rm -rf "$WORKTREE_DIR" 2>/dev/null || true
fi

# Delete stale branch if exists
git -C "$CWD" branch -D "$BRANCH_NAME" 2>/dev/null || true

# Create worktree from local HEAD (not origin/HEAD)
git -C "$CWD" worktree add -b "$BRANCH_NAME" "$WORKTREE_DIR" HEAD >&2

# Copy gitignored cache files that agents need at runtime.
# These are read-only data (FRED panel, symbol universe, density cache).
# Without them: MacroRegime can't load, get_symbols() returns fewer symbols.
CACHE_SRC="$CWD/shared/cache"
CACHE_DST="$WORKTREE_DIR/shared/cache"
if [ -d "$CACHE_SRC" ]; then
  mkdir -p "$CACHE_DST"
  cp "$CACHE_SRC"/*.parquet "$CACHE_DST/" 2>/dev/null || true
  cp "$CACHE_SRC"/*.json "$CACHE_DST/" 2>/dev/null || true
  echo "Copied shared/cache/ to worktree" >&2
fi

echo "$WORKTREE_DIR"
