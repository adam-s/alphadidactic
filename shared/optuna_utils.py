"""
optuna_utils.py — Shared Optuna optimization infrastructure.

Enforces train-only optimization, trial caps, seed injection, and
train-test gap analysis. All experiments use this instead of rolling
their own Optuna setup.

Usage:
    from shared.optuna_utils import run_optimization

    results = run_optimization(
        objective_fn=my_objective,       # fn(params) -> metrics dict
        param_space=my_param_space,      # fn(trial) -> params dict
        train_metric="train_sharpe",     # key in metrics dict to maximize
        seed_params=[default_params],    # known-good starting points
        n_trials=200,                    # hard cap (default from MAX_TRIALS)
        log_path="output/optuna_log.csv",
    )
"""

import json
import logging
import time
from pathlib import Path
from typing import Any, Callable

import numpy as np

logger = logging.getLogger(__name__)

# ═══════════════════════════════════════════════════════════════════════════
# TRIAL CAP — structural bound based on empirical convergence analysis.
# Reference experiments show convergence at 150-400 trials depending on
# parameter space dimensionality. Use MAX_TRIALS as default; experiments
# can override up to ABSOLUTE_MAX_TRIALS with justification.
# ═══════════════════════════════════════════════════════════════════════════
MAX_TRIALS = 200          # default for most experiments
ABSOLUTE_MAX_TRIALS = 200  # hard ceiling — more trials = more overfitting for diminishing returns

# TPE sampler config
N_STARTUP_TRIALS = 15  # random exploration before TPE kicks in
SEED = 42              # reproducibility


def run_optimization(
    objective_fn: Callable[[dict], dict],
    param_space: Callable[["optuna.Trial"], dict],
    train_metric: str,
    seed_params: list[dict] | None = None,
    n_trials: int = MAX_TRIALS,
    log_path: str | Path | None = None,
    test_metric: str | None = None,
    direction: str = "maximize",
    study_name: str | None = None,
) -> dict:
    """Run Optuna optimization with train-only objective and safety rails.

    Args:
        objective_fn: Takes a params dict, returns a metrics dict.
            The metrics dict MUST contain `train_metric` key.
            May also contain `test_metric` key for gap analysis (never used
            for selection).
        param_space: Takes an optuna.Trial, returns a params dict using
            trial.suggest_* methods.
        train_metric: Key in metrics dict to optimize. ONLY this metric
            drives parameter selection. Test metrics are logged but never
            used for selection.
        seed_params: List of known-good param dicts to enqueue as first
            trials. Avoids cold start — Optuna can't do worse than the
            best seed. Always include the hardcoded baseline params here.
        n_trials: Maximum number of trials. Capped at ABSOLUTE_MAX_TRIALS.
        log_path: Path to write CSV trial log. None = no log file.
        test_metric: Key in metrics dict for holdout metric. Logged for
            train-test gap analysis but NEVER used for selection.
        direction: "maximize" or "minimize".
        study_name: Optional name for the Optuna study.

    Returns:
        dict with keys:
            best_params: dict of best parameters
            best_value: float objective value
            best_metrics: full metrics dict for best trial
            seed_metrics: metrics for each seed param set (for comparison)
            n_trials: actual number of trials run
            convergence_trial: trial number where best was found
            train_test_gap: gap at best trial (if test_metric provided)
            all_trials: list of (trial_number, objective, params, metrics)
    """
    import optuna
    optuna.logging.set_verbosity(optuna.logging.WARNING)

    # Enforce trial cap
    if n_trials > ABSOLUTE_MAX_TRIALS:
        logger.warning(
            f"n_trials={n_trials} exceeds ABSOLUTE_MAX_TRIALS={ABSOLUTE_MAX_TRIALS}. "
            f"Capping at {ABSOLUTE_MAX_TRIALS}."
        )
        n_trials = ABSOLUTE_MAX_TRIALS

    # Create study with TPE sampler
    study = optuna.create_study(
        direction=direction,
        sampler=optuna.samplers.TPESampler(
            n_startup_trials=N_STARTUP_TRIALS,
            seed=SEED,
        ),
        study_name=study_name,
    )

    # Seed with known-good params (avoids cold start)
    seed_metrics_list = []
    if seed_params:
        for params in seed_params:
            study.enqueue_trial(params)
        logger.info(f"Seeded {len(seed_params)} known-good param sets")

    # Setup logging
    log_fh = None
    if log_path:
        log_path = Path(log_path)
        log_path.parent.mkdir(parents=True, exist_ok=True)
        log_fh = open(log_path, "w")

    all_trials = []
    best_val = float("-inf") if direction == "maximize" else float("inf")

    def _objective(trial):
        nonlocal best_val

        params = param_space(trial)
        t0 = time.time()
        metrics = objective_fn(params)
        elapsed = time.time() - t0

        # TRAIN-ONLY objective — test is holdout, never used for selection
        obj = metrics[train_metric]

        # Track gap for post-hoc analysis
        gap = None
        if test_metric and test_metric in metrics:
            gap = metrics[train_metric] - metrics[test_metric]

        is_best = (
            (direction == "maximize" and obj > best_val) or
            (direction == "minimize" and obj < best_val)
        )
        if is_best:
            best_val = obj

        trial_record = {
            "number": trial.number,
            "objective": obj,
            "gap": gap,
            "params": params,
            "metrics": metrics,
            "elapsed": elapsed,
        }
        all_trials.append(trial_record)

        # Track seed metrics separately
        if seed_params and trial.number < len(seed_params):
            seed_metrics_list.append(metrics)

        # Log to CSV
        if log_fh:
            if trial.number == 0:
                # Write header on first trial
                metric_keys = sorted(metrics.keys())
                param_keys = sorted(params.keys())
                header = (
                    "trial,objective,gap,elapsed,"
                    + ",".join(f"m_{k}" for k in metric_keys) + ","
                    + ",".join(f"p_{k}" for k in param_keys)
                )
                log_fh.write(header + "\n")

            metric_keys = sorted(metrics.keys())
            param_keys = sorted(params.keys())
            metric_vals = ",".join(str(round(metrics[k], 6)) if isinstance(metrics[k], float) else str(metrics[k]) for k in metric_keys)
            param_vals = ",".join(str(round(params[k], 6)) if isinstance(params[k], float) else str(params[k]) for k in param_keys)
            gap_str = f"{gap:+.3f}" if gap is not None else ""
            log_fh.write(f"{trial.number},{obj:.4f},{gap_str},{elapsed:.1f},{metric_vals},{param_vals}\n")
            log_fh.flush()

        return obj

    # Run optimization
    logger.info(f"Starting Optuna: {n_trials} trials, objective={train_metric}, direction={direction}")
    study.optimize(_objective, n_trials=n_trials, show_progress_bar=True)

    if log_fh:
        log_fh.close()

    # Extract results
    best_trial = study.best_trial
    best_record = next(r for r in all_trials if r["number"] == best_trial.number)

    result = {
        "best_params": best_record["params"],
        "best_value": best_trial.value,
        "best_metrics": best_record["metrics"],
        "seed_metrics": seed_metrics_list,
        "n_trials": len(study.trials),
        "convergence_trial": best_trial.number,
        "train_test_gap": best_record["gap"],
        "all_trials": [
            {
                "number": r["number"],
                "objective": r["objective"],
                "gap": r["gap"],
                "params": r["params"],
                "metrics": r["metrics"],
            }
            for r in all_trials
        ],
    }

    # Log summary
    logger.info(f"Optuna complete: {len(study.trials)} trials")
    logger.info(f"  Best trial: #{best_trial.number}, {train_metric}={best_trial.value:.4f}")
    if best_record["gap"] is not None:
        logger.info(f"  Train-test gap: {best_record['gap']:+.4f}")
    if seed_metrics_list:
        seed_best = max(m[train_metric] for m in seed_metrics_list)
        improvement = best_trial.value - seed_best
        logger.info(f"  vs best seed: {improvement:+.4f}")

    return result


