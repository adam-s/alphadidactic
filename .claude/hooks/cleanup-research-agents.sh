#!/usr/bin/env bash
# Cleanup script for research experiment agents
# Kills lingering Python processes and cleans up git worktrees

set -euo pipefail

echo "=== Research Agent Cleanup ==="

# Kill Python experiment processes
echo "Killing lingering experiment processes..."
pkill -f "run_strategy.py" 2>/dev/null && echo "  Killed run_strategy.py processes" || echo "  No run_strategy.py processes found"
pkill -f "build_dataset.py" 2>/dev/null && echo "  Killed build_dataset.py processes" || echo "  No build_dataset.py processes found"
pkill -f "verify_integrity.py" 2>/dev/null && echo "  Killed verify_integrity.py processes" || echo "  No verify_integrity.py processes found"

# Clean up worktrees in /tmp/
echo "Cleaning up worktrees..."
git worktree list 2>/dev/null | grep '/tmp/' | awk '{print $1}' | while read -r wt; do
    echo "  Removing worktree: $wt"
    git worktree remove --force "$wt" 2>/dev/null || echo "  Warning: could not remove $wt"
done

# Prune stale worktree references
echo "Pruning stale worktree references..."
git worktree prune 2>/dev/null && echo "  Pruned successfully" || echo "  Warning: prune failed"

echo "=== Cleanup complete ==="
