"""Risk Gate: the only path to the broker.

The PM agent never holds broker credentials — it only ever holds a
delegation token. Every order passes through here, gets checked against
the owner's signed limits, and is approved+simulated-filled or refused
with a machine-readable reason. The gate fails closed: once flattened
(loss stop hit, revoked, or past flat-by), it refuses every new buy until
reset.

"Alpaca paper" isn't wired up in this MVP — place_order fills against the
synthetic market module instead of a real broker call. Swapping in a real
Alpaca client only touches `_simulated_fill_price`; nothing else changes.
See README "Known limitations".
"""

from __future__ import annotations

import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path

from .delegation import DelegationAuthority, DelegationError
from .market_data import SyntheticMarket
from .schemas import RiskDecision

SCHEMA = """
CREATE TABLE IF NOT EXISTS orders (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    ticker        TEXT NOT NULL,
    side          TEXT NOT NULL,
    position_pct  REAL NOT NULL,
    minute        INTEGER NOT NULL,
    approved      INTEGER NOT NULL,
    reason        TEXT NOT NULL,
    rule_triggered TEXT,
    fill_price    REAL,
    created_at    REAL NOT NULL
);
"""

STARTING_EQUITY = 100_000.0


@dataclass
class _Position:
    ticker: str
    entry_price: float
    position_pct: float
    shares: float
    opened_minute: int


class RiskGate:
    def __init__(
        self,
        db_path: str | Path,
        authority: DelegationAuthority,
        market: SyntheticMarket,
    ):
        self._conn = sqlite3.connect(db_path)
        self._conn.row_factory = sqlite3.Row
        self._conn.executescript(SCHEMA)
        self._conn.commit()
        self._authority = authority
        self._market = market
        self._positions: dict[str, _Position] = {}
        self._realized_pnl = 0.0
        self._flattened = False
        self._flatten_reason: str | None = None

    def equity(self, current_minute: int) -> float:
        unrealized = sum(
            (self._market.price_at(p.ticker, current_minute) - p.entry_price) * p.shares
            for p in self._positions.values()
        )
        return STARTING_EQUITY + self._realized_pnl + unrealized

    def place_order(
        self,
        ticker: str,
        side: str,
        position_pct: float,
        delegation_token: str,
        current_minute: int,
    ) -> RiskDecision:
        decision = self._evaluate(ticker, side, position_pct, delegation_token, current_minute)
        fill_price = None
        if decision.approved:
            fill_price = self._open_position(ticker, position_pct, current_minute)
        self._log(ticker, side, position_pct, current_minute, decision, fill_price)
        return decision

    def _evaluate(
        self, ticker: str, side: str, position_pct: float, delegation_token: str, current_minute: int
    ) -> RiskDecision:
        try:
            delegation = self._authority.verify(delegation_token)
        except DelegationError as e:
            return RiskDecision(approved=False, reason=str(e), rule_triggered=str(e))

        if self._flattened:
            return RiskDecision(
                approved=False, reason=f"gate is flattened: {self._flatten_reason}", rule_triggered="GATE_FLATTENED"
            )
        if current_minute > delegation.flat_by_minute:
            return RiskDecision(
                approved=False, reason="order placed after flat-by time", rule_triggered="AFTER_FLAT_BY"
            )
        if delegation.long_only and side != "buy":
            return RiskDecision(
                approved=False, reason="delegation is long-only", rule_triggered="SHORT_NOT_ALLOWED"
            )
        if position_pct > delegation.max_position_pct:
            return RiskDecision(
                approved=False,
                reason=f"{position_pct:.1%} exceeds max position {delegation.max_position_pct:.1%}",
                rule_triggered="POSITION_LIMIT_EXCEEDED",
            )
        if ticker in self._positions:
            return RiskDecision(
                approved=False, reason=f"already holding a position in {ticker}", rule_triggered="DUPLICATE_POSITION"
            )
        return RiskDecision(approved=True, reason="within limits")

    def _open_position(self, ticker: str, position_pct: float, current_minute: int) -> float:
        price = self._market.price_at(ticker, current_minute)
        notional = STARTING_EQUITY * position_pct
        shares = notional / price
        self._positions[ticker] = _Position(ticker, price, position_pct, shares, current_minute)
        return price

    def mark_to_market_and_check_loss_stop(self, delegation_token: str, current_minute: int) -> RiskDecision | None:
        """Call periodically (or once at end of day for the MVP). If the
        day's drawdown breaches the owner's daily_loss_stop_pct, flattens
        every open position and fails the gate closed."""
        try:
            delegation = self._authority.verify(delegation_token)
        except DelegationError:
            return None
        drawdown_pct = (STARTING_EQUITY - self.equity(current_minute)) / STARTING_EQUITY
        if drawdown_pct >= delegation.daily_loss_stop_pct and not self._flattened:
            self.flatten(current_minute, reason=f"daily loss stop hit ({drawdown_pct:.2%})")
            return RiskDecision(approved=False, reason="daily loss stop hit, positions flattened", rule_triggered="DAILY_LOSS_STOP")
        return None

    def flatten(self, current_minute: int, reason: str) -> None:
        for ticker, pos in list(self._positions.items()):
            exit_price = self._market.price_at(ticker, current_minute)
            self._realized_pnl += (exit_price - pos.entry_price) * pos.shares
            del self._positions[ticker]
        self._flattened = True
        self._flatten_reason = reason

    def open_positions(self) -> list[_Position]:
        return list(self._positions.values())

    def _log(self, ticker, side, position_pct, minute, decision: RiskDecision, fill_price: float | None) -> None:
        self._conn.execute(
            """
            INSERT INTO orders (ticker, side, position_pct, minute, approved, reason, rule_triggered, fill_price, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                ticker,
                side,
                position_pct,
                minute,
                int(decision.approved),
                decision.reason,
                decision.rule_triggered,
                fill_price,
                time.time(),
            ),
        )
        self._conn.commit()

    def order_log(self) -> list[dict]:
        rows = self._conn.execute("SELECT * FROM orders ORDER BY id ASC").fetchall()
        return [dict(r) for r in rows]

    def close(self) -> None:
        self._conn.close()
