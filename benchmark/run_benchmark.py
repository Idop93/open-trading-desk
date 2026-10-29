#!/usr/bin/env python3
"""HW7 benchmark: can the Scorekeeper tell skilled providers from unskilled ones?

Uses the team's real SyntheticMarket, ViewLog (hash-chained) and Scorekeeper. See PLAN.md for the
pre-registered claim, cases and success checks. Usage:
    python3 run_benchmark.py --rule first_touch --seeds tuning   # the Scorekeeper as shipped (v1)
    python3 run_benchmark.py --rule direction --seeds fresh      # after the change (v2)
"""
from __future__ import annotations

import argparse
import inspect
import json
import statistics
import tempfile
import time
from pathlib import Path

from opentradingdesk.market_data import SyntheticMarket
from opentradingdesk.providers.technical_momentum import TechnicalMomentumProvider
from opentradingdesk.scorekeeper import Scorekeeper
from opentradingdesk.view_log import ViewLog

from bench_providers import KnownSkillProvider, LazyAvoidProvider, LiarLongProvider
from truth import spearman, view_is_correct

TICKERS = ["AAA", "BBB", "CCC"]
HORIZON = 30
FULL_DAY = [(t, m) for t in TICKERS for m in range(60, 380, 20)]          # 48 views per provider
SMALL_DAY = [(t, m) for t in TICKERS[:2] for m in (60, 180, 300)]         # 6 views per provider
SKILLS = [0.0, 0.2, 0.4, 0.6, 0.8]
GAMERS = {"lazy-avoid", "liar-long"}
SEEDS = {"tuning": range(1, 31), "fresh": range(1001, 1031)}
TEST_DAY_OFFSET = 500_000
RESULTS = Path(__file__).resolve().parent / "results"


def providers(case: str, seed: int) -> list:
    ps = [KnownSkillProvider(p, seed) for p in SKILLS] + [TechnicalMomentumProvider()]
    return ps + ([LazyAvoidProvider(), LiarLongProvider()] if case in ("B", "C") else [])


def scorekeeper(db: Path, log: ViewLog, market: SyntheticMarket, rule: str) -> Scorekeeper:
    if "outcome_rule" in inspect.signature(Scorekeeper).parameters:
        return Scorekeeper(db, log, market, outcome_rule=rule)
    if rule != "first_touch":
        raise SystemExit("this Scorekeeper has no outcome_rule option; only first_touch is available")
    return Scorekeeper(db, log, market)


def accuracy(provider, market: SyntheticMarket, schedule) -> float | None:
    """Independent check: share of the provider's views the price actually proved right."""
    checks = []
    for ticker, minute in schedule:
        view = provider.answer_view(ticker, HORIZON, minute, market)
        if view is not None:
            checks.append(view_is_correct(view.__dict__, market, minute))
    return sum(checks) / len(checks) if checks else None


def one_run(case: str, seed: int, rule: str) -> dict:
    started = time.perf_counter()
    schedule = SMALL_DAY if case == "C" else FULL_DAY
    train_market = SyntheticMarket(TICKERS, seed=seed)
    with tempfile.TemporaryDirectory() as tmp:
        log = ViewLog(Path(tmp) / "views.db")
        for provider in providers(case, seed):
            for ticker, minute in schedule:
                view = provider.answer_view(ticker, HORIZON, minute, train_market)
                if view is not None:
                    view.validate()
                    log.log_view(view, minute)
        chain_ok, chain_hash = log.verify_chain()
        keeper = scorekeeper(Path(tmp) / "scores.db", log, train_market, rule)
        scores = {r.provider_id: r.hit_rate for r in keeper.score_all(390)}
        keeper.close(); log.close()

    order = [p.card().provider_id for p in providers(case, seed)]
    ranked = sorted(order, key=lambda pid: (-scores.get(pid, 0.0), order.index(pid)))
    pick = ranked[0]

    test_market = SyntheticMarket(TICKERS, seed=seed + TEST_DAY_OFFSET)
    test_acc = {p.card().provider_id: accuracy(p, test_market, schedule) for p in providers(case, seed + TEST_DAY_OFFSET)}
    answered = [a for a in test_acc.values() if a is not None]
    known = [f"skill-p{int(p * 100):02d}" for p in SKILLS]
    return {
        "case": case, "seed": seed, "rule": rule, "scores": scores, "ranked": ranked, "pick": pick,
        "spearman": spearman(SKILLS, [scores.get(pid, 0.0) for pid in known]),
        "pick_test_acc": test_acc[pick], "random_test_acc": statistics.mean(answered),
        "fixed_test_acc": test_acc["technical-momentum-01"], "best_test_acc": max(answered),
        "gamer_ranked_first": pick in GAMERS, "chain_verified": chain_ok, "chain_head": chain_hash,
        "seconds": round(time.perf_counter() - started, 3), "model_calls": 0,
    }


def summarize(runs: list[dict]) -> dict:
    def stat(key):
        vals = [r[key] for r in runs if r[key] is not None]
        return {"mean": round(statistics.mean(vals), 3), "sd": round(statistics.stdev(vals), 3) if len(vals) > 1 else 0.0}
    picks = {}
    for r in runs:
        picks[r["pick"]] = picks.get(r["pick"], 0) + 1
    return {"runs": len(runs), "spearman": stat("spearman"), "pick_test_acc": stat("pick_test_acc"),
            "random_test_acc": stat("random_test_acc"), "fixed_test_acc": stat("fixed_test_acc"),
            "best_test_acc": stat("best_test_acc"),
            "gamer_ranked_first_rate": round(sum(r["gamer_ranked_first"] for r in runs) / len(runs), 3),
            "chains_verified": sum(r["chain_verified"] for r in runs), "picks": picks,
            "seconds_per_run": stat("seconds"), "model_calls": 0}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--rule", default="first_touch")
    ap.add_argument("--seeds", choices=SEEDS, default="tuning")
    args = ap.parse_args()
    label = f"{args.rule}_{args.seeds}"
    RESULTS.mkdir(exist_ok=True)
    report = {}
    with (RESULTS / f"{label}_runs.jsonl").open("w") as evidence:
        for case in ("A", "B", "C"):
            runs = [one_run(case, s, args.rule) for s in SEEDS[args.seeds]]
            for r in runs:
                evidence.write(json.dumps(r) + "\n")
            report[case] = summarize(runs)
    (RESULTS / f"{label}_summary.json").write_text(json.dumps(report, indent=2))
    print(f"{label}")
    print(f"{'case':4} {'spearman':>9} {'pick acc':>9} {'random':>7} {'fixed':>6} {'best':>5} {'gamer#1':>8} {'chains':>7} {'s/run':>6}")
    for case, s in report.items():
        print(f"{case:4} {s['spearman']['mean']:>9.2f} {s['pick_test_acc']['mean']:>9.3f} {s['random_test_acc']['mean']:>7.3f} "
              f"{s['fixed_test_acc']['mean']:>6.3f} {s['best_test_acc']['mean']:>5.3f} {s['gamer_ranked_first_rate']:>8.0%} "
              f"{s['chains_verified']:>4}/{s['runs']} {s['seconds_per_run']['mean']:>6.3f}")
        print(f"     picks: {s['picks']}")


if __name__ == "__main__":
    main()
