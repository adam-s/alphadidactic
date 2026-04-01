# Causal Signal Research

> **MANDATORY: Do NOT use MEMORY.md in this project.** All persistent instructions live in `.claude/` files.
>
> **MANDATORY: Monitor agents with sleep-then-read.** `sleep 180 && python -m shared.apc read <channel> --new`. Repeat every 3 min until COMPLETE/ERROR. Max 10 polls per agent. Do NOT use `run_in_background` for monitoring.
>
> **MANDATORY: No deprecation.** Delete code you don't need. Update every caller.

**We are building `.claude/` — an instruction set that creates experiments. We are NOT building experiments.**

Experiments are test harnesses that expose gaps in the instruction set. Run → Review → Attack → Patch `.claude/` → Re-run same experiment. Score trajectory on a fixed experiment measures progress.

**The goal is to find bugs first and find alpha second.** 99/100 positive results are caused by a bug. The bugs don't crash — they produce beautiful equity curves that are lies.

---

## Core Principles

- **Generalize, never specialize.** No specific symbols, thresholds, or experiment names in `.claude/`.
- **Architectural constraints > instructions.** CursorEngine, settle_price_fallback(), pending-row pattern prevent bugs by construction.
- **Silent failure is the default.** Internal consistency masks external incorrectness.
- **Every change must prove equivalence.** Exact match to 8+ decimal places on 20+ test cases.
- **Bugs suppress signal as often as they inflate it.** Null result may mean broken implementation.

---

## How `.claude/` Works

- **`rules/`** — Always loaded. Keep lean.
- **`skills/`** — Metadata loaded; body on invocation.
- **`reference/`** — Never auto-loaded. Looked up on demand.
- **`agents/`** — System prompt when spawned.

All three agents (experiment, reviewer, adversary) run every cycle. See `reference/project-architecture.md`.

---

## Instruction Audit (before committing `.claude/` changes)

0. **Patch discipline:** Check which files the failing agent read. Update an existing instruction in one of those files to cover the new case. Add a new instruction only if no existing one applies. Prefer equal or fewer words.
1. **Generalize:** `grep -rni 'FRED\|T10Y2Y\|flow_cache\|earnings_releases\|options_trades' .claude/rules/*.md .claude/agents/*.md .claude/CLAUDE.md` — any hit is too specific.
2. **Contradictions:** Two files saying different things → delete one.
3. **Duplication:** `for c in "C-exit" "pending-row" "settle_price_fallback"; do n=$(grep -rl "$c" .claude/ --include='*.md' | wc -l); [ "$n" -gt 2 ] && echo "DRIFT: $c in $n files"; done`
4. **Broken references:** `grep -rh 'reference/.*\.md' .claude/ --include='*.md' | grep -oE 'reference/[a-zA-Z/_-]+\.md' | sort -u | while read f; do [ ! -f ".claude/$f" ] && echo "BROKEN: $f"; done`
5. **Size:** `wc -c .claude/rules/*.md .claude/agents/*.md .claude/CLAUDE.md` — target ≤ 40KB.
