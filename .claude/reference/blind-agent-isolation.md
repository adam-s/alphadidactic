# Blind Agent Isolation

How to ensure experiment agents cannot access reference experiments. This is critical for the validation phase: if a blind agent can peek at the answer, it copies instead of proving the instructions are sufficient.

---

## The Problem

Experiment agents must work from `.claude/` rules and `shared/` infrastructure only. But three things leak information:

1. **CLAUDE.md is auto-injected** into all sub-agents. It mentions `reference_experiments/` in the repo structure. Cannot be prevented — it's how Claude Code loads project instructions.

2. **Grep/Glob traverse the entire repo.** A `Grep(pattern="MOM_WINDOW")` with no explicit path searches everything, including `reference_experiments/`. The results contain file paths and content from reference experiments.

3. **Worktree isolation is broken** (as of March 2025):
   - `worktree.sparsePaths` is silently ignored for `Agent(isolation: "worktree")` — [anthropics/claude-code#35149](https://github.com/anthropics/claude-code/issues/35149)
   - `isolation: "worktree"` has no effect for team agents — [anthropics/claude-code#33045](https://github.com/anthropics/claude-code/issues/33045)
   - Edit/Read tools may resolve to main workspace paths instead of worktree — [anthropics/claude-code#36182](https://github.com/anthropics/claude-code/issues/36182)

4. **Worktrees are created inside the repo.** `Agent(isolation: "worktree")` creates worktrees in `.claude/worktrees/`, not `/tmp/`. The agent can traverse up (`../..`) to access the main repo. There is no setting to change the worktree location. This means worktree isolation provides git branch isolation only — NOT filesystem isolation.

---

## The Solution: Physical Separation

**Move `reference_experiments/` out of the repo before launching blind agents.** This is the only approach that provides structural isolation against all access patterns (Read, Grep, Glob, Bash).

### Before blind agent runs

```bash
# Move references out of the repo to /tmp/
mv reference_experiments/ /tmp/claudodidact_references/

# Verify they're gone
ls reference_experiments/ 2>&1  # should fail
```

### After blind agent runs (for reviewer/adversary)

```bash
# Move them back
mv /tmp/claudodidact_references/ reference_experiments/
```

### Automated (in the orchestrator)

```python
import shutil, os

REPO = "/Users/adamsohn/Projects/claudodidact"
REF_SRC = os.path.join(REPO, "reference_experiments")
REF_TMP = "/tmp/claudodidact_references"

def hide_references():
    """Move references out before launching experiment agent."""
    if os.path.exists(REF_SRC):
        shutil.move(REF_SRC, REF_TMP)

def restore_references():
    """Move references back for reviewer/adversary."""
    if os.path.exists(REF_TMP):
        shutil.move(REF_TMP, REF_SRC)
```

---

## Defense in Depth (Supplementary)

Physical separation is the primary defense. These are supplementary layers:

### Layer 1: PreToolUse hook (`.claude/hooks/block-reference-access.sh`)

Blocks Read/Glob/Grep/Bash when the tool input string contains `reference_experiments`. Catches direct path access but NOT indirect access via:
- `Grep(pattern="something", path=".")` — searches reference_experiments/ without the string in the input
- `Glob(pattern="**/*.py")` — returns reference_experiments/ paths without the string in the input

### Layer 2: Behavioral (agent instructions)

The experiment agent definition says "You CANNOT read reference_experiments/." This is the weakest layer — the agent follows it when it knows about the restriction, but CLAUDE.md (which is auto-injected) tells it reference_experiments exists.

### Layer 3: worktree.sparsePaths (BROKEN — do not rely on)

Configured in `.claude/settings.json` but silently ignored for `Agent(isolation: "worktree")` per [#35149](https://github.com/anthropics/claude-code/issues/35149). May work in future Claude Code versions. Check the issue status before relying on it.

---

## Why Not Just Use Hooks?

We tested this empirically:

1. **Test 1** (hook only, experiment output exists): Agent found `experiments/04_residual_momentum/` instead of references. Appeared to work but actually never tried the reference path.

2. **Test 2** (hook only, experiment output deleted): Agent used `Grep` to search the whole repo, found reference content, and reported correct answers from `reference_experiments/04_residual_momentum/run_strategy.py`. Hook didn't fire because the Grep input didn't contain "reference_experiments" — only the results did.

**Conclusion:** Hooks operate on tool INPUT, not tool OUTPUT. Any search tool that traverses the filesystem will find reference_experiments/ content regardless of hooks. Physical separation is the only reliable approach.

---

## Verification

After hiding references, run this test:

```
Agent(prompt="Search the codebase for MOM_WINDOW and BETA_WINDOW. Report exact values and file locations.")
```

**Expected:** Agent finds nothing (or only finds values in `.claude/` docs if mentioned there).
**If it finds reference values:** References were not properly hidden.
