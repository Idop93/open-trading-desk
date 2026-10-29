"""Scorekeeper: scores every logged view against what the ticker actually
did, and publishes a track record per provider.

Accuracy and availability are scored separately (a no-show is not a wrong
call, it's a missed call) per the proposal's failure handling. The score
is a hit rate with a Wilson confidence interval, so a 1-for-1 provider and
a 40-for-50 provider don't look the same after one day.
"""

from __future__ import annotations

import math
import sqlite3
from dataclasses import dataclass
from pathlib import Path

from .market_data import SyntheticMarket
from .view_log import ViewLog

SCHEMA = """
CREATE TABLE IF NOT EXISTS scores (
    provider_id TEXT PRIMARY KEY,
    n_views     INTEGER NOT NULL,
    hits        INTEGER NOT NULL,
    misses      INTEGER NOT NULL,
    expired     INTEGER NOT NULL,
    no_shows    INTEGER NOT NULL,
    hit_rate    REAL NOT NULL,
    ci_low      REAL NOT NULL,
    ci_high     REAL NOT NULL,
    updated_at_minute INTEGER NOT NULL
);
"""


@dataclass
class ScoreRecord:
    provider_id: str
    n_views: int
    hits: int
    misses: int
    expired: int
    no_shows: int
    hit_rate: float
    ci_low: float
    ci_high: float


def _wilson_interval(hits: int, n: int, z: float = 1.96) -> tuple[float, float]:
    """95% Wilson score interval — behaves sanely at small n, unlike a
    naive normal approximation, which is the point: one day of views is a
    small sample and the PM agent needs to see that."""
    if n == 0:
        return (0.0, 0.0)
    phat = hits / n
    denom = 1 + z**2 / n
    center = phat + z**2 / (2 * n)
    spread = z * math.sqrt((phat * (1 - phat) + z**2 / (4 * n)) / n)
    low = (center - spread) / denom
    high = (center + spread) / denom
    return (max(0.0, low), min(1.0, high))


def _outcome(view_payload: dict, market: SyntheticMarket, session_minute: int) -> str:
    """'hit' | 'miss' | 'expired' — walk the price path forward from the
    moment the view was requested to entry+horizon, first touch wins."""
    ticker = view_payload["ticker"]
    horizon = view_payload["horizon_minutes"]
    path = market.path_between(ticker, session_minute, session_minute + horizon)
    if view_payload["direction"] == "avoid":
        # "avoid" is correct if price never ran away upward past target
        # before the horizon elapsed — i.e. there was something to avoid.
        ran_away = any(bar.price >= view_payload["target"] for bar in path)
        return "miss" if ran_away else "hit"

    target, stop = view_payload["target"], view_payload["stop"]
    for bar in path:
        if bar.price <= stop:
            return "miss"
        if bar.price >= target:
            return "hit"
    return "expired"


def _direction_outcome(view_payload: dict, market: SyntheticMarket, session_minute: int) -> str:
    """'hit' | 'miss' — did the price move the way the view said over its horizon?

    Same rule for LONG and AVOID, and every view is decided (none dropped as expired), so a
    provider with no skill scores about 50% whatever direction it always picks. Prices come from
    the market at the request minute, not from the provider's stated entry, so a provider cannot
    improve its score by misreporting entry. Added after the HW7 benchmark showed the first-touch
    rule ranking an always-AVOID provider first in half of the runs.
    """
    ticker = view_payload["ticker"]
    start = market.price_at(ticker, session_minute)
    end = market.price_at(ticker, session_minute + view_payload["horizon_minutes"])
    went_up = end > start
    correct = went_up if view_payload["direction"] == "long" else not went_up
    return "hit" if correct else "miss"


OUTCOME_RULES = {"first_touch": _outcome, "direction": _direction_outcome}


class Scorekeeper:
    def __init__(self, db_path: str | Path, view_log: ViewLog, market: SyntheticMarket,
                 outcome_rule: str = "first_touch"):
        if outcome_rule not in OUTCOME_RULES:
            raise ValueError(f"unknown outcome_rule {outcome_rule!r}; choose from {sorted(OUTCOME_RULES)}")
        self._outcome = OUTCOME_RULES[outcome_rule]
        self._conn = sqlite3.connect(db_path)
        self._conn.row_factory = sqlite3.Row
        self._conn.executescript(SCHEMA)
        self._conn.commit()
        self._view_log = view_log
        self._market = market

    def score_all(self, as_of_minute: int) -> list[ScoreRecord]:
        tallies: dict[str, dict[str, int]] = {}

        for row in self._view_log.all_entries():
            import json

            payload = json.loads(row["payload"])
            provider_id = payload["provider_id"]
            tallies.setdefault(provider_id, {"n_views": 0, "hits": 0, "misses": 0, "expired": 0, "no_shows": 0})

            if row["kind"] == "no_show":
                tallies[provider_id]["no_shows"] += 1
                continue

            tallies[provider_id]["n_views"] += 1
            outcome = self._outcome(payload, self._market, row["session_minute"])
            if outcome == "hit":
                tallies[provider_id]["hits"] += 1
            elif outcome == "miss":
                tallies[provider_id]["misses"] += 1
            else:
                tallies[provider_id]["expired"] += 1

        records = []
        for provider_id, t in tallies.items():
            scored_n = t["n_views"]  # expired views don't count for/against accuracy
            decided = t["hits"] + t["misses"]
            hit_rate = t["hits"] / decided if decided else 0.0
            ci_low, ci_high = _wilson_interval(t["hits"], decided)
            record = ScoreRecord(
                provider_id=provider_id,
                n_views=scored_n,
                hits=t["hits"],
                misses=t["misses"],
                expired=t["expired"],
                no_shows=t["no_shows"],
                hit_rate=hit_rate,
                ci_low=ci_low,
                ci_high=ci_high,
            )
            records.append(record)
            self._persist(record, as_of_minute)

        return sorted(records, key=lambda r: r.hit_rate, reverse=True)

    def get_scores(self, provider_id: str | None = None) -> list[ScoreRecord]:
        query = "SELECT * FROM scores"
        params: tuple = ()
        if provider_id:
            query += " WHERE provider_id = ?"
            params = (provider_id,)
        rows = self._conn.execute(query, params).fetchall()
        return sorted(
            [
                ScoreRecord(
                    provider_id=r["provider_id"],
                    n_views=r["n_views"],
                    hits=r["hits"],
                    misses=r["misses"],
                    expired=r["expired"],
                    no_shows=r["no_shows"],
                    hit_rate=r["hit_rate"],
                    ci_low=r["ci_low"],
                    ci_high=r["ci_high"],
                )
                for r in rows
            ],
            key=lambda r: r.hit_rate,
            reverse=True,
        )

    def _persist(self, record: ScoreRecord, as_of_minute: int) -> None:
        self._conn.execute(
            """
            INSERT INTO scores (provider_id, n_views, hits, misses, expired, no_shows, hit_rate, ci_low, ci_high, updated_at_minute)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(provider_id) DO UPDATE SET
                n_views=excluded.n_views, hits=excluded.hits, misses=excluded.misses,
                expired=excluded.expired, no_shows=excluded.no_shows, hit_rate=excluded.hit_rate,
                ci_low=excluded.ci_low, ci_high=excluded.ci_high, updated_at_minute=excluded.updated_at_minute
            """,
            (
                record.provider_id,
                record.n_views,
                record.hits,
                record.misses,
                record.expired,
                record.no_shows,
                record.hit_rate,
                record.ci_low,
                record.ci_high,
                as_of_minute,
            ),
        )
        self._conn.commit()

    def close(self) -> None:
        self._conn.close()
