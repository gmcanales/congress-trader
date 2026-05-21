"""
Bayesian hierarchical model: does holding a leadership / committee-chair
position at the time of a purchase predict positive forward excess return?

Outcome
  excess_63d  (filing-date-anchored 63-trading-day forward excess return
               vs SPY) — built from `v_stock_transactions_leadership` via
               `build_trade_dataset.build()`.

Predictors (fixed effects)
  leadership_at_trade      — THIS is the headline test
  in_majority              — member's party controls the chamber
  is_post_trifecta         — transaction_date ≥ 2025-01-03 (regime control)
  party_R                  — Republican (vs Democratic baseline)
  sector_<X>               — one-hot sector dummies (Technology baseline)

Hierarchical structure
  alpha_member  ~ Normal(mu_alpha, tau_member)
  every trade inherits its trader's random intercept.

The headline target is the posterior on `beta_leadership`. P(beta > 0 | data)
is the direct probabilistic answer to "do leaders have an edge, controlling
for who's in the majority, the trifecta regime change, party, sector, and
per-member baseline tendencies?"

Run:

  docker compose run --rm --no-deps backend python bayesian_model.py
  docker compose run --rm --no-deps backend python bayesian_model.py \\
      --draws 2000 --chains 4 --target-accept 0.95

By default the script also persists the posterior summary as a new
AnalysisRun(tool='bayesian_hierarchical') so the result is traceable
alongside the rules-backtest runs.
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import warnings
from datetime import date

# Quiet pytensor's startup noise so the log stays readable.
os.environ.setdefault("PYTENSOR_FLAGS", "warn__ignore_bug_before=all")
warnings.filterwarnings("ignore", category=FutureWarning)
warnings.filterwarnings("ignore", category=UserWarning)

import numpy as np
import pandas as pd
import pymc as pm
import arviz as az

from app.database import SessionLocal
from app.models import AnalysisRun, Dataset
from build_trade_dataset import build


# Reference (baseline) sector for the dummy encoding. Picked because it has
# the most rows and is uncontroversial as a reference level.
REFERENCE_SECTOR = "Technology"


def _prepare_design(df: pd.DataFrame, horizon: int) -> dict:
    """Encode the data frame into the arrays PyMC needs: y, design matrix X,
    member index, and column names."""
    df = df.copy()
    y_col = f"excess_{horizon}d_spy"
    df = df[df[y_col].notna()].reset_index(drop=True)

    # Winsorize tails to reduce sensitivity to extreme outliers (a single
    # ten-bagger or wipeout shouldn't dominate the population mean).
    lo, hi = df[y_col].quantile([0.005, 0.995])
    df[y_col] = df[y_col].clip(lo, hi)

    # Encode member as integer codes for the random effect.
    member_idx, member_ids = pd.factorize(df["member_id"])
    df["member_code"] = member_idx

    # Sector one-hots, dropping the reference sector. Unknown sector treated
    # as its own level so we don't lose those rows; we'll alias it to the
    # reference by including it under "Other" with no separate effect.
    sectors = (df["sector"].fillna("Unknown")).astype(str)
    sector_levels = [s for s in sectors.unique() if s not in (REFERENCE_SECTOR,)]
    sector_levels.sort()
    sector_dummies = pd.DataFrame(index=df.index)
    for lvl in sector_levels:
        sector_dummies[f"sector_{lvl}"] = (sectors == lvl).astype(np.float32).values

    # Fixed-effect design matrix (excluding the per-member intercept which is
    # handled separately as a hierarchical random effect).
    X_parts = {
        "leadership_at_trade": df["leadership_at_trade"].astype(np.float32).values,
        "in_majority":         df["in_majority"].astype(np.float32).values,
        "is_post_trifecta":    df["is_post_trifecta"].astype(np.float32).values,
        "party_R":             (df["party"].astype(str) == "Republican").astype(np.float32).values,
    }
    for col in sector_dummies.columns:
        X_parts[col] = sector_dummies[col].values

    X = np.column_stack(list(X_parts.values()))
    feature_names = list(X_parts.keys())
    y = df[y_col].astype(np.float32).values

    return {
        "y": y,
        "X": X,
        "member_code": member_idx.astype(np.int32),
        "n_members": int(member_idx.max()) + 1,
        "feature_names": feature_names,
        "n_obs": len(df),
        "df": df,
    }


def fit_model(design: dict, draws: int, tune: int, chains: int,
              target_accept: float, seed: int) -> az.InferenceData:
    y = design["y"]
    X = design["X"]
    member_code = design["member_code"]
    n_members = design["n_members"]
    n_features = X.shape[1]

    coords = {
        "feature": design["feature_names"],
        "member": np.arange(n_members),
        "obs": np.arange(design["n_obs"]),
    }

    with pm.Model(coords=coords) as model:
        # Hierarchical per-member intercept.
        mu_alpha = pm.Normal("mu_alpha", mu=0.0, sigma=0.05)
        tau_member = pm.HalfNormal("tau_member", sigma=0.1)
        alpha_member = pm.Normal("alpha_member", mu=mu_alpha,
                                 sigma=tau_member, dims="member")

        # Fixed effects.
        beta = pm.Normal("beta", mu=0.0, sigma=0.1, dims="feature")

        # Residual scale.
        sigma = pm.HalfNormal("sigma", sigma=0.5)

        mu = alpha_member[member_code] + pm.math.dot(X, beta)

        pm.Normal("y_obs", mu=mu, sigma=sigma, observed=y, dims="obs")

        idata = pm.sample(
            draws=draws, tune=tune, chains=chains,
            target_accept=target_accept, random_seed=seed,
            progressbar=False, compute_convergence_checks=True,
        )
    return idata


def summarize(idata: az.InferenceData, feature_names: list[str]) -> dict:
    """Distill the posterior into the headline numbers we want to report."""
    summary_df = az.summary(
        idata, var_names=["mu_alpha", "tau_member", "sigma", "beta"],
        hdi_prob=0.94,
    )

    # Posterior on each fixed effect by name.
    beta_post = idata.posterior["beta"]  # (chain, draw, feature)
    beta_by_feature = {}
    for i, name in enumerate(feature_names):
        samp = beta_post.isel(feature=i).values.flatten()
        beta_by_feature[name] = {
            "mean":      float(np.mean(samp)),
            "sd":        float(np.std(samp)),
            "hdi_3":     float(np.quantile(samp, 0.03)),
            "hdi_97":    float(np.quantile(samp, 0.97)),
            "p_gt_0":    float((samp > 0).mean()),
        }

    mu_alpha = idata.posterior["mu_alpha"].values.flatten()
    tau_member = idata.posterior["tau_member"].values.flatten()
    sigma = idata.posterior["sigma"].values.flatten()

    # Convergence diagnostics.
    ess = float(summary_df["ess_bulk"].min())
    rhat_max = float(summary_df["r_hat"].max())

    return {
        # n_obs is filled in by main() from the design dict; here we just
        # set a placeholder so the schema is consistent.
        "n_obs": None,
        "diagnostics": {
            "min_ess_bulk": ess,
            "max_rhat": rhat_max,
        },
        "global": {
            "mu_alpha_mean": float(np.mean(mu_alpha)),
            "mu_alpha_sd":   float(np.std(mu_alpha)),
            "tau_member_mean": float(np.mean(tau_member)),
            "sigma_mean":    float(np.mean(sigma)),
        },
        "beta": beta_by_feature,
    }


def persist_run(params: dict, summary: dict) -> int:
    s = SessionLocal()
    try:
        ds = s.query(Dataset).order_by(Dataset.id).first()
        run = AnalysisRun(
            dataset_id=ds.id,
            tool="bayesian_hierarchical",
            status="done",
            params=params,
            scores={
                "p_leadership_gt_0": summary["beta"]["leadership_at_trade"]["p_gt_0"],
                "leadership_mean":  summary["beta"]["leadership_at_trade"]["mean"],
                "leadership_hdi_3":  summary["beta"]["leadership_at_trade"]["hdi_3"],
                "leadership_hdi_97": summary["beta"]["leadership_at_trade"]["hdi_97"],
                "in_majority_mean": summary["beta"]["in_majority"]["mean"],
                "trifecta_mean":    summary["beta"]["is_post_trifecta"]["mean"],
                "min_ess":          summary["diagnostics"]["min_ess_bulk"],
                "max_rhat":         summary["diagnostics"]["max_rhat"],
            },
            full_results={"params": params, "summary": summary},
        )
        s.add(run); s.commit(); s.refresh(run)
        return run.id
    finally:
        s.close()


def _print_report(summary: dict) -> None:
    print()
    print("Bayesian hierarchical model — posterior summary")
    print(f"  draws/chain × chains      : (see params)")
    print(f"  diagnostics: min ESS = {summary['diagnostics']['min_ess_bulk']:.0f}  "
          f"max R̂ = {summary['diagnostics']['max_rhat']:.3f}  "
          f"(want R̂ < 1.01, ESS > 400)")
    print()
    print(f"  Global params:")
    g = summary["global"]
    print(f"    mu_alpha (population mean baseline): {g['mu_alpha_mean']:+.4f}  ± {g['mu_alpha_sd']:.4f}")
    print(f"    tau_member (between-member SD)     : {g['tau_member_mean']:.4f}")
    print(f"    sigma (residual SD)                : {g['sigma_mean']:.4f}")
    print()
    print(f"  Fixed effects (in raw forward-excess-return units):")
    print(f"    {'feature':<28} {'mean':>9} {'sd':>7} {'hdi 3-97':>18} {'P(>0)':>8}")
    for name, b in summary["beta"].items():
        hdi = f"[{b['hdi_3']:+.4f}, {b['hdi_97']:+.4f}]"
        print(f"    {name:<28} {b['mean']:>+9.4f} {b['sd']:>7.4f} {hdi:>18} {b['p_gt_0']:>8.3f}")


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--start", type=date.fromisoformat, default=date(2021, 1, 1))
    p.add_argument("--end", type=date.fromisoformat, default=date(2025, 12, 31))
    p.add_argument("--horizon", type=int, default=63)
    p.add_argument("--draws", type=int, default=1500)
    p.add_argument("--tune", type=int, default=1500)
    p.add_argument("--chains", type=int, default=4)
    p.add_argument("--target-accept", type=float, default=0.92)
    p.add_argument("--seed", type=int, default=20260521)
    p.add_argument("--no-persist", action="store_true",
                   help="Don't write to analysis_runs (useful for dry experiments).")
    args = p.parse_args()

    logging.info("Building per-trade dataset...")
    from pathlib import Path
    build(args.start, args.end, args.horizon, Path("/tmp/_bayes_tmp.parquet"))
    df = pd.read_parquet("/tmp/_bayes_tmp.parquet")
    logging.info(f"Loaded {len(df)} rows for model fit")

    design = _prepare_design(df, args.horizon)
    logging.info(f"Design: n_obs={design['n_obs']} n_members={design['n_members']} "
                 f"n_features={len(design['feature_names'])}")
    logging.info(f"Features: {design['feature_names']}")

    logging.info(f"Sampling: {args.chains} chains × {args.draws} draws "
                 f"(tune={args.tune}, target_accept={args.target_accept})...")
    idata = fit_model(design, args.draws, args.tune, args.chains,
                      args.target_accept, args.seed)

    summary = summarize(idata, design["feature_names"])
    summary["n_obs"] = design["n_obs"]
    summary["n_members"] = design["n_members"]
    _print_report(summary)

    if not args.no_persist:
        params = {
            "start": args.start.isoformat(), "end": args.end.isoformat(),
            "horizon": args.horizon, "draws": args.draws,
            "tune": args.tune, "chains": args.chains,
            "target_accept": args.target_accept, "seed": args.seed,
            "feature_names": design["feature_names"],
            "n_obs": design["n_obs"], "n_members": design["n_members"],
        }
        run_id = persist_run(params, summary)
        print(f"\nPersisted AnalysisRun #{run_id}")


if __name__ == "__main__":
    main()
