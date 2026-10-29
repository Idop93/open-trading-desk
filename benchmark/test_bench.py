"""Sanity checks for the benchmark itself, before trusting any result."""
from bench_providers import KnownSkillProvider, LazyAvoidProvider
from opentradingdesk.market_data import SyntheticMarket
from run_benchmark import FULL_DAY, TICKERS, accuracy
from truth import spearman


def test_spearman_basics():
    assert spearman([1, 2, 3], [10, 20, 30]) == 1.0
    assert spearman([1, 2, 3], [30, 20, 10]) == -1.0


def test_known_skill_is_what_it_claims_over_many_days():
    for p, expected in ((1.0, 1.0), (0.0, 0.5), (0.6, 0.8)):
        accs = [accuracy(KnownSkillProvider(p, s), SyntheticMarket(TICKERS, seed=s), FULL_DAY) for s in range(40)]
        assert abs(sum(accs) / len(accs) - expected) < 0.04, (p, sum(accs) / len(accs))


def test_lazy_avoid_has_no_real_skill():
    accs = [accuracy(LazyAvoidProvider(), SyntheticMarket(TICKERS, seed=s), FULL_DAY) for s in range(40)]
    assert abs(sum(accs) / len(accs) - 0.5) < 0.05
