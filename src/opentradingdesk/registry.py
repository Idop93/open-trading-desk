"""Signal Exchange: the provider registry.

A SQLite-backed card catalog. Providers register once; PM agents call
find_providers() to discover who covers a ticker, ranked by score (falls
back to registration order until the Scorekeeper has published anything).
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from .schemas import ProviderCard

SCHEMA = """
CREATE TABLE IF NOT EXISTS providers (
    provider_id   TEXT PRIMARY KEY,
    name          TEXT NOT NULL,
    kind          TEXT NOT NULL,
    coverage      TEXT NOT NULL,   -- comma-separated tickers
    latency_ms    INTEGER NOT NULL,
    cost_per_view REAL NOT NULL,
    registered_at TEXT NOT NULL
);
"""


class Registry:
    def __init__(self, db_path: str | Path):
        self._conn = sqlite3.connect(db_path)
        self._conn.row_factory = sqlite3.Row
        self._conn.executescript(SCHEMA)
        self._conn.commit()

    def register(self, card: ProviderCard) -> None:
        self._conn.execute(
            """
            INSERT INTO providers (provider_id, name, kind, coverage, latency_ms, cost_per_view, registered_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(provider_id) DO UPDATE SET
                name=excluded.name, kind=excluded.kind, coverage=excluded.coverage,
                latency_ms=excluded.latency_ms, cost_per_view=excluded.cost_per_view
            """,
            (
                card.provider_id,
                card.name,
                card.kind,
                ",".join(card.coverage),
                card.latency_ms,
                card.cost_per_view,
                card.registered_at,
            ),
        )
        self._conn.commit()

    def get(self, provider_id: str) -> ProviderCard | None:
        row = self._conn.execute(
            "SELECT * FROM providers WHERE provider_id = ?", (provider_id,)
        ).fetchone()
        return self._row_to_card(row) if row else None

    def find_providers(self, ticker: str) -> list[ProviderCard]:
        rows = self._conn.execute("SELECT * FROM providers").fetchall()
        cards = [self._row_to_card(r) for r in rows]
        return [c for c in cards if ticker in c.coverage or "*" in c.coverage]

    def all_providers(self) -> list[ProviderCard]:
        rows = self._conn.execute("SELECT * FROM providers").fetchall()
        return [self._row_to_card(r) for r in rows]

    @staticmethod
    def _row_to_card(row: sqlite3.Row) -> ProviderCard:
        return ProviderCard(
            provider_id=row["provider_id"],
            name=row["name"],
            kind=row["kind"],
            coverage=row["coverage"].split(","),
            latency_ms=row["latency_ms"],
            cost_per_view=row["cost_per_view"],
            registered_at=row["registered_at"],
        )

    def close(self) -> None:
        self._conn.close()
