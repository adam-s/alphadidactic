# Wall-Clock Model

## Purpose

TemporalGuard checks individual data accesses but not structural relationships between computations. The wall-clock model forces you to diagram every data access against real wall-clock time, revealing pairing errors that pass point-in-time checks.

Every experiment must produce a wall-clock diagram before results are trusted. This is STEP 4 of the experiment protocol.

---

## Template

Adapt phase names, times, and data sources to your strategy's market and trading window. The template below uses U.S. equities (ET timezone, 09:30-16:00 session) as an example, but the structure applies to any market, timezone, or frequency.

```markdown
## Wall-Clock Diagram: [experiment name]

### Data Sources
| Source | Granularity | Timezone in DB | Conversion Required |
|--------|------------|----------------|---------------------|
| [table name] | [frequency] | [stored TZ] | [conversion method] |

### [Phase 1 Name] ([time] [timezone])
| Data Access | Expression | Available Since | Used For |
|-------------|-----------|----------------|----------|
| [variable] | [how computed] | [concrete wall-clock time + timezone] | [which computation uses it] |

### [Phase 2 Name] ([time] [timezone])
| Data Access | Expression | Available Since | Used For |
|-------------|-----------|----------------|----------|
| [variable] | [how computed] | [concrete wall-clock time + timezone] | [which computation uses it] |

### Decision -> Settlement Flow
| Decision Made At | Based On Data Available At | Settlement Time | Return Earned | Causal? |
|-----------------|---------------------------|----------------|--------------|---------|
| 16:00 ET day T | <= 16:00 ET day T | 09:35 ET day T+1 | overnight | YES |

### Causality Proof
For each row in Decision -> Settlement Flow:
- [ ] All "Based On Data Available At" times < "Decision Made At" time
- [ ] "Decision Made At" time < "Settlement Time"
- [ ] No data from after "Decision Made At" is used in the decision
```

---

## Rules

1. **Every data access in the experiment must appear in the diagram.** If code reads a value, it must have a row in one of the phase tables.

2. **"Available Since" must be a concrete wall-clock time**, not "when computed" or "at query time." The question is: when did reality make this number knowable?

3. **External data has publication lag.** Any data source with a release delay (macro releases, earnings, regulatory filings, news) must model when the data was available, not when it was observed. Using data[T] on day T when publication lag means it wasn't released until T+1 is a lookahead violation even though the date says T.

4. **The Decision -> Settlement Flow must show causality.** Every decision must be based on data available before the decision time, and settlement must occur after the decision time.

5. **Gates and filters are data accesses too.** If a percentile gate uses a rolling window, the window's data must all be available before the gate is evaluated.

---

## Common Violations

### Value included in its own standardization window (R1 — self-reference)
```
BAD: z_score[T] = (value[T] - mean(values[0:T+1])) / std(values[0:T+1])
     # value[T] pulls the mean and std toward itself, biasing the score toward 0
OK:  z_score[T] = (value[T] - mean(values[0:T])) / std(values[0:T])
     # score computed against strictly prior values
```
This applies to z-scores, percentile ranks, and any standardization where the value being scored is included in the reference distribution. Note: using the same minute-bar price for both signal computation and trade execution is fine — the issue is only when a value biases the statistics used to evaluate itself.

### Return settles before decision (A3 — temporal inversion)
```
BAD: decision at time D, return settled at time S where S < D
OK:  decision at time D, return settled at time S where S > D
```
The specific times depend on your market and strategy. For U.S. overnight strategies, the classic inversion is decision at 16:00 ET earning a return that settled at 09:35 ET the same day.

### External data used before publication (publication lag)
```
BAD: external_data[T] used in decision on day T (not yet published)
OK:  external_data[T] used after available_at(T)
```
This applies to any data source with a publication delay: FRED macro releases (typically T+1 business day), earnings announcements, regulatory filings, news sentiment. Model availability forward from observation date, not backward from decision date.

---

## Example: Overnight Mean-Reversion

```markdown
## Wall-Clock Diagram: Overnight Mean-Reversion (SPY)

### Data Sources
| Source | Granularity | Timezone in DB | Conversion Required |
|--------|------------|----------------|---------------------|
| minute_bars | 1-minute | UTC | Yes: dual AT TIME ZONE |

### Morning Phase (09:35 ET)
| Data Access | Expression | Available Since | Used For |
|-------------|-----------|----------------|----------|
| p_open[T] | SPY open at 09:35 ET | 09:35 ET day T | overnight return: p_open[T]/p_close[T-1] - 1 |

### Evening Phase (16:00 ET)
| Data Access | Expression | Available Since | Used For |
|-------------|-----------|----------------|----------|
| p_close[T] | SPY close at 16:00 ET | 16:00 ET day T | EMA input, overnight return denom for T+1 |
| ema_20[T] | EMA(20) of p_close up to T | 16:00 ET day T | mean-reversion signal |
| signal[T] | p_close[T] < ema_20[T] | 16:00 ET day T | go long overnight if below EMA |

### Decision -> Settlement Flow
| Decision Made At | Based On Data Available At | Settlement Time | Return Earned | Causal? |
|-----------------|---------------------------|----------------|--------------|---------|
| 16:00 ET day T | 16:00 ET day T (p_close, ema) | 09:35 ET day T+1 | p_open[T+1]/p_close[T] - 1 | YES |

### Causality Proof
- [x] p_close[T] available at 16:00 ET day T < decision at 16:00 ET day T ✓
- [x] ema_20[T] uses only p_close[0..T], all available by 16:00 ET day T ✓
- [x] Decision at 16:00 ET day T < Settlement at 09:35 ET day T+1 ✓
- [x] No data from after 16:00 ET day T used in decision ✓
```
