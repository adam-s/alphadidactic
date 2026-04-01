# Workflow Rules

Process rules adapted for research experiment agents. These prevent resource waste and zombie processes.

---

## Commit After Every Cycle

Every iteration cycle that produces instruction patches or experiment results gets its own commit **before starting the next cycle.** Do not batch commits across cycles. Do not defer — `Commit: (pending)` in the cycle log means the orchestrator forgot this step.

After the adversary completes, the orchestrator MUST immediately execute steps 1-9 below WITHOUT waiting for user input. This is not optional. This is not "when time permits." The cycle is not complete until patches are applied and committed. If the orchestrator presents findings and waits for the user to say "make the changes," the orchestrator has failed — the changes should already be made.

1. **Self-reflection grid (MANDATORY).** Fill this grid for every cycle. The answers drive what gets patched. Do NOT skip this step.

   | Area | Question |
   |------|----------|
   | Agent compliance | Did the agent follow ALL instructions? Where did it diverge? Check tool call logs. |
   | Missing instructions | What instruction would have prevented each divergence? |
   | Vague/contradicted | What instruction exists but was too vague, too long, or contradicted by another? |
   | Monitoring | Did monitoring catch problems early enough? What signal was missed? |
   | Orchestrator gaps | What did the reviewer/adversary find that I missed in my quick scan? |
   | Shared infrastructure | Did the agent work around a gap in `shared/`? Should that be a utility? |
   | Verification gaps | Did Checks 6/7 catch what they should? What slipped through? |
   | Token efficiency | How many tool calls did the agent use vs budget? How many did the ORCHESTRATOR use for monitoring vs the 60-call budget? Were there wasted polls during non-interventionable phases? |
   | Generalization | Is every proposed patch a general principle, not a specific fix? |

   Write the grid answers in the cycle log entry in `docs/curriculum-state.md`.

2. **List all possible improvements.** From the grid answers, list EVERYTHING that could be improved — instructions, infrastructure, utilities, paradigm example, orchestrator process, verification. Be exhaustive.

3. **Patch everything actionable.** Implement ALL items that are generalized principles — not just the top 3. More patches per cycle = faster convergence. Every patch must be a generalized principle — no specific instruments, thresholds, experiments, or domain details. If you can't state the rule without naming a specific case, it's not generalized enough. The only reason to defer a patch is if it requires more investigation or conflicts with another patch.

4. **Apply instruction patches immediately.** Edit the files NOW for all actionable items. Do not summarize what should change — make the actual edits. Paradigm examples, `.claude/` rules, shared infrastructure — whatever the improvement requires. The user should see diffs, not proposals.
5. **Audit `.claude/` instructions** (see § Instruction Audit below)
6. Evaluate statistical analysis proposals from reviewer (Section C) and adversary findings:
   - Promote useful custom tests from experiment directories to `shared/stat_tests.py` (see promotion criteria in `reference/statistical-analysis.md`)
   - Update `reference/statistical-analysis.md` decision framework if the cycle revealed new experiment types, anti-patterns, or interpretation guidance
7. Stage all changed `.claude/` files, `shared/` infrastructure, `docs/curriculum-state.md`, and experiment output
8. Commit with the cycle number and a summary of patches
9. Record the commit hash in `docs/curriculum-state.md`
10. Only then proceed to the next cycle

This ensures every cycle's patches are independently recoverable, and the git log reconstructs the full instruction evolution.

---

## Instruction Audit

**The canonical audit checklist is now in `.claude/CLAUDE.md`.** It is inline so the orchestrator sees it every session without a cross-reference. The detailed explanations below are supplementary context.

### 1. Generalize domain-specific rules

Rules should be general principles, not one-off fixes for specific experiments. If a rule names a specific instrument, threshold, time interval, or experiment number, generalize it.

**Automated check:** Grep rules/ and agents/ for specific instrument tickers, experiment numbers, hardcoded thresholds, AND domain-specific data source names (table names, series IDs, cache directories, domain-specific function names). See CLAUDE.md § Instruction Audit step 1 for the canonical grep command.

**Test:** "Would this rule still make sense for a completely different instrument, signal type, and time horizon?" If no, generalize or delete. Domain-specific details belong in `reference/`, not in always-loaded rules or agent system prompts.

### 2. Resolve contradictions

Two files saying different things about the same behavior is worse than either being wrong alone — the agent picks whichever it reads last. When found:
- Decide which is correct
- Remove the other instruction entirely
- If both are valid under different conditions, merge into one rule with explicit conditions

### 3. Remove duplication

