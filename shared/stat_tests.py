"""
stat_tests.py — Statistical robustness tests for experiment validation.

Starter toolkit. Agents should write custom tests when the experiment demands it.
All functions are pure (array in → dict out), no DB access, no file I/O.
Custom tests should follow the same contract: return a dict with at minimum
`test_name`, `result`, `interpretation`, and `pass` keys.

Dependencies: numpy, scipy (both already in the environment).
"""

import numpy as np
from scipy import stats


# ═══════════════════════════════════════════════════════════════════════════════
# BUG DETECTORS (primary purpose)
# ═══════════════════════════════════════════════════════════════════════════════


def permutation_test(
    signal_returns: np.ndarray,
    all_returns: np.ndarray,
    n_perms: int = 1000,
    seed: int = 42,
    tc_per_active_day: float = 0.0,
) -> dict:
    """Shuffle signal dates and check if alpha survives.

    If the signal is real, shuffled versions should produce worse Sharpe.
    If shuffled Sharpe is similar, the 'alpha' is structural bias.

    Args:
        signal_returns: daily return series from the strategy (include 0.0 for flat days)
        all_returns: daily return series for the full period (buy-and-hold benchmark)
        n_perms: number of random permutations
        seed: RNG seed for reproducibility
        tc_per_active_day: transaction cost to deduct from each active day in
            permuted returns (e.g., 2*TC for round-trip). Must match the TC
            applied to signal_returns. If signal_returns already include TC,
            pass the same TC here so permutations are TC-fair. Default 0.0
            for backward compatibility, but callers SHOULD pass their TC.

    Runtime scales as O(n_perms * len(signal_returns)). For a 1000-day strategy
    with 1000 perms, this is sub-second. For strategies with expensive fit steps
    (e.g., HMM refit), shuffle only the signal-to-return mapping, not the full
    strategy recomputation — or reduce n_perms.
    """
    rng = np.random.default_rng(seed)
    sig = np.asarray(signal_returns, dtype=float)
    sig = sig[~np.isnan(sig)]

    if len(sig) < 10:
        return {
            "test_name": "permutation_test",
            "result": None,
            "interpretation": "Too few observations for permutation test",
            "pass": None,
        }

    observed_sharpe = _sharpe(sig)

    # Active days mask: which days the strategy traded (non-zero return)
    active_mask = sig != 0.0
    n_active = int(active_mask.sum())

    all_ret = np.asarray(all_returns, dtype=float)
    all_ret = all_ret[~np.isnan(all_ret)]

    # Permutation: randomly select n_active days from all_returns,
    # rest are 0.0 (flat). This tests whether picking ANY n_active days
    # produces similar results. TC is applied to active days so the
    # comparison is fair (same cost structure as the observed strategy).
    n_days = len(all_ret)
    perm_sharpes = np.empty(n_perms)
    for i in range(n_perms):
        perm_indices = rng.choice(n_days, size=n_active, replace=False)
        perm_rets = np.zeros(n_days)
        perm_rets[perm_indices] = all_ret[perm_indices] - tc_per_active_day
        perm_sharpes[i] = _sharpe(perm_rets)

    p_value = float(np.mean(perm_sharpes >= observed_sharpe))
    perm_mean = float(np.mean(perm_sharpes))
    perm_std = float(np.std(perm_sharpes))

    return {
        "test_name": "permutation_test",
        "observed_sharpe": round(observed_sharpe, 4),
        "perm_mean_sharpe": round(perm_mean, 4),
        "perm_std_sharpe": round(perm_std, 4),
        "p_value": round(p_value, 4),
        "n_perms": n_perms,
        "n_active_days": n_active,
        "result": "significant" if p_value < 0.05 else "not_significant",
        "interpretation": (
            f"p={p_value:.3f}. "
            + (
                "Signal Sharpe is distinguishable from random day selection."
                if p_value < 0.05
                else "Signal Sharpe is NOT distinguishable from random day selection — "
                "the 'alpha' may be structural bias or market direction."
            )
        ),
        "pass": p_value < 0.05,
    }


