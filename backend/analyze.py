"""
CLI for regenerating the congressional-trade backtest with variable inputs.

Runs the same train/test analysis as POST /api/model/run, but with every knob
exposed on the command line — train/test windows, the event anchor date,
forward horizons, and the strategy grid.

Run inside the backend container:

  docker compose exec backend python analyze.py
  docker compose exec backend python analyze.py --date-field transaction_date
  docker compose exec backend python analyze.py \\
      --train-start 2021-01-01 --train-end 2023-12-31 \\
      --test-start 2024-01-01 --test-end 2024-12-31 \\
      --hold-days 21,42,63 --min-members 1,2 --output /tmp/run.json
"""
import argparse
import itertools
import json
from datetime import date

from app.services import backtest


def _date(s: str) -> date:
    return date.fromisoformat(s)


def _ints(s: str) -> list[int]:
    return [int(x) for x in s.split(",") if x.strip()]


def _print_event_study(es: dict, label: str) -> None:
    print(f"\n=== EVENT STUDY [{label}]  {es['window']['start']}..{es['window']['end']}"
          f"  anchor={es['date_field']}  events={es['events_total']}"
          f"  missing_price={es['events_missing_price']} ===")
    for side in ("buy", "sell"):
        for hname, groups in es["results"][side].items():
            a = groups["all"]
            if not a.get("n"):
                continue
            b = a["buckets"]
            print(f"  {side:4} {hname:5}  n={a['n']:5}  mean={a['mean_excess']:+.4f}"
                  f"  median={a['median_excess']:+.4f}  win={a['win_rate']:.2f}"
                  f"  t={a['t_stat']}"
                  f"  O/I/U={b['outperform']:.2f}/{b['in-line']:.2f}/{b['underperform']:.2f}")


def main() -> None:
    p = argparse.ArgumentParser(
        description="Regenerate the congressional-trade backtest analysis.")
    p.add_argument("--train-start", type=_date, default=date(2021, 1, 1))
    p.add_argument("--train-end", type=_date, default=date(2024, 12, 31))
    p.add_argument("--test-start", type=_date, default=date(2025, 1, 1))
    p.add_argument("--test-end", type=_date, default=date(2025, 12, 31))
    p.add_argument("--date-field", choices=["filing_date", "transaction_date"],
                   default="filing_date",
                   help="event anchor: filing_date (tradeable) or transaction_date")
    p.add_argument("--event-horizons", type=_ints, default=[5, 21, 63],
                   help="event-study forward horizons, in trading days")
    p.add_argument("--hold-days", type=_ints, default=[21, 63],
                   help="backtest holding periods for the grid search")
    p.add_argument("--min-members", type=_ints, default=[1, 2, 3],
                   help="conviction thresholds (distinct buyers) for the grid")
    p.add_argument("--benchmark", default="SPY",
                   help="benchmark ticker for excess return (SPY cap-weight, RSP equal-weight)")
    p.add_argument("--leadership-only", action="store_true",
                   help="restrict to trades made while the member held a leadership/chair position")
    p.add_argument("--benchmark-mode", choices=["ticker", "sector"], default="ticker",
                   help="ticker: excess vs the single --benchmark; sector: excess vs each stock's GICS sector ETF")
    p.add_argument("--owner-filter",
                   choices=list(backtest.OWNER_FILTERS), default="any",
                   help="restrict by who held the asset: any | self | spouse_or_joint | spouse_only | joint_only | dependent")
    p.add_argument("--output", help="path to write the full result JSON")
    args = p.parse_args()

    horizons = {f"{d}d": d for d in args.event_horizons}
    param_grid = [{"horizon_days": h, "min_members": m}
                  for h, m in itertools.product(args.hold_days, args.min_members)]

    print(f"Analysis  anchor={args.date_field}  benchmark={args.benchmark}"
          f"  bench_mode={args.benchmark_mode}  leadership_only={args.leadership_only}"
          f"  owner_filter={args.owner_filter}")
    print(f"  train {args.train_start}..{args.train_end}"
          f"  |  test {args.test_start}..{args.test_end}")
    print(f"  grid: {len(param_grid)} configs  |  event horizons: {args.event_horizons}")

    result = backtest.train_test_analysis(
        args.train_start, args.train_end, args.test_start, args.test_end,
        date_field=args.date_field, param_grid=param_grid, horizons=horizons,
        benchmark=args.benchmark, leadership_only=args.leadership_only,
        benchmark_mode=args.benchmark_mode,
        owner_filter=args.owner_filter,
    )

    _print_event_study(result["event_study"]["train"], "TRAIN")
    _print_event_study(result["event_study"]["test"], "TEST")

    print("\n=== GRID SEARCH ON TRAIN ===")
    for g in result["grid_search_train"]:
        pr, res = g["params"], g["result"]
        print(f"  hold={pr['horizon_days']:>3}d  min_members={pr['min_members']}"
              f"  positions={res['positions_opened']:>5}"
              f"  strat={res['strategy_return']:+.4f}"
              f"  bench={res['benchmark_return']:+.4f}"
              f"  excess={res['excess_return']:+.4f}")

    sel = result["selected_params"]
    st = result["selected_train_result"]
    oos = result["out_of_sample_test"]
    print(f"\nSELECTED (best train excess): "
          f"hold={sel['horizon_days']}d  min_members={sel['min_members']}")
    print(f"  train          : strat={st['strategy_return']:+.4f}"
          f"  bench={st['benchmark_return']:+.4f}  excess={st['excess_return']:+.4f}")
    print(f"  OUT-OF-SAMPLE  : strat={oos['strategy_return']:+.4f}"
          f"  bench={oos['benchmark_return']:+.4f}  excess={oos['excess_return']:+.4f}")

    if args.output:
        with open(args.output, "w") as f:
            json.dump(result, f, indent=2)
        print(f"\nFull result JSON written to {args.output}")


if __name__ == "__main__":
    main()
