#!/usr/bin/env bash
# PreToolUse hook — deny writes to main repo from worktrees.
set -euo pipefail

INPUT="$(cat)"
CWD="$(printf '%s' "$INPUT" | jq -r '.cwd')"
FILE_PATH="$(printf '%s' "$INPUT" | jq -r '.tool_input.file_path // empty')"

# Only apply in worktrees (external path)
if [[ "$CWD" != "/tmp/claudodidact-worktrees/"* ]]; then
  exit 0
fi

# No file path = not a file tool, allow
if [[ -z "$FILE_PATH" ]]; then
  exit 0
fi

# If path is inside the worktree cwd, allow
if [[ "$FILE_PATH" == "$CWD/"* || "$FILE_PATH" == "$CWD" ]]; then
  exit 0
fi

# If path is inside /tmp/, allow
if [[ "$FILE_PATH" == "/tmp/"* ]]; then
  exit 0
fi

# Block: path is outside worktree (likely main repo)
echo "BLOCKED: Write to $FILE_PATH denied — agent is in worktree at $CWD. Only write inside the worktree." >&2
exit 2
