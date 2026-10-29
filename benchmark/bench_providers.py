"""Test providers with KNOWN skill, so the benchmark knows the right ranking in advance.

Only these test providers may peek at the future price path; that is how their skill is set.
They implement the team's real Provider interface, so the exchange treats them like any provider.
"""
from __future__ import annotations

import random

from opentradingdesk.market_data import SyntheticMarket
from opentradingdesk.providers.base import Provider
from opentradingdesk.schemas import ProviderCard, View


def _view(pid: str, ticker: str, price: float, direction: str, horizon: int, confidence: float) -> View:
    # Same geometry as the team's technical provider: stop -0.5%, target +1%.
    return View(provider_id=pid, ticker=ticker, direction=direction, entry=price, stop=round(price * 0.995, 2),
                target=round(price * 1.01, 2), horizon_minutes=horizon, confidence=confidence,
                rationale="benchmark test provider")


class _TestProvider(Provider):
    def __init__(self, provider_id: str, true_skill: float | None):
        self.provider_id, self.true_skill = provider_id, true_skill

    def card(self) -> ProviderCard:
        return ProviderCard(provider_id=self.provider_id, name=self.provider_id, kind="benchmark",
                            coverage=["*"], latency_ms=1, cost_per_view=0.0)


class KnownSkillProvider(_TestProvider):
    """Correct direction with probability p (by peeking ahead), otherwise a coin flip.
    Expected directional accuracy = 0.5 + p/2."""

    def __init__(self, p: float, seed: int):
        super().__init__(f"skill-p{int(p * 100):02d}", p)
        self._rng = random.Random(f"{seed}-{p}")

    def answer_view(self, ticker: str, horizon_minutes: int, as_of_minute: int, market: SyntheticMarket) -> View:
        now = market.price_at(ticker, as_of_minute)
        up = market.price_at(ticker, as_of_minute + horizon_minutes) > now
        if self._rng.random() < self.true_skill:
            direction = "long" if up else "avoid"
        else:
            direction = self._rng.choice(["long", "avoid"])
        return _view(self.provider_id, ticker, now, direction, horizon_minutes, 0.6)


class LazyAvoidProvider(_TestProvider):
    """Always AVOID. Zero skill."""

    def __init__(self):
        super().__init__("lazy-avoid", 0.0)

    def answer_view(self, ticker, horizon_minutes, as_of_minute, market) -> View:
        return _view(self.provider_id, ticker, market.price_at(ticker, as_of_minute), "avoid", horizon_minutes, 0.6)


class LiarLongProvider(_TestProvider):
    """Always LONG with 95% confidence. Zero skill, maximum self-reported confidence."""

    def __init__(self):
        super().__init__("liar-long", 0.0)

    def answer_view(self, ticker, horizon_minutes, as_of_minute, market) -> View:
        return _view(self.provider_id, ticker, market.price_at(ticker, as_of_minute), "long", horizon_minutes, 0.95)
