# 12 Dynamic Universe — Temporal Proof

## Wall-Clock Diagram

```
Day T (entry day):
  09:30 ─── Market opens
  09:35 ─── p0935: Settle previous overnight position (if any)
           ├── Accumulator update: prev_p1530[sym] → p0935[sym] overnight return
           └── (observable: prev close from yesterday, today's open)

  15:30 ─── p1530: Entry decision
           ├── Liquidity ranking: trailing 60-day activity × avg_price (all past)
           ├── Eligible set: top-N by liquidity score
           ├── Signal: iret(p0935→p1530) × avg_pos × (1+0.75×streak) × hit_rate
           ├── Regime gate: MacroRegime(today) must be "bull"
           ├── Percentile gate: signal ≥ 50th pctile of past 252 signals
           └── ENTER: pending = (top-1 symbol, p1530 price, today)

  16:00 ─── p1600: SPY B&H benchmark only (not used for trading)

Day T+1 (settlement):
  09:35 ─── p0935: Settle overnight position
           ├── exit_price = p0935[pending_symbol]
           ├── If missing: settle_price_fallback (earlier-first, then forward)
           └── day_ret = exit_price / entry_price - 1 - 2×TC
```

## Temporal Audit Table

| # | Data Access | Available At (ET) | Used At (ET) | Causal? | Evidence |
|---|------------|-------------------|-------------|---------|----------|
| 1 | prev_p1530 (yesterday's 15:30 close) | T-1 15:30 | T 09:35 | Y | Stored in prev_p1530 dict at end of T-1 |
| 2 | p0935 (today's open) | T 09:35 | T 09:35 | Y | CachedPhasedDay.resolve_up_to(09:35) |
| 3 | Accumulator update (overnight return) | T 09:35 | T 09:35 | Y | Uses prev_p1530 (T-1) and p0935 (T), both available |
| 4 | Settlement of pending position | T 09:35 | T 09:35 | Y | pending set at T-1 15:30, settled at T 09:35 |
| 5 | Activity tracking (has_prices) | T 15:30 | T 15:30 | Y | Uses p0935 + p1530 from today, both available |
| 6 | Price level tracking | T 15:30 | T 15:30 | Y | Uses p1530 from today |
| 7 | Liquidity ranking | T 15:30 | T 15:30 | Y | Uses trailing 60-day deques (all past) |
| 8 | iret (intraday return) | T 15:30 | T 15:30 | Y | p1530/p0935 - 1, both available at 15:30 |
| 9 | Signal computation | T 15:30 | T 15:30 | Y | All inputs (accumulator, iret) available |
| 10 | Regime gate | T 15:30 | T 15:30 | Y | MacroRegime uses only data up to T |
| 11 | Percentile gate | T 15:30 | T 15:30 | Y | signal_history contains only past signals |
| 12 | Entry price (p1530) | T 15:30 | T 15:30 | Y | Entry at decision-time price |

All rows Causal = Y.

## C-Class Accounting Checklist

| Check | Status | Evidence |
|-------|--------|----------|
| C2 | PASS | Single position, no leverage. `pending` is scalar. |
| C5 | PASS | `equity *= (1 + day_ret)` — multiplicative |
| C-exit | PASS | `settle_price_fallback()` called when p0935 is None. 1 gap logged to data_gaps.json |
| C-TC | PASS | `TC` from `shared/config.py`. `rr - 2 * TC` per trade (Case 1: nightly turnover) |
| C-split | PASS | Signal: `abs(iret) >= SPLIT_THRESHOLD` filters. Return: `abs(rr) >= SPLIT_THRESHOLD` filters. Accumulator: `abs(r) < SPLIT_THRESHOLD` filters. |
| C-sizing | PASS | Position size from signal ranking at T 15:30 only |

## Results

| Metric | Value |
|--------|-------|
| Train Sharpe | +1.709 |
| Test Sharpe | +1.454 |
| Total Return | +378.3% |
| Max Drawdown | 13.7% |
| Win Rate | 57% |
| Trades | 418 (239W / 179L) |
| Data Gaps | 1 (CMG 2023-04-27, same_day_forward at 09:36) |

## Verification

All 8 checks pass. Check 6 max_delta = 8.00e-11 (tolerance 1e-8).
Statistical robustness tests: see output/stat_tests.json.