def bootstrap_sharpe_ci(
    daily_returns: np.ndarray,
    n_boot: int = 1000,
    ci: float = 0.95,
    seed: int = 42,
) -> dict:
    """Bootstrap confidence interval for annualized Sharpe ratio.

    If the CI includes zero, the Sharpe is not distinguishable from noise.
    """
    rng = np.random.default_rng(seed)
    rets = np.asarray(daily_returns, dtype=float)
    rets = rets[~np.isnan(rets)]

    if len(rets) < 20:
        return {
            "test_name": "bootstrap_sharpe_ci",
            "result": None,
            "interpretation": "Too few observations for bootstrap",
            "pass": None,
        }

    observed = _sharpe(rets)
    n = len(rets)
    boot_sharpes = np.empty(n_boot)
    for i in range(n_boot):
        sample = rng.choice(rets, size=n, replace=True)
        boot_sharpes[i] = _sharpe(sample)

    alpha = (1 - ci) / 2
    lo = float(np.percentile(boot_sharpes, alpha * 100))
    hi = float(np.percentile(boot_sharpes, (1 - alpha) * 100))
    includes_zero = lo <= 0 <= hi

    return {
        "test_name": "bootstrap_sharpe_ci",
        "observed_sharpe": round(observed, 4),
        "ci_lower": round(lo, 4),
        "ci_upper": round(hi, 4),
        "ci_level": ci,
        "n_boot": n_boot,
        "includes_zero": includes_zero,
        "result": "zero_in_ci" if includes_zero else "zero_outside_ci",
        "interpretation": (
            f"Sharpe {observed:.3f}, {ci:.0%} CI [{lo:.3f}, {hi:.3f}]. "
            + (
                "CI includes zero — Sharpe is not distinguishable from noise."
                if includes_zero
                else "CI excludes zero — Sharpe is statistically meaningful."
            )
        ),
        "pass": not includes_zero,
    }


def concentration_ratio(
    daily_returns: np.ndarray,
    top_n: int = 5,
) -> dict:
    """What fraction of total P&L comes from the top N days?

    High concentration means the result depends on a few outlier days.
    A robust signal should have P&L spread across many days.
    """
    rets = np.asarray(daily_returns, dtype=float)
    rets = rets[~np.isnan(rets)]
    nonzero = rets[rets != 0.0]

    if len(nonzero) < top_n:
        return {
            "test_name": "concentration_ratio",
            "result": None,
            "interpretation": f"Fewer than {top_n} active days",
            "pass": None,
        }

    total_pnl = float(np.sum(nonzero))
    if abs(total_pnl) < 1e-12:
        return {
            "test_name": "concentration_ratio",
            "result": None,
            "interpretation": "Total P&L is effectively zero",
            "pass": None,
        }

    # Sort by absolute contribution to P&L
    sorted_by_abs = nonzero[np.argsort(-np.abs(nonzero))]
    top_n_pnl = float(np.sum(sorted_by_abs[:top_n]))
    ratio = abs(top_n_pnl / total_pnl)

    # Also check: if we remove top N days, does the sign flip?
    remaining = sorted_by_abs[top_n:]
    remaining_pnl = float(np.sum(remaining))
    sign_flips = (total_pnl > 0 and remaining_pnl < 0) or (
        total_pnl < 0 and remaining_pnl > 0
    )

    # Signed concentration: what fraction of total POSITIVE P&L comes from
    # the top N winning days? This is the economically meaningful metric —
    # absolute concentration mixes winners and losers, hiding the severity.
    positive_rets = nonzero[nonzero > 0]
    signed_concentration = None
    signed_concentrated = False
    if len(positive_rets) >= top_n and float(np.sum(positive_rets)) > 1e-12:
        sorted_winners = positive_rets[np.argsort(-positive_rets)]
        top_n_winners = float(np.sum(sorted_winners[:top_n]))
        total_positive = float(np.sum(positive_rets))
        signed_concentration = round(top_n_winners / total_positive, 4)
        signed_concentrated = signed_concentration > 0.90

    return {
        "test_name": "concentration_ratio",
        "top_n": top_n,
        "total_active_days": len(nonzero),
        "top_n_pnl_fraction": round(ratio, 4),
        "signed_top_n_fraction": signed_concentration,
        "sign_flips_without_top_n": sign_flips,
        "result": "concentrated" if ratio > 0.5 else "distributed",
        "interpretation": (
            f"Top {top_n} days account for {ratio:.1%} of total P&L "
            f"({len(nonzero)} active days). "
            + (
                "Removing them flips the sign — result depends entirely on outliers. "
                if sign_flips
                else ""
            )
            + (
                f"CRITICAL: {signed_concentration:.0%} of positive P&L from top {top_n} winners "
                f"— signal viability depends on {top_n} event(s). "
                if signed_concentrated
                else ""
            )
            + (
                "Highly concentrated — investigate whether top days are data artifacts."
                if ratio > 0.5
                else "P&L is reasonably distributed across trading days."
            )
        ),
        "pass": not sign_flips and not signed_concentrated,
    }


