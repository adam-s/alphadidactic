---
name: instruction-tuning
description: Self-Improving Instruction Tuning
---

# Self-Improving Instruction Tuning

**Sub-agents are clones of `.claude/`.** Every sub-agent branches from HEAD and inherits all rules, examples, agent definitions, skills, and reference files. When the orchestrator updates `.claude/` and commits, the next sub-agent born from HEAD gets every change automatically. There is no separate "orchestrator instructions" vs "sub-agent instructions" — they are the same repo. Updating yourself IS updating the clones. This is why commit-before-launch discipline matters: uncommitted changes don't propagate.

**The core question: "How would I build this correctly?"**

The orchestrator watches each sub-agent attempt, identifies where it diverges from correct behavior, and asks: "What generalized instruction would have prevented this?" Then patches `.claude/` and reruns. The loop converges when the sub-agent produces correct output without any instruction-specific help.

The agent output is throwaway. The instruction improvements are the product.

**There is NO backwards compatibility.** The experiments are the test harness for `.claude/`. Every iteration can and should change anything — instructions, utilities, infrastructure, shared code, agent definitions, hooks, skills. Nothing is sacred except the principle that the instructions must improve. If an example doesn't teach the right pattern, rewrite it. If a shared utility makes the wrong thing easy, restructure it. If a rule is ignored because it's a paragraph buried in 10KB of text, promote it to a hard stop. The orchestrator must not hesitate to make breaking changes — the next sub-agent starts fresh from HEAD.

---

## Consent Check

Before running this skill, confirm with the user:

1. **Resource consumption:** This will spawn multiple sub-agents, each making up to 200 tool calls. Each review adds 40 tool calls. Total per cycle: ~960 tool calls (4 agents + 4 reviews).
2. **Autonomous agents:** Sub-agents will read reference experiments, write code, query the database, and produce reports autonomously.
3. **Shared database:** Sub-agents access TimescaleDB (read-only). Maximum 4 concurrent agents to respect connection limits.
4. **Worktree creation:** Each agent gets a git worktree in `/tmp/`. These are cleaned up after completion.

Proceed only with explicit user confirmation.

---

## Configuration

```yaml
max_concurrent_agents: 4        # shared DB constraint
max_cycles: 5                   # stop after 5 instruction-improvement cycles
convergence_target: 24          # out of 28 reviewer points
agent_budget: 200               # tool calls per experiment agent
reviewer_budget: 40             # tool calls per reviewer
```

---

## Input: Hypothesis Prompts

The user provides a list of hypothesis prompts. Each prompt describes a causal relationship to test.

```markdown
## Hypothesis 1: [Signal Type] — [Instrument/Universe]
When [condition based on available data], take [position].
The [causal mechanism] creates [expected return pattern].
SYMBOLS: [instrument(s)]
DATE_RANGE: [start] to [end]

## Hypothesis 2: [Different Signal Type] — [Different Universe]
[Condition] predicts [outcome]. [Causal mechanism].
SYMBOLS: [instrument(s)]
DATE_RANGE: [start] to [end]
```

---

## The Loop

One hypothesis at a time. Iterate until the agent produces clean output, then move to the next.

### Iteration N (same hypothesis):

#### Step 1: Prepare clean HEAD

Before launching, ensure HEAD is clean and instructions are pruned:

```bash
# 1. Delete previous experiment output (worktrees branch from HEAD)
rm -rf experiments/{experiment_name}/

# 2. Prune .claude/ — check for contradictions, duplication, stale references
#    Measure: wc -c .claude/rules/*.md .claude/CLAUDE.md
#    If total > 50KB, consolidate before launching (agents pay per-turn)
wc -c .claude/rules/*.md .claude/CLAUDE.md

# 3. Commit clean HEAD
git add -A && git commit -m "Clean for {hypothesis} iter {N}"

# 5. Verify clean state
python -m shared.agent_protocol clean
python -m shared.db_monitor status  # verify 2 connections, no queries
```

**WorktreeCreate hook** creates worktrees at `/tmp/claudodidact-worktrees/` (outside repo). Agents can't see `reference_experiments/` or main-repo files. No `mv` needed.

#### Step 2: Launch experiment agent

```
You are an experiment agent. Follow .claude/agents/experiment-agent.md exactly.

HYPOTHESIS: {hypothesis}
SYMBOLS: {symbols}
DATE_RANGE: {date_range}
EXPERIMENT_DIR: experiments/{NN}_{experiment_name}
CYCLE: {hypothesis}_iter{N}
APC_CHANNEL: {hypothesis}_iter{N}_experiment

Read all mandatory files in .claude/ before writing code. Follow the 5-step pipeline.
Fill the commit gate matrix with specific evidence.

IMPORTANT: Before writing ANY code, run `ls shared/` and check what functions already exist.
Run `python -m shared.system_monitor` before starting.
Check symbol density with `shared/db_monitor.get_density(symbol)`.
Test on 5 trading days first before expanding to the full date range.
All generated files (parquet, etc.) go in output/ subdirectory.

After STEP 3 passes, ALWAYS run STEP 5b (Optuna parameter optimization).
A null at one parameterization says nothing — search the space.
Use shared/optuna_utils.py. Train-only objective. Seed with baseline. Re-verify after.
See shared/optuna_utils.py for the API.
```