def save_optuna_results(result: dict, output_dir: str | Path) -> None:
    """Save optimization results to JSON."""
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    # Save full results (with all trials for analysis)
    path = output_dir / "optuna_results.json"
    with open(path, "w") as f:
        json.dump(result, f, indent=2, default=str)
    logger.info(f"Saved optuna results to {path}")

    # Save just the best params (for use in run_strategy.py)
    params_path = output_dir / "optuna_best_params.json"
    with open(params_path, "w") as f:
        json.dump(result["best_params"], f, indent=2, default=str)
    logger.info(f"Saved best params to {params_path}")


def analyze_overfitting(result: dict) -> dict:
    """Analyze train-test gap across all trials to find overfitting frontier.

    Returns dict with:
        pareto_trials: trials on the Pareto frontier (high train, low gap)
        gap_correlation: correlation between train metric and gap
        recommended_params: params from the Pareto-optimal trial with
            best balance of train performance and low overfitting
    """
    trials_with_gap = [
        t for t in result["all_trials"]
        if t["gap"] is not None
    ]

    if not trials_with_gap:
        return {"error": "No test metric available for gap analysis"}

    objectives = np.array([t["objective"] for t in trials_with_gap])
    gaps = np.array([t["gap"] for t in trials_with_gap])

    # Correlation: positive = higher train → more overfit
    if np.std(objectives) > 0 and np.std(gaps) > 0:
        gap_corr = float(np.corrcoef(objectives, gaps)[0, 1])
    else:
        gap_corr = 0.0

    # Pareto frontier: maximize objective while minimizing gap
    # A trial is Pareto-optimal if no other trial has both higher
    # objective AND lower gap
    pareto_indices = []
    for i, (obj_i, gap_i) in enumerate(zip(objectives, gaps)):
        dominated = False
        for j, (obj_j, gap_j) in enumerate(zip(objectives, gaps)):
            if i == j:
                continue
            if obj_j >= obj_i and gap_j <= gap_i and (obj_j > obj_i or gap_j < gap_i):
                dominated = True
                break
        if not dominated:
            pareto_indices.append(i)

    pareto_trials = [trials_with_gap[i] for i in pareto_indices]

    # Recommend: Pareto trial with best objective where gap < median gap
    median_gap = float(np.median(gaps))
    candidates = [t for t in pareto_trials if t["gap"] < median_gap]
    if candidates:
        recommended = max(candidates, key=lambda t: t["objective"])
    elif pareto_trials:
        recommended = min(pareto_trials, key=lambda t: abs(t["gap"]))
    else:
        recommended = trials_with_gap[0]

    return {
        "pareto_trials": [
            {"number": t["number"], "objective": t["objective"], "gap": t["gap"]}
            for t in pareto_trials
        ],
        "gap_correlation": gap_corr,
        "median_gap": median_gap,
        "recommended_params": recommended["params"],
        "recommended_objective": recommended["objective"],
        "recommended_gap": recommended["gap"],
    }
