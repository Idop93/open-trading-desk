"""Rule-based provider in the style of DayTrader's Sentiment Analyst: a
seeded synthetic "headline/social buzz" feed instead of live Finnhub
news + Reddit, scored for direction/confidence. Deliberately uncorrelated
with the price-only technical provider so the two don't always agree —
the PM agent has to actually combine conflicting views.
"""

from __future__ import annotations

import random

from ..market_data import SyntheticMarket
from ..schemas import ProviderCard, View, now_utc, iso
from .base import Provider


class SentimentHeadlineProvider(Provider):
    def __init__(
        self,
        provider_id: str = "sentiment-headline-01",
        coverage: list[str] | None = None,
        seed: int = 909,
    ):
        self._provider_id = provider_id
        self._coverage = coverage or ["*"]
        self._seed = seed

    def card(self) -> ProviderCard:
        return ProviderCard(
            provider_id=self._provider_id,
            name="Sentiment / Headline Analyst",
            kind="sentiment",
            coverage=self._coverage,
            latency_ms=80,
            cost_per_view=0.0,
        )

    def _synthetic_sentiment(self, ticker: str, as_of_minute: int) -> float:
        """Fixed per (ticker, time-bucket) so repeat calls within the same
        hour agree, like a headline that doesn't change minute to minute."""
        bucket = as_of_minute // 30
        rng = random.Random(f"{self._seed}:{ticker}:{bucket}")
        return rng.uniform(-1.0, 1.0)

    def answer_view(
        self, ticker: str, horizon_minutes: int, as_of_minute: int, market: SyntheticMarket
    ) -> View | None:
        sentiment = self._synthetic_sentiment(ticker, as_of_minute)
        price = market.price_at(ticker, as_of_minute)
        confidence = round(min(0.95, 0.45 + abs(sentiment) * 0.5), 3)

        if sentiment > 0.15:
            return View(
                provider_id=self._provider_id,
                ticker=ticker,
                direction="long",
                entry=round(price, 2),
                stop=round(price * 0.99, 2),
                target=round(price * (1 + 0.006 + abs(sentiment) * 0.01), 2),
                horizon_minutes=horizon_minutes,
                confidence=confidence,
                rationale=f"synthetic headline/social sentiment score {sentiment:+.2f} — net bullish buzz.",
                timestamp=iso(now_utc()),
            )

        return View(
            provider_id=self._provider_id,
            ticker=ticker,
            direction="avoid",
            entry=round(price, 2),
            stop=round(price * 0.99, 2),
            target=round(price * 1.01, 2),
            horizon_minutes=horizon_minutes,
            confidence=confidence,
            rationale=f"synthetic headline/social sentiment score {sentiment:+.2f} — no bullish catalyst.",
            timestamp=iso(now_utc()),
        )