Launch with `model: opus`, `isolation: worktree`, `run_in_background: true`.

#### Step 3: Monitor Agent (sleep-then-read loop)

See `docs/orchestrator-process.md` § Rule 3 for the full protocol.

**Do ALL productive work BEFORE entering the monitor loop** (prepare reviewer prompt, read reference, prepare adversary prompt). Then:

1. Launch agent
2. Enter sleep-then-read loop — repeat until COMPLETE or ERROR:
   ```bash
   sleep 180 && python -m shared.apc read <channel> --new
   ```
3. On COMPLETE: proceed to Step 4. On ERROR: investigate.

**Do NOT** use `apc monitor` or `apc wait` with `run_in_background` — their output goes to a file the user never sees. The sleep-then-read loop is the ONLY way to surface progress in the chat.
- Log all warning signs (unbounded SQL, Sharpe > 3.0, missing `prev_decision`, raw SQL) for the instruction improvement step.

#### Step 4: Diagnose

When the agent completes, scan the output for bugs. The orchestrator does this FIRST — before launching reviewer/adversary. Check:

1. **Check 7 implementation:** Does it have the cross-period opposing-directions check? Does it use unconditional benchmark (not vs 0%)?
2. **Split filter:** Applied on BOTH signal and return sides? (grep SPLIT_THRESHOLD in both build_dataset.py and run_strategy.py)
3. **Split ledger:** Does the code use `CorporateActionLedger` / `build_default_split_ledger()` as PRIMARY? Magnitude-only = will miss forward splits on leveraged ETFs.
4. **C-exit:** Missing exit = max loss? Missing entry = non-trade? (grep for -1.0 and 0.0 near missing price logic)
5. **Fabricated evidence:** Do the quantitative claims in TEMPORAL_PROOF.md match reality? (spot-check 2 numbers)
6. **Stale line references:** Do line numbers in TEMPORAL_PROOF.md point to the claimed code? (grep 2-3 claimed line numbers)
7. **Train/test labels:** Do period labels match common.py:TRAIN_END? Does actual data range match START_DATE?
8. **H9 — Check 3 dynamic causal:** Does verify_integrity.py compute causal flags from timestamps, or hardcode True?
9. **H10 — Check 6 raw DB:** Does verify_integrity.py import CursorEngine and query raw prices?
10. **Optuna (MANDATORY):** Did the agent run STEP 5b? If not, that's a failure — Optuna is required after STEP 3 passes. If so: was baseline verified FIRST (H11)? Is the objective train-only (H12)? Was baseline seeded? Were optimized params re-verified? Is the train-test gap reported? Does TEMPORAL_PROOF show baseline vs optimized side-by-side?
11. **Output directory:** Did parquet go in output/?

If the orchestrator finds a bug directly, **skip reviewer/adversary** — diagnose the instruction gap, patch it, delete the experiment, and rerun immediately. This saves ~160K tokens per iteration.

Only launch reviewer + adversary when the orchestrator cannot find bugs in a quick scan (~5 min). That's when the experiment needs deeper forensic analysis.

#### Step 5: Patch instructions

For each bug found:

1. **Identify the instruction gap:** The agent had the rule but didn't follow it — is the rule too vague? Does it need an implementation pattern? Or is the rule missing entirely?
2. **Write a GENERALIZED patch** — never specific to this experiment. Every instruction must be a principle that a coding agent can extrapolate to new situations, not a specific rule tied to one scenario. Before committing, apply this self-check:
   - **Generalization test:** Would this instruction prevent the same CLASS of bug in a completely different experiment with different instruments, signals, and data sources? If the instruction mentions specific symbols, thresholds, column names, or experiment details, it's too specific. Rewrite it as a principle.
   - **Example test:** Specific cases may ILLUSTRATE the principle as "do this / don't do this" examples, but the principle must stand alone without the example. If removing the example makes the instruction meaningless, the principle is missing. The statement IS the principle; the example is optional illustration.
   - **Coding agent test:** Could an agent solving a DIFFERENT problem use this instruction to avoid this class of bug? If yes, it's generalized. If the agent would need to know about THIS experiment to apply it, it's too specific.
   - **Extrapolation test:** Read the patch as if you're a coding agent seeing it for the first time. Can you derive the correct behavior for a novel case from the principle alone? If the principle requires specific numeric values, function names, or implementation details to be actionable, it's a recipe, not a principle. Principles teach judgment; recipes teach steps.