def return_autocorrelation(
    daily_returns: np.ndarray,
    max_lag: int = 5,
) -> dict:
    """Check serial correlation in strategy returns.

    Positive autocorrelation inflates Sharpe by sqrt(autocorrelation factor).
    Real alpha from overnight signals typically has low autocorrelation.
    High autocorrelation suggests the returns are not independent observations,
    and the effective sample size (and thus Sharpe significance) is overstated.
    """
    rets = np.asarray(daily_returns, dtype=float)
    rets = rets[~np.isnan(rets)]

    if len(rets) < max_lag + 10:
        return {
            "test_name": "return_autocorrelation",
            "result": None,
            "interpretation": "Too few observations",
            "pass": None,
        }

    autocorrs = {}
    for lag in range(1, max_lag + 1):
        c = float(np.corrcoef(rets[:-lag], rets[lag:])[0, 1])
        autocorrs[f"lag_{lag}"] = round(c, 4)

    # Ljung-Box-style threshold: |autocorr| > 2/sqrt(n) is suspicious
    threshold = 2.0 / np.sqrt(len(rets))
    significant_lags = [
        lag for lag, c in autocorrs.items() if abs(c) > threshold
    ]

    # Sharpe inflation factor from lag-1 autocorrelation
    # Only POSITIVE lag-1 autocorrelation inflates Sharpe.
    # Negative autocorrelation (mean-reversion) deflates it.
    rho1 = autocorrs["lag_1"]
    if rho1 > 0.01:
        inflation = np.sqrt((1 + 2 * rho1) / (1 - 2 * rho1)) if rho1 < 0.5 else float("inf")
    else:
        inflation = 1.0

    # Only flag positive autocorrelation at lag-1 as a Sharpe concern.
    # Higher lags and negative autocorrelation are informative but don't inflate Sharpe.
    positive_significant = [
        lag for lag, c in autocorrs.items() if c > threshold
    ]

    return {
        "test_name": "return_autocorrelation",
        "autocorrelations": autocorrs,
        "significance_threshold": round(threshold, 4),
        "significant_lags": significant_lags,
        "positive_significant_lags": positive_significant,
        "sharpe_inflation_factor": round(inflation, 4) if inflation != float("inf") else "inf",
        "n_observations": len(rets),
        "result": "inflated" if positive_significant else ("autocorrelated" if significant_lags else "independent"),
        "interpretation": (
            f"Lag-1 autocorrelation: {rho1:.4f} (threshold: ±{threshold:.4f}). "
            + (
                f"Positive autocorrelation at lags {positive_significant} — Sharpe inflated "
                f"by ~{inflation:.2f}x. Effective sample size is smaller than N={len(rets)}."
                if positive_significant
                else (
                    f"Significant autocorrelation at lags {significant_lags} but negative/mixed — "
                    f"does not inflate Sharpe. Returns show mean-reversion tendency."
                    if significant_lags
                    else "No significant autocorrelation — returns appear independent."
                )
            )
        ),
        "pass": len(positive_significant) == 0,
    }


# ═══════════════════════════════════════════════════════════════════════════════
# SIGNAL QUALITY (secondary — only meaningful after bugs are ruled out)
# ═══════════════════════════════════════════════════════════════════════════════


def ic_series(
    signal: np.ndarray,
    forward_return: np.ndarray,
) -> dict:
    """Information coefficient: rank correlation of signal with forward return.

    Computed as Spearman correlation. Useful for cross-sectional signals
    where you rank assets by signal strength each period.
    For time-series (long/flat) signals, use permutation_test instead.
    """
    sig = np.asarray(signal, dtype=float)
    fwd = np.asarray(forward_return, dtype=float)

    # Align and drop NaN
    mask = ~(np.isnan(sig) | np.isnan(fwd))
    sig, fwd = sig[mask], fwd[mask]

    if len(sig) < 20:
        return {
            "test_name": "ic_series",
            "result": None,
            "interpretation": "Too few observations for IC",
            "pass": None,
        }

    ic, p_value = stats.spearmanr(sig, fwd)
    ic = float(ic)
    p_value = float(p_value)

    # IC > 0.05 is considered meaningful in practice
    return {
        "test_name": "ic_series",
        "ic": round(ic, 4),
        "p_value": round(p_value, 4),
        "n_observations": len(sig),
        "result": "meaningful" if abs(ic) > 0.05 and p_value < 0.05 else "weak",
        "interpretation": (
            f"IC={ic:.4f}, p={p_value:.4f}, N={len(sig)}. "
            + (
                f"Rank correlation is statistically significant and practically meaningful."
                if abs(ic) > 0.05 and p_value < 0.05
                else "IC is weak or not statistically significant."
            )
        ),
        "pass": abs(ic) > 0.05 and p_value < 0.05,
    }


