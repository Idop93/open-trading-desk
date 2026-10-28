"""Rule-based provider in the style of DayTrader's Technical Analyst:
moving-average crossover + momentum, no LLM call. Deterministic so the
demo is reproducible without API keys.

This is the "two of our DayTrader analysts" half of the proposal's
smallest demo — same signal family (trend/momentum off price bars), ported
to the standard View schema instead of DayTrader's internal
TechnicalAnalystReport shape.
"""

from __future__ import annotations

from ..market_data import SyntheticMarket
from ..schemas import ProviderCard, View, now_utc, iso
from .base import Provider

SHORT_WINDOW = 15
LONG_WINDOW = 60


class TechnicalMomentumProvider(Provider):
    def __init__(self, provider_id: str = "technical-momentum-01", coverage: list[str] | None = None):
        self._provider_id = provider_id
        self._coverage = coverage or ["*"]

    def card(self) -> ProviderCard:
        return ProviderCard(
            provider_id=self._provider_id,
            name="Technical Momentum Analyst",
            kind="technical",
            coverage=self._coverage,
            latency_ms=50,
            cost_per_view=0.0,
        )

    def answer_view(
        self, ticker: str, horizon_minutes: int, as_of_minute: int, market: SyntheticMarket
    ) -> View | None:
        bars = market.bars_up_to(ticker, as_of_minute)
        if len(bars) < LONG_WINDOW + 1:
            return None  # not enough history yet — simulates a provider declining to answer

        price = bars[-1].price
        short_ma = sum(b.price for b in bars[-SHORT_WINDOW:]) / SHORT_WINDOW
        long_ma = sum(b.price for b in bars[-LONG_WINDOW:]) / LONG_WINDOW
        spread_pct = (short_ma - long_ma) / long_ma

        confidence = min(0.95, 0.5 + abs(spread_pct) * 40)

        if spread_pct > 0.0008:
            entry = price
            stop = round(price * 0.995, 2)
            target = round(price * 1.01, 2)
            rationale = (
                f"{SHORT_WINDOW}-min MA ({short_ma:.2f}) is {spread_pct:.2%} above "
                f"{LONG_WINDOW}-min MA ({long_ma:.2f}) — bullish crossover."
            )
            return View(
                provider_id=self._provider_id,
                ticker=ticker,
                direction="long",
                entry=round(entry, 2),
                stop=stop,
                target=target,
                horizon_minutes=horizon_minutes,
                confidence=round(confidence, 3),
                rationale=rationale,
                timestamp=iso(now_utc()),
            )

        rationale = (
            f"{SHORT_WINDOW}-min MA ({short_ma:.2f}) is not meaningfully above "
            f"{LONG_WINDOW}-min MA ({long_ma:.2f}) ({spread_pct:.2%}) — no clean trend."
        )
        return View(
            provider_id=self._provider_id,
            ticker=ticker,
            direction="avoid",
            entry=round(price, 2),
            stop=round(price * 0.995, 2),
            target=round(price * 1.01, 2),
            horizon_minutes=horizon_minutes,
            confidence=round(min(0.9, 0.5 + abs(spread_pct) * 40), 3),
            rationale=rationale,
            timestamp=iso(now_utc()),
        )