3. **Apply** to the correct file (rules/ for constraints, skills/ for workflows, reference/ for knowledge)
4. **Update the orchestrator's OWN instructions** if the cycle revealed a gap in the orchestrator's process — monitoring, quick scan, audit steps, self-reflection grid, launch prompts. The orchestrator is not exempt from improvement. If a recurring issue survived 3+ cycles, the orchestrator's process failed to catch or fix it — change the process, not just the sub-agent rules.
5. Update shared utilities if an architectural fix would prevent the bug class by construction.
6. **Grep `.claude/` for contradictions** with all patches

#### Step 6: Clean and rerun

```bash
rm -rf experiments/{experiment_name}/
git add -A && git commit -m "H{X} iter {N}: found {bug}, patched {file}"
# Now HEAD is clean — relaunch from Step 2
```

Repeat until the agent produces clean output (no bugs found in Step 4).

#### Step 6b: Optuna pass (after bugs are fixed)

Once the agent produces a clean experiment (STEP 3 passes, no bugs in diagnose), relaunch with Optuna enabled. The agent runs STEPS 0-3 with hardcoded params, then STEP 5b (200 trials max). This is slower (~200 extra tool calls) so only do it after the instruction set is producing correct output.

The Optuna pass answers: "Does any parameterization of this hypothesis produce signal?" A null at hardcoded params is not a null result — a null across 200 Optuna trials IS.

If Optuna finds better params, the agent re-verifies (STEP 3 again with optimized params) and reports baseline vs optimized side-by-side.

#### Step 7: Reviewer + Adversary (final validation)

Only run after the agent passes the orchestrator's quick scan AND Optuna has run.

```bash
# Restore references BEFORE launching reviewer/adversary (they need them)
mv /tmp/claudodidact_references reference_experiments
```

Launch in parallel:

- Reviewer (opus): scores against 28-point rubric
- Adversary (opus): tries to break the experiment

If they find new bugs, patch and rerun. If they can't break it, the hypothesis is done.

#### Step 8: Audit, Prune, and Consistency Check

Before committing instruction changes, the orchestrator MUST run this checklist EVERY cycle:

