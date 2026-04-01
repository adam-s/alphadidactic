#!/usr/bin/env bash
# H9 guard: Block hardcoded causal flags in verify_integrity files.
# Causal flags must be computed dynamically from available_at vs used_at.
set -euo pipefail

INPUT="$(cat)"
FILE_PATH="$(printf '%s' "$INPUT" | jq -r '.tool_input.file_path // empty')"

# Only check verify_integrity files
if [[ "$FILE_PATH" != *verify_integrity* ]]; then
  exit 0
fi

# Extract content — Write uses .content, Edit uses .new_string
CONTENT="$(printf '%s' "$INPUT" | jq -r '.tool_input.content // .tool_input.new_string // empty')"

if [[ -z "$CONTENT" ]]; then
  exit 0
fi

# Check for hardcoded causal flags (skip comment lines)
if echo "$CONTENT" | grep -v '^\s*#' | grep -qP '"causal"\s*:\s*(True|False)|causal\s*=\s*(True|False)'; then
  echo "BLOCKED: Hardcoded causal flag in $FILE_PATH. Check 3 requires: causal = parse_time(available_at) <= parse_time(used_at). See rules/experiment-checks.md Check 3." >&2
  exit 2
fi

exit 0
