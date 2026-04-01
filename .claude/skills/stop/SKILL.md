---
name: stop
description: Emergency stop — kill all agents, clean APC, restore references, prune worktrees, verify DB clean. Use when stopping a cycle or recovering from a crash.
disable-model-invocation: true
---

# Stop All Agents and Clean Up

Run these steps in order:

## 1. Kill all background agents

Stop any running agents using TaskStop for each active agent ID. If IDs are unknown, proceed to process cleanup.

## 2. Kill experiment processes

```bash
pkill -f "build_dataset.py" 2>/dev/null
pkill -f "run_strategy.py" 2>/dev/null
pkill -f "verify_integrity.py" 2>/dev/null
pkill -f "system_monitor apc" 2>/dev/null
pkill -f "db_monitor monitor" 2>/dev/null
```

## 3. Clean APC channels

```bash
python -m shared.agent_protocol clean
```

## 4. Restore reference experiments (if hidden)

```bash
if [ -d /tmp/claudodidact_references ]; then
  mv /tmp/claudodidact_references reference_experiments
  echo "References restored"
else
  echo "References already in place"
fi
```

## 5. Prune worktrees

```bash
git worktree prune
rm -rf .claude/worktrees/*/  2>/dev/null
```

## 6. Kill orphaned processes from other projects

```bash
# Stale interceptor/other project processes that burn CPU
pkill -f "interceptor-worktrees" 2>/dev/null
pkill -f "interceptor-fresh-clone" 2>/dev/null
```

## 7. Clean temp directories

```bash
rm -rf /tmp/claudodidact-worktrees/ 2>/dev/null
rm -rf /tmp/claudodidact_references/ 2>/dev/null
```

## 8. Verify clean state

```bash
python -m shared.db_monitor status
python -m shared.system_monitor
ps aux | grep -E "build_dataset|run_strategy|verify_integrity|system_monitor|db_monitor|python.*shared" | grep -v grep
```

Report: connections count, active queries, system health, any zombie processes found.