**8a. Measure token cost:**
```bash
wc -c .claude/rules/*.md .claude/CLAUDE.md  # Target: under 50KB total
```
This is the highest per-turn cost — every byte loads on every turn for every agent. Sub-agents inherit ALL rules regardless of `paths:` frontmatter (#8395), so content reduction is the only real optimization for sub-agent token cost.

**8b. Duplication audit:**
```bash
# Search for concepts that appear in multiple files
grep -rl "key_term_from_patch" .claude/rules/ .claude/reference/ .claude/agents/
```
Decision tree:
- Same concept in **3+ files** → consolidate to ONE authoritative file, cross-reference from others
- Same concept in **2 files** with different consumers (rule + agent) → acceptable
- **Examples, implementation details, historical narratives** → move to `reference/` (loaded on-demand, not auto-loaded per turn)
- **Two paragraphs saying the same thing in one file** → merge

**8c. Content classification — what to keep vs move:**
- **KEEP in rules:** Mandatory gates, detection patterns, verification procedures, structural constraints
- **MOVE to reference:** Detailed explanations of WHY rules exist, implementation examples, historical bug stories, verbose patterns that can be summarized in one sentence
- **DELETE:** Rules that describe only one specific experiment, dead cross-references, outdated guidance superseded by newer rules

**8d. Cross-reference validation:**
```bash
# Verify all cross-references point to actual files
grep -r "reference/" .claude/rules/ | grep -o 'reference/[^ )]*' | sort -u
# Check each file exists
```

**8e. Generalization check:** Re-read each patch. Does it state a principle or a recipe? Can a coding agent extrapolate to a novel case? (See Step 5 self-check.)

**8f. Scoring alignment:** Confirm reviewer scoring criteria still match updated rules. A new rule without a corresponding scoring check is unenforceable.

**8g. End-to-end read:** Read all affected files after patching — a patch at line 50 may contradict something at line 200.

#### Step 9: Convergence Check

```
Cycle {N} Results:
| Hypothesis | A-class | C-class | Verification | Total | Pass? |
|-----------|---------|---------|-------------|-------|-------|
| H1        | /14     | /8      | /6          | /28   | Y/N   |
| H2        | /14     | /8      | /6          | /28   | Y/N   |
| ...       | ...     | ...     | ...         | ...   | ...   |

Convergence: {count passing} / {total} >= {convergence_target}/28
```

**Convergence reached** when: Fresh agents (not the same ones from earlier cycles) score 24+/28 on the reviewer without any hints or extra guidance beyond the instruction files.

**If not converged:** Go to Cycle N+1 with updated instructions.

**If converged OR max_cycles reached:** Stop and produce the final report.

---

## Self-Reflection Grid

**Before updating any instructions, utilities, or code, the orchestrator asks: "How would I do everything better?"**

After each run (experiment + reviewer + adversary), fill this grid. Each row is a question the orchestrator asks itself. The answers drive what gets patched.

| Area | Question | This Run | Action |
|------|----------|----------|--------|
| **Sub-agent instructions** | Did the agent follow the instructions correctly? Where did it diverge? | | |
| **Sub-agent instructions** | What instruction was missing that would have prevented the divergence? | | |
| **Sub-agent instructions** | What instruction exists but was too vague, too long, or contradicted by another? | | |
| **Orchestrator process** | Did monitoring catch problems early enough? What signal was missed? | | |
| **Orchestrator process** | Was the diagnose step thorough enough? What did the reviewer/adversary find that I missed? | | |
| **Orchestrator process** | Did I waste tokens? (unnecessary polls, duplicate work, over-monitoring, under-monitoring) | | |
| **Paradigm example** | Does the example show the pattern the agent needed? What's missing? | | |
| **Paradigm example** | Did the agent invent something that should be in the example for the next agent? | | |
| **Shared infrastructure** | Did the agent work around a gap in `shared/`? Should that be a utility? | | |
| **Verification** | Did Check 6/7 catch what they should? What slipped through? | | |
| **Verification** | Are the reviewer scoring criteria aligned with the current rules? | | |
| **Optimization** | Did the agent run Optuna (MANDATORY)? If not, that's a failure. If so: train-only objective, baseline seeded, re-verified, gap analyzed? Does the report show baseline vs optimized? | | |
| **Optimization** | Did the agent have sufficient Optuna instructions? What pattern was missing? | | |
| **Token efficiency** | Can any always-loaded rule be moved behind a `paths` filter or into `reference/`? | | |
| **Tool call efficiency** | How many tool calls did the agent use vs budget? Where did it spend the most? Were there wasted retries, unnecessary reads, or repeated queries? | | |
| **Tool call patterns** | Did the agent read files it didn't need? Did it restart scripts unnecessarily? Were there patterns the instructions could prevent (e.g., always reading X before Y)? | | |
| **Generalization** | Is every proposed patch a general principle, not a specific fix? | | |

**Only after filling this grid** should the orchestrator update instructions, utilities, example code, or infrastructure. The grid is the input; the patches are the output.

---

## Commit + Cleanup

After each cycle:
1. **Commit instruction patches and experiment output.** Every cycle that produces instruction changes or experiment results gets its own commit. Use the cycle log entry as the commit message body. Do not defer commits — `Commit: (pending)` in the cycle log means the orchestrator forgot this step.
2. **Update `docs/curriculum-state.md`** with the cycle log entry and commit hash.
3. Run `.claude/hooks/cleanup-research-agents.sh`
4. Remove worktrees for this cycle
5. Prune stale worktree references
6. Clean APC channels: `python -m shared.agent_protocol clean`

After the final cycle:
1. Full cleanup of all worktrees
2. Final commit with convergence report
3. Produce the convergence report

---

## Final Output

### Convergence Report

```markdown
# Instruction Tuning Report

## Summary
- Cycles run: {N}
- Converged: YES/NO
- Final scores: [table]

## Instruction Changes by Cycle
[Log of all changes]

## Remaining Gaps
[Any known issues the instructions don't yet cover]

## Recommendation
[Whether the instruction set is ready for production use]
```

### Deliverables
1. Updated rule files in `.claude/rules/`
2. Updated agent definitions if needed
3. Updated reference files if needed
4. Convergence report
5. All experiment outputs (throwaway, but preserved for reference)

---

## 20-Check Scorecard

The full scorecard combines reviewer checks with process checks:

| # | Check | Source | Max |
|---|-------|--------|-----|
| 1-7 | A-class temporal checks | Reviewer Audit 1 | 14 |
| 8-11 | C-class accounting checks | Reviewer Audit 2 | 8 |
| 12-14 | Verification completeness | Reviewer Audit 3 | 6 |
| 15 | Pre-flight document exists | Process | 1 |
| 16 | Reference experiments read before coding | Process | 1 |
| 17 | No hard-stop violations during execution | Process | 2 |
| 18 | Commit gate matrix filled with specific evidence | Process | 1 |
| 19 | No retry-on-failure behavior | Process | 1 |
| 20 | Clean worktree state on completion | Process | 1 |
| **Total** | | | **35** |

Process checks (15-20) are scored by the orchestrator, not the reviewer.
