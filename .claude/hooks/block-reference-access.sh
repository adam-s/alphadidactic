#!/bin/bash
# ─────────────────────────────────────────────────────────────────────
# HOOK: Block experiment agents from reading reference_experiments/
# ─────────────────────────────────────────────────────────────────────
#
# WHY THIS EXISTS:
#
# The instruction tuning loop has two phases:
#   1. BUILD: Experiment agents create experiments from instructions alone
#   2. VALIDATE: Compare blind agent output against hand-debugged references
#
# Experiment agents must be BLIND — they work from .claude/ rules and
# shared/ infrastructure only. If they peek at reference_experiments/,
# they copy the answer instead of proving the instructions are sufficient.
#
# CLAUDE.md is auto-injected into all sub-agents (can't be prevented),
# and it mentions reference_experiments/ in the repo structure. The agent
# definition says "You CANNOT read reference_experiments/" but that's
# behavioral, not structural. This hook makes it structural.
#
# Reviewer and adversary agents ARE allowed to read references — they
# need them for comparison. Set ALLOW_REFERENCE_ACCESS=1 in their env
# to bypass this hook. Experiment agents must NOT have this env var.
#
# The hook checks stdin for PreToolUse events on Read, Glob, Grep, and
# Bash tools. If the tool input references reference_experiments/, the
# hook exits non-zero with a reason, which blocks the tool call.
# ─────────────────────────────────────────────────────────────────────

set -euo pipefail

# Bypass: reviewer/adversary agents set this env var to access references
if [ "${ALLOW_REFERENCE_ACCESS:-}" = "1" ]; then
  exit 0
fi

# Read the JSON event from stdin
INPUT=$(cat)

# Extract tool name and input
TOOL=$(echo "$INPUT" | python3 -c "import sys,json; print(json.load(sys.stdin).get('tool_name',''))" 2>/dev/null || echo "")

# Only check file-access tools
case "$TOOL" in
  Read|Glob|Grep|Bash) ;;
  *) exit 0 ;;
esac

# Check if the tool input references reference_experiments/
TOOL_INPUT=$(echo "$INPUT" | python3 -c "
import sys, json
event = json.load(sys.stdin)
inp = event.get('tool_input', {})
# Check all string values in the tool input
for v in inp.values():
    if isinstance(v, str) and 'reference_experiments' in v:
        print('BLOCKED')
        break
" 2>/dev/null || echo "")

if [ "$TOOL_INPUT" = "BLOCKED" ]; then
  echo "BLOCKED: Experiment agents cannot access reference_experiments/. Work from .claude/ rules and shared/ infrastructure only."
  exit 2
fi

exit 0
