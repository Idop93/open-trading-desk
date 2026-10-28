"""Synthetic 1-minute bar generator.

The MVP does not call Alpaca/Finnhub — it replays a seeded random walk per
ticker so the whole exchange runs offline, instantly, and reproducibly.
This is the one deliberate simplification from the full proposal (see
README "Known limitations"): providers and the Scorekeeper both read bars
from here instead of live/historical market data.

Swapping this module for a real Alpaca/yfinance feed is the only change
needed to move the rest of the system (registry, view log, risk gate,
scorekeeper, providers) onto live data.
"""

from __future__ import annotations

import random
from dataclasses import dataclass

MARKET_OPEN_MINUTE = 0  # 09:30 ET == minute 0 of the session
SESSION_MINUTES = 390  # 09:30-16:00 ET


@dataclass
class Bar:
    minute: int  # minutes since 09:30 ET
    price: float


class SyntheticMarket:
    """One seeded random walk per ticker, generated once up front so that
    "as of minute N" and "the eventual outcome" are both reads of the same
    fixed path — exactly what lets the Scorekeeper check a view against
    what the ticker "actually did" later in the session.
    """

    def __init__(self, tickers: list[str], seed: int = 20261028, vol: float = 0.0012):
        self._paths: dict[str, list[Bar]] = {}
        for i, ticker in enumerate(tickers):
            rng = random.Random(seed + i * 7919)  # distinct but reproducible per ticker
            price = 100.0 + rng.uniform(-20, 80)
            bars = [Bar(minute=0, price=round(price, 2))]
            for minute in range(1, SESSION_MINUTES + 1):
                drift = rng.gauss(0, vol)
                price = max(0.5, price * (1 + drift))
                bars.append(Bar(minute=minute, price=round(price, 2)))
            self._paths[ticker] = bars

    def tickers(self) -> list[str]:
        return list(self._paths.keys())

    def bars_up_to(self, ticker: str, minute: int) -> list[Bar]:
        return [b for b in self._paths[ticker] if b.minute <= minute]

    def price_at(self, ticker: str, minute: int) -> float:
        minute = max(0, min(minute, SESSION_MINUTES))
        return self._paths[ticker][minute].price

    def path_between(self, ticker: str, start_minute: int, end_minute: int) -> list[Bar]:
        end_minute = min(end_minute, SESSION_MINUTES)
        return [b for b in self._paths[ticker] if start_minute <= b.minute <= end_minute]
