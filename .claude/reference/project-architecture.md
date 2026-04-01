# Project Architecture & Iteration Loop

Detailed architecture, roles, execution patterns, and instruction maintenance rules. Read this when onboarding, running the iteration loop, or patching instructions.

---

## Self-Improving Loop

Four distinct roles in the iteration pipeline:

1. **Experiment agent** — BLIND. Gets only `.claude/` rules + `shared/` infrastructure + a hypothesis. Cannot access `reference_experiments/` or `docs/`. Builds the experiment.

2. **Reviewer agent** — INFORMED. Scores against the 28-point rubric (known bug classes). Compares quality to reference experiments. Proposes instruction improvements.

3. **Adversary agent** — HOSTILE. Tries to break the experiment using everything the model knows. Finds bugs the rubric doesn't cover. If it can't break it, that's the highest endorsement.

4. **Orchestrator** (human + Claude in main chat) — FULL ACCESS. Patches instructions and infrastructure from reviewer + adversary findings. **The orchestrator is self-referencing:** sub-agents are clones of `.claude/` — updating the orchestrator's instructions IS updating the clones. Every commit propagates to the next agent born from HEAD.

**The iteration cycle:** experiment → reviewer → adversary → patch → repeat. Each iteration updates both **instructions** (`.claude/`) and **infrastructure** (`shared/`). Convergence = the adversary can't break it.

**Bloat reduction is the highest-value patch.** The best `.claude/` change is removing an instruction without affecting any experiment's behavior. Fewer words = less token cost per agent = more context for reasoning. After each iteration, look for instructions that are (a) duplicated across files, (b) implied by architectural constraints that already enforce them, or (c) never triggered by any experiment. Delete them.

Track iteration progress in `docs/curriculum-state.md`. Git log reconstructs the full instruction patch history.

---

## Execution Patterns

- **Progress feedback:** Experiment agents write `PROGRESS.md` in their experiment directory after each pipeline step.
- **Parallelism:** Up to 4 agents concurrently using `isolation: "worktree"`. Matches the 4 DB connection limit.
- **Model selection:** Use `opus` for experiment agents (protocol-following, faster). Use `opus` for reviewers (judgment, comparison).
- **System monitoring:** See `docs/orchestrator-process.md` § "Background Agents + APC Monitoring" for the full protocol.
- **Test small first:** Every experiment tests on 5 trading days before expanding to the full date range.

---

## Exploration History

The reference experiments were developed through 195 iterations. The explorations in `docs/explorations/` document the painful discoveries — especially explorations 31-35:

- **31: Timezone look-ahead** — single-step timezone cast silently shifted all prices. Sharpe +2.6 → worthless.
- **32: DISTINCT ON mirage** — TimescaleDB chunk boundaries caused random row selection. 91.5% of prices wrong.
- **33: The return that already happened** — same-day signal/return pairing. TSLA +7,764% → +177%.
- **34: Wall-clock model** — percentile gates computed from data that included the value being gated.
- **35: The audit that lied** — simplified verification code inherited the bug it was checking for.

See `reference/bug-catalog.md` for the distilled patterns.

---

## Instruction Maintenance

After EVERY iteration cycle, commit instruction patches before starting the next cycle. Before committing:

1. **Always generalize.** Every instruction must be a general principle, not a specific fix. Specific cases may illustrate but must never BE the rule.
2. **Prune.** If the same concept appears in 3+ files, consolidate to ONE authoritative file. Each rule has ONE home:
   - Database rules → `rules/database-safety.md`
   - Temporal rules → `rules/temporal-correctness.md`
   - Accounting rules → `rules/accounting-correctness.md`
   - Patterns → `reference/` files
   - Agent behavior → `agents/` files
   - Pipeline steps → `rules/experiment-checks.md`
3. **Resolve contradictions.** Read all affected files after patching.
4. **Keep it portable.** Everything must live in the repo, not in memory or local config.
5. **Minimize token cost.** `rules/` files load on every turn for every agent. Be concise. Use `paths` frontmatter where possible. Move detailed examples and procedures to `reference/` or skill supporting files.
