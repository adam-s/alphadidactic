#!/usr/bin/env bash
# SubagentStop hook — clean up after agent completes.
set -euo pipefail

# Kill any experiment processes this agent may have spawned
pkill -f "build_dataset.py" 2>/dev/null || true
pkill -f "run_strategy.py" 2>/dev/null || true
pkill -f "verify_integrity.py" 2>/dev/null || true

# Clean APC channels
python -m shared.agent_protocol clean 2>/dev/null || true

# Remove .claude/worktrees/ — Claude Code creates these inside the repo
# even when our WorktreeCreate hook redirects to /tmp/. They leak information.
rm -rf .claude/worktrees/ 2>/dev/null || true

# Prune git worktree references
git worktree prune 2>/dev/null || true
