"""
Kernel signal algorithm — self-contained, no external imports.

Consolidated from:
  - spy_flow_spreads/14_kernel_signal/algorithm.py (kernel correlation, backtest)
  - spy_flow_spreads/15_portfolio/algorithm.py (paranoid backtest, combine_legs)
  - spy_flow_spreads/16_intraday_exit/algorithm.py (intraday return series)

All config values are passed as parameters — no `from config import ...`.
"""
import numpy as np
import pandas as pd
from scipy.stats import rankdata

import sys
from pathlib import Path

# Import metrics from sibling module
sys.path.insert(0, str(Path(__file__).resolve().parent))
from metrics import calc_metrics  # noqa: E402


# ============================================================================
# Signal Construction (from 14_kernel_signal)
# ============================================================================


def zscore_series(s: pd.Series, lookback: int, min_periods: int = 30) -> pd.Series:
    """Rolling z-score: (x - rolling_mean) / rolling_std.

    BACKWARD-LOOKING: At each index i, uses only values [i-lookback+1 : i].
    """
    mean = s.rolling(lookback, min_periods=min_periods).mean()
    std = s.rolling(lookback, min_periods=min_periods).std()
    return (s - mean) / std.replace(0, np.nan)


def apply_ema(series: pd.Series, ema_span: int) -> pd.Series:
    """Exponential moving average smoothing.

    BACKWARD-LOOKING: EMA[T] = alpha * x[T] + (1-alpha) * EMA[T-1].
    """
    return series.ewm(span=ema_span, min_periods=max(3, ema_span // 2)).mean()


def build_features(
    flows: pd.DataFrame,
    feature_defs: list[tuple],
    zscore_lookback: int,
    zscore_min_periods: int = 30,
) -> dict[str, pd.Series]:
    """Extract z-scored features from raw options flow data.

    For each feature in feature_defs:
        1. Filter flows by symbol/dte_bucket/size_bucket
        2. Sum flow_column by trade_date
        3. Zero-fill missing days
        4. Apply rolling z-score (backward-looking)

    Args:
        flows: Flow DataFrame with columns: symbol, trade_date, dte_bucket,
            size_bucket, and the flow columns referenced in feature_defs.
        feature_defs: List of tuples: (name, symbol, size_buckets, dte_bucket, flow_column)
        zscore_lookback: Rolling window for z-score.
        zscore_min_periods: Minimum observations before z-score produces values.
    """
    qqq = flows[flows["symbol"] == "QQQ"].copy()
    dates = qqq["trade_date"].sort_values().unique()

    features = {}
    for name, _symbol, sizes, dte, flow_col in feature_defs:
        mask = qqq["dte_bucket"] == dte
        if sizes is not None:
            mask &= qqq["size_bucket"].isin(sizes)
        raw = qqq[mask].groupby("trade_date")[flow_col].sum()
        raw = raw.reindex(dates, fill_value=0.0)
        features[name] = zscore_series(raw, zscore_lookback, zscore_min_periods)

    return features


# ============================================================================
# Kernel Correlation (from 14_kernel_signal)
# ============================================================================


def weighted_pearson(x: np.ndarray, y: np.ndarray, w: np.ndarray) -> float:
    """Weighted Pearson correlation."""
    w_sum = w.sum()
    if w_sum == 0:
        return np.nan

    mx = np.average(x, weights=w)
    my = np.average(y, weights=w)

    dx = x - mx
    dy = y - my

    cov_xy = np.sum(w * dx * dy) / w_sum
    var_x = np.sum(w * dx * dx) / w_sum
    var_y = np.sum(w * dy * dy) / w_sum

    denom = np.sqrt(var_x * var_y)
    if denom < 1e-15:
        return np.nan

    return cov_xy / denom


def kernel_spearman_series(
    flow: np.ndarray,
    return_pct: np.ndarray,
    half_life: int,
    min_obs: int = 60,
) -> np.ndarray:
    """Expanding exponential-kernel weighted Spearman correlation.

    At each time T, computes kernel-weighted Spearman between:
      flow[0..T-1] and return_pct[0..T-1]

    BACKWARD-LOOKING: At time T, only uses data available at T close.
    """
    n = len(flow)
    assert len(return_pct) == n

    result = np.full(n, np.nan)
    decay = np.log(2) / half_life

    for T in range(min_obs, n):
        x = flow[:T]
        y = return_pct[:T]

        mask = np.isfinite(x) & np.isfinite(y)
        n_valid = mask.sum()
        if n_valid < min_obs:
            continue

        x_valid = x[mask]
        y_valid = y[mask]

        valid_indices = np.where(mask)[0]
        ages = (T - 1) - valid_indices
        w = np.exp(-decay * ages)

        rx = rankdata(x_valid)
        ry = rankdata(y_valid)

        result[T] = weighted_pearson(rx, ry, w)

    return result


def kernel_composite_signal(
    features: dict[str, pd.Series],
    return_pct: pd.Series,
    half_life: int,
    ema_span: int,
    feature_names: list[str],
    coeffs: dict[str, float] | None = None,
    min_corr: float | dict[str, float] = 0.02,
    dissent_dampen: float = 1.0,
    kernel_min_obs: int = 60,
) -> tuple[pd.Series, dict[str, pd.Series]]:
    """Build adaptive composite signal using kernel correlations.

    BACKWARD-LOOKING: kernel correlations, z-scored features, and EMA are all causal.
    """
    if coeffs is None:
        coeffs = {name: 1.0 for name in feature_names}

    if isinstance(min_corr, (int, float)):
        _min_corr = {name: float(min_corr) for name in feature_names}
    else:
        _min_corr = min_corr

    common_dates = features[feature_names[0]].index
    for name in feature_names[1:]:
        common_dates = common_dates.intersection(features[name].index)
    common_dates = common_dates.intersection(return_pct.index).sort_values()

    ret_arr = return_pct.reindex(common_dates).values

    feat_arrays = {}
    for name in feature_names:
        feat_arrays[name] = features[name].reindex(common_dates).values

    kernel_corrs = {}
    kernel_arrays = {}
    for name in feature_names:
        kc = kernel_spearman_series(feat_arrays[name], ret_arr, half_life, min_obs=kernel_min_obs)
        kernel_corrs[name] = pd.Series(kc, index=common_dates, name=name)
        kernel_arrays[name] = kc

    n = len(common_dates)
    raw_signal = np.full(n, np.nan)

    for T in range(n):
        active = []
        for name in feature_names:
            kc = kernel_arrays[name][T]
            if not np.isfinite(kc) or abs(kc) < _min_corr.get(name, 0.02):
                continue

            c = coeffs.get(name, 1.0)
            if c < 0.01:
                continue

            feat_val = feat_arrays[name][T]
            if not np.isfinite(feat_val):
                continue

            weight = c * kc
            kc_sign = 1 if kc > 0 else -1
            active.append((kc_sign, weight * feat_val))

        if len(active) < 1:
            continue

        if dissent_dampen < 1.0 and len(active) >= 3:
            n_pos = sum(1 for sign, _ in active if sign > 0)
            n_neg = len(active) - n_pos

            if n_pos != n_neg:
                minority_sign = 1 if n_pos < n_neg else -1
                components = []
                for sign, comp in active:
                    if sign == minority_sign:
                        components.append(comp * dissent_dampen)
                    else:
                        components.append(comp)
            else:
                components = [comp for _, comp in active]
        else:
            components = [comp for _, comp in active]

        raw_signal[T] = sum(components) / len(active)

    raw_series = pd.Series(raw_signal, index=common_dates)
    signal = apply_ema(raw_series, ema_span)

    return signal, kernel_corrs


# ============================================================================
# Backtest Engine (from 14_kernel_signal)
# ============================================================================


def run_backtest(
    long_prices: pd.DataFrame,
    inv_prices: pd.DataFrame,
    signal_series: pd.Series,
    risk_fraction: float,
    initial_capital: float = 100_000,
    magnitude_sizing: bool = False,
) -> tuple[pd.DataFrame, list[dict]]:
    """Execute the QQQ/PSQ allocation backtest.

    signal > 0 -> QQQ (long), signal < 0 -> PSQ (inverse), 0 -> flat.
    """
    long_df = long_prices.sort_values("trade_date").reset_index(drop=True)
    inv_df = inv_prices.sort_values("trade_date").reset_index(drop=True)

    long_by_date = {
        row["trade_date"]: {"price": row["price"], "return_pct": row["return_pct"]}
        for _, row in long_df.iterrows()
    }
    inv_by_date = {
        row["trade_date"]: {"price": row["price"], "return_pct": row["return_pct"]}
        for _, row in inv_df.iterrows()
    }

    sig_by_date = {}
    for dt, val in signal_series.items():
        v = float(val)
        if np.isfinite(v):
            sig_by_date[pd.Timestamp(dt).strftime("%Y-%m-%d")] = v

    confidence_by_date = {}
    if magnitude_sizing:
        sig_abs = signal_series.abs()
        rolling_med = sig_abs.rolling(60, min_periods=10).median()
        for dt in signal_series.index:
            date_str = pd.Timestamp(dt).strftime("%Y-%m-%d")
            sv = sig_by_date.get(date_str, 0.0)
            if sv == 0.0:
                continue
            med = rolling_med.get(dt, np.nan)
            if np.isfinite(med) and med > 1e-10:
                raw_conf = abs(sv) / med
                confidence_by_date[date_str] = min(max(raw_conf, 0.1), 1.0)
            else:
                confidence_by_date[date_str] = 1.0

    dates = sorted(long_by_date.keys())
    capital = float(initial_capital)
    daily = []
    trades = []

    for date in dates:
        date_str = pd.Timestamp(date).strftime("%Y-%m-%d")
        long_d = long_by_date[date]
        inv_d = inv_by_date.get(date)

        sig_val = sig_by_date.get(date_str, 0.0)

        if sig_val == 0.0:
            daily.append({
                "date": date, "value": capital, "signal": sig_val,
                "position": "flat", "allocation_pct": 0,
            })
            continue

        if magnitude_sizing:
            confidence = confidence_by_date.get(date_str, 1.0)
            allocation = capital * risk_fraction * confidence
        else:
            confidence = 1.0
            allocation = capital * risk_fraction

        if sig_val > 0:
            instrument = "QQQ"
            ret_pct = long_d["return_pct"]
        else:
            instrument = "PSQ"
            ret_pct = inv_d["return_pct"] if inv_d else None

        if ret_pct is not None and np.isfinite(ret_pct):
            pnl = allocation * ret_pct / 100.0
            capital += pnl
            trades.append({
                "entry_date": date_str,
                "instrument": instrument,
                "signal": sig_val,
                "confidence": confidence,
                "allocation": allocation,
                "allocation_pct": risk_fraction * confidence,
                "return_pct": ret_pct,
                "pnl": pnl,
                "capital_after": capital,
            })

        daily.append({
            "date": date, "value": capital, "signal": sig_val,
            "position": instrument.lower(), "allocation_pct": risk_fraction,
        })

    return pd.DataFrame(daily), trades


def evaluate(
    daily_df: pd.DataFrame,
    trades: list[dict],
    initial_capital: float = 100_000,
    period_start: str = "2022-01-18",
    period_end: str = "2026-03-10",
) -> dict:
    """Compute full evaluation metrics for a backtest run."""
    m = calc_metrics(daily_df, initial_capital=initial_capital,
                     period_start=period_start, period_end=period_end)
    n = len(trades)
    wins = sum(1 for t in trades if t["pnl"] > 0)
    gp = sum(t["pnl"] for t in trades if t["pnl"] > 0)
    gl = abs(sum(t["pnl"] for t in trades if t["pnl"] <= 0))
    return {
        **m,
        "num_trades": n,
        "win_rate": wins / n if n > 0 else 0,
        "profit_factor": gp / gl if gl > 0 else float("inf"),
        "total_pnl": sum(t["pnl"] for t in trades),
    }


# ============================================================================
# Growth Leg: Kernel 3c Paranoid Exit (from 15_portfolio)
# ============================================================================


def generate_kernel3c_signals(qqq_price_series, kernel_signal):
    """EMA-50 exit + kernel-gated re-entry.

    Exit: close < EMA-50. Re-entry: close > EMA-50 AND kernel >= 0.
    BACKWARD-LOOKING.
    """
    ema50 = qqq_price_series.ewm(span=50, min_periods=50).mean()

    exit_signal = {}
    reentry_signal = {}
    for date in qqq_price_series.index:
        ds = pd.Timestamp(date).strftime("%Y-%m-%d")
        if pd.isna(ema50[date]):
            continue
        price_below = bool(qqq_price_series[date] < ema50[date])
        price_above = bool(qqq_price_series[date] > ema50[date])

        exit_signal[ds] = price_below

        ks = np.nan
        if date in kernel_signal.index:
            ks = kernel_signal[date]
        reentry_signal[ds] = price_above and np.isfinite(ks) and ks >= 0

    return exit_signal, reentry_signal


def run_paranoid_backtest(
    qqq_prices, exit_signal, reentry_signal,
    risk_fraction=1.0, initial_capital=100_000,
):
    """Stateful paranoid-exit backtest.

    State machine: LONG <-> CASH.
    Initial state: LONG. Exit on close < EMA-50.
    Re-enter when price > EMA-50 AND kernel >= 0.
    """
    qqq_df = qqq_prices.sort_values("trade_date").reset_index(drop=True)
    in_position = True
    capital = float(initial_capital)
    daily = []
    trades = []

    for _, row in qqq_df.iterrows():
        date = row["trade_date"]
        date_str = pd.Timestamp(date).strftime("%Y-%m-%d")
        ret_pct = row["return_pct"]
        exit_today = exit_signal.get(date_str, False)
        reentry_today = reentry_signal.get(date_str, False)

        if in_position:
            if exit_today:
                in_position = False
                daily.append({"date": date, "value": capital, "position": "cash",
                              "action": "exit"})
                continue
            allocation = capital * risk_fraction
            if ret_pct is not None and np.isfinite(ret_pct):
                pnl = allocation * ret_pct / 100.0
                capital += pnl
                trades.append({"entry_date": date_str, "instrument": "QQQ",
                               "pnl": pnl, "return_pct": ret_pct})
            daily.append({"date": date, "value": capital, "position": "qqq",
                          "action": "hold"})
        else:
            if reentry_today:
                in_position = True
                allocation = capital * risk_fraction
                if ret_pct is not None and np.isfinite(ret_pct):
                    pnl = allocation * ret_pct / 100.0
                    capital += pnl
                    trades.append({"entry_date": date_str, "instrument": "QQQ",
                                   "pnl": pnl, "return_pct": ret_pct})
                daily.append({"date": date, "value": capital, "position": "qqq",
                              "action": "reentry"})
            else:
                daily.append({"date": date, "value": capital, "position": "cash",
                              "action": "wait"})

    return pd.DataFrame(daily), trades


# ============================================================================
# Portfolio Combination (from 15_portfolio)
# ============================================================================


def combine_legs(growth_daily, hedge_daily, growth_pct, hedge_pct, initial_capital):
    """Combine two daily equity series into a portfolio.

    Each leg grows independently. No rebalancing — allocations drift.
    """
    g = growth_daily.copy()
    g["date"] = pd.to_datetime(g["date"])
    g = g.sort_values("date").reset_index(drop=True)

    h = hedge_daily.copy()
    h["date"] = pd.to_datetime(h["date"])
    h = h.sort_values("date").reset_index(drop=True)

    g_start = g["value"].iloc[0]
    h_start = h["value"].iloc[0]

    merged = pd.merge(
        g[["date", "value"]].rename(columns={"value": "growth_val"}),
        h[["date", "value"]].rename(columns={"value": "hedge_val"}),
        on="date", how="inner",
    )

    growth_alloc = initial_capital * growth_pct
    hedge_alloc = initial_capital * hedge_pct

    merged["growth_scaled"] = growth_alloc * (merged["growth_val"] / g_start)
    merged["hedge_scaled"] = hedge_alloc * (merged["hedge_val"] / h_start)
    merged["value"] = merged["growth_scaled"] + merged["hedge_scaled"]

    if "position" in g.columns:
        pos_map = dict(zip(g["date"], g["position"]))
        merged["growth_position"] = merged["date"].map(pos_map).fillna("unknown")
    if "position" in h.columns:
        pos_map_h = dict(zip(h["date"], h["position"]))
        merged["hedge_position"] = merged["date"].map(pos_map_h).fillna("unknown")
    if "signal" in h.columns:
        sig_map = dict(zip(h["date"], h["signal"]))
        merged["hedge_signal"] = merged["date"].map(sig_map).fillna(0.0)

    return merged


def eval_combined(
    combined_daily, initial_capital,
    start_date="2022-01-18", end_date="2026-03-10",
    train_end="2024-12-31", test_start="2025-01-02",
):
    """Evaluate a combined portfolio over train/test/full periods."""
    results = {}

    trades = []
    for i in range(1, len(combined_daily)):
        pnl = combined_daily.iloc[i]["value"] - combined_daily.iloc[i - 1]["value"]
        trades.append({
            "entry_date": str(combined_daily.iloc[i]["date"]),
            "pnl": pnl,
            "instrument": "portfolio",
            "return_pct": pnl / combined_daily.iloc[i - 1]["value"] * 100,
        })

    daily = combined_daily[["date", "value"]].copy()

    results["full"] = evaluate(daily, trades, initial_capital,
                               period_start=start_date, period_end=end_date)

    train_mask = pd.to_datetime(daily["date"]) <= pd.Timestamp(train_end)
    daily_train = daily[train_mask]
    train_trades = [t for t in trades if t["entry_date"] <= train_end]
    results["train"] = evaluate(daily_train, train_trades, initial_capital,
                                period_start=start_date, period_end=train_end)

    test_mask = pd.to_datetime(daily["date"]) >= pd.Timestamp(test_start)
    daily_test = daily[test_mask]
    test_trades = [t for t in trades if t["entry_date"] >= test_start]
    test_cap = float(daily_train.iloc[-1]["value"]) if len(daily_train) > 0 else initial_capital
    results["test"] = evaluate(daily_test, test_trades, test_cap,
                               period_start=test_start, period_end=end_date)

    return results


def apply_leverage(combined_daily, leverage, initial_capital):
    """Apply leverage to daily portfolio returns. No borrowing cost modeled."""
    vals = combined_daily["value"].values.copy()
    levered = [float(initial_capital)]
    for i in range(1, len(vals)):
        daily_ret = (vals[i] - vals[i - 1]) / vals[i - 1]
        levered.append(levered[-1] * (1 + leverage * daily_ret))
    out = combined_daily.copy()
    out["value"] = levered
    return out