The same concept explained in multiple files wastes tokens and creates drift risk (one gets updated, the other doesn't). Keep the canonical version in one place; other files reference it.

**Automated check:** For key concepts, count how many files explain them:

```bash
for concept in "C-exit" "pending-row" "split_threshold" "SPLIT_THRESHOLD" "settle_return" "tc_per_active"; do
  count=$(grep -rl "$concept" .claude/ --include='*.md' 2>/dev/null | wc -l)
  [ "$count" -gt 2 ] && echo "DRIFT RISK: '$concept' in $count files"
done
```

More than 2 files explaining the same concept = drift risk. Consolidate to 1 canonical + pointers.

### 4. Verify all file references resolve

**This is the most critical check.** If a file reference points to a file that doesn't exist, the agent silently ignores the instruction and follows whatever fallback it has. Run this command every cycle:

```bash
grep -rh '\.claude/[a-z].*\.md' .claude/ | grep -oE '\.claude/[a-zA-Z/_-]+\.md' | sort -u | while read f; do [ ! -f "$f" ] && echo "BROKEN: $f"; done
```

Any BROKEN reference is a **critical bug** — it means an entire instruction file is invisible to the agent. Fix immediately.

### 4b. Verify agent compliance on new instructions

When adding a new mandatory instruction (e.g., "call WebSearch", "run stat tests"), do NOT assume the agent will follow it. After the first cycle with the new instruction:

1. Check the agent's tool call log — did it actually call the required tool?
2. Check the output — does it contain the required content (URLs, test results)?
3. If not, trace the loading chain: did the agent read the file containing the instruction?

Agents follow what they read, not what we intend. If an instruction is in a file the agent doesn't read, it's invisible.

### 5. Prune rules enforced by infrastructure

If `shared/` infrastructure now makes a bug class impossible by construction, the verbose rule warning about it is dead weight. Replace with a brief pointer: "Enforced by [infrastructure] — see [reference]."

**Automated check:** After adding new infrastructure (like `settle_return()`), grep for rules that warn about the same bug class and trim them:

```bash
# Example: after adding settle_return(), check if C-exit warnings can be trimmed
grep -rn "missing exit\|missing price.*max loss\|C-exit" .claude/rules/ .claude/agents/ --include='*.md' | head -20
```

If the infrastructure prevents the bug by construction, the rule should be 1-2 lines pointing to the infrastructure, not a multi-paragraph explanation.

### 6. Scoring alignment

Does the reviewer score against every new rule? An unscored rule is unenforceable — the experiment agent can ignore it and still get 28/28. Check `agents/research-reviewer-agent.md` for corresponding audit criteria.

### 7. End-to-end read

Read all affected files after patching. A patch at line 50 may contradict something at line 200. Read the whole file, not just the diff.

### 8. Measure

```bash
wc -c .claude/rules/*.md .claude/CLAUDE.md
```

Target ≤ 50KB. Report the total in the commit message.

---

## Pre-Screen Before Reviewer/Adversary

After the experiment agent completes, spend 5 minutes scanning for obvious bugs BEFORE launching reviewer/adversary. Check:

1. **numpy.bool_ trap:** `grep -n "is True\|is False" run_strategy.py verify_integrity.py` — any match on a variable from numpy/parquet is a bug. Must use `== True` or `bool()`.
2. **TC application:** Is cost applied on every active day, or only on position changes? (grep for `TC` in run_strategy.py)
3. **C-exit:** Missing exit = max loss? Missing entry = non-trade? (grep for `-1.0` and `0.0`)
4. **Check 6 independence:** Does verify_integrity.py import from run_strategy.py? (grep for `from run_strategy`)
5. **Check 7 dynamic:** Is the pass/fail result computed or hardcoded? (grep for `True` near check7)
6. **Split ledger:** Does the code query `stock_splits` or only use magnitude thresholds?

If you find a bug, patch the instructions and rerun the experiment. Don't waste reviewer/adversary tokens on a known-broken experiment.

---

## Reading Agent Output

Never spawn sub-agents (Explore, etc.) to read agent output files — they hang on large files and waste context. Use direct bash commands instead:

```bash
# Get verdict
grep '"recommendation"' OUTPUT_FILE | tail -1

# Get score
grep '"score"' OUTPUT_FILE | tail -1

# Get findings summary
grep 'FINDING\|CRITICAL\|HIGH\|MEDIUM' OUTPUT_FILE | tail -10

# Get web search count
grep -c '"name":"WebSearch"' OUTPUT_FILE
```

If you need more detail, use `tail -c 5000` to read the end of the file where the summary usually is.

---

## No Retry on Unexpected Output

If a script produces unexpected output (wrong shape, unexpected values, errors), do NOT re-run it hoping for a different result. Instead:
1. Read the output carefully
2. Identify the root cause
3. Fix the code
4. Then re-run

Research code is deterministic. If it produces wrong output once, it will produce wrong output every time until the code changes.

---

## One Install

Install dependencies once at the start. Do not re-install mid-experiment. If a dependency is missing, add it to `requirements.txt` and install once.

```bash
pip install -r requirements.txt  # once, at setup
```

---

## Process Cleanup

Before starting a new experiment run:
1. Kill any lingering Python processes from previous runs
2. Clean up any temporary files
3. Verify database connections are closed

After completing an experiment:
1. Close all database connections explicitly
2. Remove any temporary files created during the run
3. Save all outputs to the experiment directory

---

## Database Safety

See `rules/database-safety.md` for all database rules (connection limits, timeouts, density tiers, recovery procedures).

---

## Background Agents + APC Monitoring

This is the authoritative reference for agent communication and system monitoring. Agent definitions (`.claude/agents/`) and database safety (`rules/database-safety.md`) cross-reference this section.

### Rule 1: All sub-agents run in the background

The orchestrator must never block on a sub-agent. Launch with `run_in_background: true`. This keeps the orchestrator interactive — the user can ask questions, check status, or intervene while agents work.

### Rule 2: Every agent and script reports via APC

Every agent prompt must include an APC channel ID:

```
APC_CHANNEL: cycle{N}_{role}_{experiment}    # e.g., cycle4_experiment_09_eod
```

**Agent-level reporting** — after every pipeline step:

```python
from shared.agent_protocol import AgentChannel
channel = AgentChannel("cycle4_experiment_09_eod", "09_eod_reversal")
channel.progress("STEP_0", "Pre-flight complete", {"items": 7})
channel.progress("STEP_1", "Dataset built", {"rows": 9261, "elapsed_min": 0.1})
channel.complete({"sharpe_test": 0.84})
```

**Script-level reporting** — inside any loop with >100 iterations:

```python
from shared.agent_protocol import ScriptProgress
progress = ScriptProgress.attach("cycle4_experiment_09_eod")
progress.start("build_dataset", total=len(trading_days))
for i, day in enumerate(trading_days):
    # ... work ...
    progress.tick(i + 1)  # throttled to every 30s
progress.done({"rows": len(df)})
```

No silent agents. A 20-minute dataset build with zero APC messages is unacceptable.

### Rule 3: Sleep-then-read is the ONLY monitoring pattern

**Why:** Background commands (`run_in_background`) only notify on completion. Their intermediate stdout goes to a file the user never sees. `apc monitor` and `apc wait` with `run_in_background` are useless for visibility — the user gets nothing until the agent finishes. The ONLY way to surface agent progress in the chat is a foreground `sleep + read` loop.

**The pattern:**

```bash
sleep 180 && python -m shared.apc read <channel> --new
```

Repeat this every 3 minutes until the channel shows COMPLETE or ERROR. Each call costs one tool invocation but gives the user real-time visibility into agent progress.

**Orchestrator tool-call budget per agent:**
- Launch: 1 call
- Monitor experiment agent: max 10 sleep-then-read calls (~30 min coverage)
- Pre-screen experiment output: 5-8 calls (grep checks)
- Monitor reviewer: max 3 sleep-then-read calls
- Monitor adversary: max 3 sleep-then-read calls
- **Total orchestrator budget per cycle: ~60 tool calls**

**The workflow:**

1. **Launch agent** → first `apc read <channel> --new` to confirm alive (1 call)
2. **Do productive work** while agent runs (prepare reviewer prompt, read reference, etc.)
3. **After productive work is done** → enter sleep-then-read loop: `sleep 180 && python -m shared.apc read <channel> --new`
4. **On each read:** check for COMPLETE/ERROR. If COMPLETE, proceed to diagnose. If ERROR, investigate.
5. **If system metrics show CRITICAL** (in APC messages from ScriptProgress), intervene immediately.

**Do NOT:**
- Use `apc monitor` or `apc wait` with `run_in_background` — output is invisible to the user
- Use `agent_protocol tail` — enters an infinite poll loop
- Check file size with `wc -c` or trial count with `wc -l` — use APC
- Poll more than once per 3 minutes

**Cleanup:** `python -m shared.apc clean` — remove all channels + cursors after a cycle.

**What each poll checks:**

| Check | Shown by `apc status` | Red flag |
|-------|----------------------|----------|
| APC progress | Agent step + staleness | Stale >5 min = may be stuck |
| DB connections | Connection count | >4 = leak |
| Active queries | Query list | Any >30s = wrong query |
| Disk | Free GB | CRITICAL <15 GB |
| Memory | Used % | CRITICAL >95% |
| CPU | Load % | Sustained 100% = stuck loop |

### Rule 4: Orchestrator intervention

If a poll reveals a problem:

| Problem | Action |
|---------|--------|
| No APC messages after 2 min | Check if agent process is running. If dead, note the failure and relaunch. |
| APC stalled (same step for >5 min) | Check DB for stuck queries (`python -m shared.db_monitor status`). Kill if needed. |
| Disk CRITICAL | `docker builder prune --all -f && docker image prune -f`. If still critical, stop agents. |
| Memory CRITICAL | Check for runaway processes. Kill the largest non-essential Python process. |
| DB connections >4 | `python -m shared.db_monitor kill`. Then `pkill -f build_dataset.py` etc. |
| Query >30s | `python -m shared.db_monitor kill --max-seconds 30`. The query is wrong, not slow. |
| Zombie processes | `pkill -f "build_dataset.py"` / `pkill -f "run_strategy.py"` / `pkill -f "verify_integrity.py"` |

### Rule 5: Blind agent isolation

Experiment agents must not see reference experiments or prior experiment output.

**Defense in depth — three layers:**

1. **Gitignore:** `reference_experiments/` is gitignored — not in git tracking.
2. **WorktreeCreate hook:** `.claude/hooks/create-worktree.sh` creates worktrees at `/tmp/claudodidact-worktrees/` (outside the repo). Agents can't traverse to the main repo filesystem. Branches from local HEAD so unpushed instruction patches are visible.
3. **PreToolUse guard:** `.claude/hooks/guard-worktree-writes.sh` blocks Write/Edit operations that target paths outside the worktree.

No `mv` to `/tmp/` needed — the hooks handle isolation by construction.

**For reruns:** Previous experiment output committed to main will appear in worktrees. Delete the experiment directory AND commit the deletion before creating the worktree (see Rule 5b above).

**The experiment agent's canary check** (`agents/experiment-agent.md`) verifies isolation at startup and reports via APC. If the orchestrator sees `ISOLATION_VIOLATION`, the hooks may have failed.

**Reviewer/adversary access:** These agents run in the main repo (no worktree). They can access `reference_experiments/` directly from the filesystem.

### Rule 5b: Experiment output isolation for reruns

Worktrees branch from HEAD. Any experiment output committed to main is visible to agents in worktrees. This contaminates reruns — the agent can read its own prior implementation.

**Before rerunning an experiment:**
1. Delete the previous experiment directory
2. **Commit the deletion** to main (so HEAD is clean)
3. THEN create the worktree and launch the agent

```bash
# Wrong: delete files but don't commit — worktree still has them from HEAD
rm -rf experiments/XX_name/
# Agent worktree branches from HEAD which still has the files ← CONTAMINATED

# Right: delete AND commit before launching worktree
rm -rf experiments/XX_name/
git add -A experiments/XX_name/ && git commit -m "Remove XX_name for clean rerun"
# Now HEAD is clean — worktree won't have the files
```

**For the orchestrator's own access:** If the orchestrator needs to compare results across runs, keep experiment output on a separate branch or in `docs/` summaries. Do not rely on main-branch experiment directories for comparison — they contaminate worktrees.

**Alternative:** Add experiment output directories to `.gitignore` so they're never committed. The orchestrator reads them directly from the filesystem, and worktrees never see them. This requires the orchestrator to extract results before cleanup.

### Rule 6: PID tracking and process management

**Track every process you spawn.** Write PIDs to `/tmp/claudodidact-pids.txt` so that if the session crashes, the next session can clean up orphans.

**Starting background processes:**

```bash
PID_FILE=/tmp/claudodidact-pids.txt
> "$PID_FILE"  # clear at cycle start

# DB monitor
python -m shared.db_monitor monitor --auto-kill --interval 10 --max-query 45 &
echo "$! db_monitor" >> "$PID_FILE"

# System monitor
python -m shared.system_monitor apc &
echo "$! system_monitor" >> "$PID_FILE"
```

When using the Bash tool with `run_in_background`, the tool returns a task ID (not a PID). Record these task IDs too — use `TaskStop` to stop them.

**Stopping agents:** Use `TaskStop` with the agent's task ID to stop a running background agent. This is cleaner than `pkill` because it terminates the agent's full process tree.

### Rule 7: Cleanup after each cycle

Order matters — stop writers before deleting what they write to.

```bash
PID_FILE=/tmp/claudodidact-pids.txt

# 1. Stop background monitors via tracked PIDs
if [ -f "$PID_FILE" ]; then
    while read pid name; do
        kill "$pid" 2>/dev/null && echo "Killed $name ($pid)"
    done < "$PID_FILE"
    rm "$PID_FILE"
fi

# 2. Fallback: pattern-match anything missed
pkill -f "system_monitor apc" 2>/dev/null
pkill -f "db_monitor monitor" 2>/dev/null
pkill -f "build_dataset.py" 2>/dev/null
pkill -f "run_strategy.py" 2>/dev/null
pkill -f "verify_integrity.py" 2>/dev/null

# 3. Clean APC channels (no writers remain)
python -m shared.agent_protocol clean

# 4. Verify clean state
python -m shared.db_monitor status                 # expect 2 connections
ps aux | grep -E "build_dataset|run_strategy|verify_integrity|system_monitor apc|db_monitor monitor" | grep -v grep  # expect empty
```

**On session start (recovering from crash):**

```bash
# Kill any orphans from a prior session
PID_FILE=/tmp/claudodidact-pids.txt
if [ -f "$PID_FILE" ]; then
    echo "Found orphan PID file — cleaning up prior session"
    while read pid name; do kill "$pid" 2>/dev/null; done < "$PID_FILE"
    rm "$PID_FILE"
fi
pkill -f "build_dataset.py" 2>/dev/null
pkill -f "run_strategy.py" 2>/dev/null
pkill -f "verify_integrity.py" 2>/dev/null
python -m shared.db_monitor status
```

---

## Worktree Discipline

When using git worktrees for parallel experiments:
- Each experiment gets its own worktree
- Worktrees are created in `/tmp/` (not in the main repo)
- **After creating a worktree, copy gitignored cache files** that shared infrastructure needs:

  ```bash
  # Copy shared cache files into the worktree
  cp -r shared/cache/ /tmp/worktree_path/shared/cache/ 2>/dev/null
  ```

  Without cache files, shared data loaders fall back to DB queries (slower but functional).
- Clean up worktrees after the experiment completes or fails
- Run `git worktree prune` after cleanup

```bash
# Create
git worktree add /tmp/exp_XX_name -b exp/XX_name

# After completion
git worktree remove /tmp/exp_XX_name --force
git worktree prune
```

**Known Claude Code limitation:** `Agent(isolation: "worktree")` creates worktrees in `.claude/worktrees/` inside the repo, NOT in `/tmp/`. This cannot be configured ([#35149](https://github.com/anthropics/claude-code/issues/35149)). Worktrees inside the repo can traverse to the parent directory. **Do not rely on worktree location for isolation.** The primary defense for blind agents is physical separation: move `reference_experiments/` to `/tmp/claudodidact_references/` before launching experiment agents (Rule 5). The worktree location is a secondary concern.

---

## Experiment Naming

- Prefix with sequential number: `00_`, `01_`, `02_`, etc.
- Use snake_case descriptive name: `03_options_flow_momentum`
- Branch name matches: `exp/03_options_flow_momentum`

---

## Output Discipline

**Principle:** Every generated artifact belongs to its experiment. Nothing generated by an experiment should land in `shared/`, the repo root, or any other experiment's directory. `shared/` contains only source code (`.py`) and `__init__.py` — never data, plots, logs, or test output.

All generated artifacts go in `experiments/XX_name/output/`:
- `output/dataset.parquet` — cached dataset
- `output/results.parquet` — strategy results
- `output/pnl_chart.png` — P&L visualization
- `output/stat_tests.json` — statistical robustness results
- `output/` — any other generated files (CSV, profiler output, `__pycache__`)

Experiment-specific custom code (e.g., custom statistical tests) stays in the experiment root alongside other `.py` files:
- `stat_tests.py` — custom statistical tests for this experiment
- `common.py`, `build_dataset.py`, `run_strategy.py`, `verify_integrity.py`
- `TEMPORAL_PROOF.md`, `PREFLIGHT.md`, `PROGRESS.md`
- Console output is logged, not just printed

Do NOT write outputs to:
- The repo root
- `shared/` — infrastructure source code only, never data or artifacts
- Other experiments' directories
- Temporary directories (except worktrees)
- Stdout only (must also be saved to file)