def ic_decay(
    signal: np.ndarray,
    returns_by_lag: dict[int, np.ndarray],
) -> dict:
    """IC at increasing lags. Real signals decay; bugs often don't.

    Args:
        signal: signal values aligned with returns_by_lag arrays
        returns_by_lag: {lag: forward_returns_at_that_lag}, e.g. {1: ret_1d, 2: ret_2d, 5: ret_5d}
    """
    sig = np.asarray(signal, dtype=float)
    results = {}

    for lag, fwd in sorted(returns_by_lag.items()):
        fwd = np.asarray(fwd, dtype=float)
        mask = ~(np.isnan(sig) | np.isnan(fwd))
        if mask.sum() < 20:
            results[lag] = None
            continue
        ic, _ = stats.spearmanr(sig[mask], fwd[mask])
        results[lag] = round(float(ic), 4)

    lags = [k for k, v in results.items() if v is not None]
    ics = [results[k] for k in lags]

    if len(ics) < 2:
        return {
            "test_name": "ic_decay",
            "ic_by_lag": results,
            "result": None,
            "interpretation": "Too few lags to assess decay",
            "pass": None,
        }

    # Check monotonic decay: is each IC <= the previous?
    decays = all(abs(ics[i]) >= abs(ics[i + 1]) for i in range(len(ics) - 1))
    # Check if lag-1 IC is strongest
    lag1_strongest = all(abs(ics[0]) >= abs(ic) for ic in ics[1:])

    return {
        "test_name": "ic_decay",
        "ic_by_lag": results,
        "monotonic_decay": decays,
        "lag1_strongest": lag1_strongest,
        "result": "healthy_decay" if decays else "suspicious_pattern",
        "interpretation": (
            f"IC by lag: {results}. "
            + (
                "IC decays monotonically with lag — consistent with real signal."
                if decays
                else "IC does NOT decay with lag — suspicious. Real predictive power "
                "should weaken at longer horizons. Flat or increasing IC suggests "
                "look-ahead bias or a structural artifact."
            )
        ),
        "pass": decays,
    }


def regime_stability(
    daily_returns: np.ndarray,
    regime_labels: np.ndarray,
) -> dict:
    """Does the signal work across regimes or only in one?

    A signal that only works in bull markets may be capturing market
    direction, not a distinct edge.
    """
    rets = np.asarray(daily_returns, dtype=float)
    labels = np.asarray(regime_labels)

    unique_regimes = np.unique(labels[~(labels == None)])  # noqa: E711
    if len(unique_regimes) < 2:
        return {
            "test_name": "regime_stability",
            "result": None,
            "interpretation": "Fewer than 2 regimes found",
            "pass": None,
        }

    regime_stats = {}
    for regime in unique_regimes:
        mask = labels == regime
        r = rets[mask]
        r = r[~np.isnan(r)]
        if len(r) < 10:
            regime_stats[str(regime)] = {"n": len(r), "sharpe": None, "mean": None}
            continue
        regime_stats[str(regime)] = {
            "n": int(len(r)),
            "sharpe": round(_sharpe(r), 4),
            "mean_bps": round(float(np.mean(r)) * 10000, 2),
            "win_rate": round(float(np.mean(r[r != 0] > 0)) * 100, 1) if np.any(r != 0) else 0,
        }

    # Check if signal direction is consistent across regimes
    sharpes = [s["sharpe"] for s in regime_stats.values() if s["sharpe"] is not None]
    all_positive = all(s > 0 for s in sharpes)
    all_negative = all(s < 0 for s in sharpes)
    consistent = all_positive or all_negative

    return {
        "test_name": "regime_stability",
        "regime_stats": regime_stats,
        "direction_consistent": consistent,
        "result": "stable" if consistent else "regime_dependent",
        "interpretation": (
            f"Per-regime stats: {regime_stats}. "
            + (
                "Signal direction is consistent across regimes."
                if consistent
                else "Signal works in some regimes but not others — "
                "may be capturing regime direction rather than a distinct edge."
            )
        ),
        "pass": consistent,
    }


# ═══════════════════════════════════════════════════════════════════════════════
# INTERNAL
# ═══════════════════════════════════════════════════════════════════════════════


def _sharpe(daily_returns: np.ndarray) -> float:
    """Annualized Sharpe. Matches shared/metrics.py logic."""
    if len(daily_returns) < 2:
        return 0.0
    std = np.std(daily_returns)
    if std == 0:
        return 0.0
    return float(np.mean(daily_returns) / std * np.sqrt(252))
