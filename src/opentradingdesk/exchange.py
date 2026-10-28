"""Wires the three services together: Signal Exchange (registry + view
log), Scorekeeper, and Risk Gate. This is the thing the MCP server exposes
and the thing the tests exercise directly, so both go through the exact
same code path.
"""

from __future__ import annotations

import time
from pathlib import Path

from .delegation import DelegationAuthority
from .market_data import SyntheticMarket
from .providers.base import Provider
from .providers.claude_analyst import ClaudeAnalystProvider
from .providers.sentiment_headline import SentimentHeadlineProvider
from .providers.technical_momentum import TechnicalMomentumProvider
from .registry import Registry
from .risk_gate import RiskGate
from .schemas import NoShow, View, now_utc, iso
from .scorekeeper import Scorekeeper
from .view_log import ViewLog

DEFAULT_TICKERS = ["NVDA", "AAPL", "TSLA"]


class Exchange:
    def __init__(self, data_dir: str | Path, tickers: list[str] | None = None, market_seed: int = 20261028):
        data_dir = Path(data_dir)
        data_dir.mkdir(parents=True, exist_ok=True)
        self.tickers = tickers or DEFAULT_TICKERS

        self.market = SyntheticMarket(self.tickers, seed=market_seed)
        self.registry = Registry(data_dir / "registry.db")
        self.view_log = ViewLog(data_dir / "view_log.db")
        self.authority = DelegationAuthority(data_dir / "delegation.db")
        self.risk_gate = RiskGate(data_dir / "orders.db", self.authority, self.market)
        self.scorekeeper = Scorekeeper(data_dir / "scores.db", self.view_log, self.market)

        self._providers: dict[str, Provider] = {}
        self._bootstrap_providers()

    def _bootstrap_providers(self) -> None:
        providers: list[Provider] = [
            TechnicalMomentumProvider(coverage=self.tickers),
            SentimentHeadlineProvider(coverage=self.tickers),
            ClaudeAnalystProvider(coverage=self.tickers),
        ]
        for p in providers:
            self._providers[p.card().provider_id] = p
            self.registry.register(p.card())

    # ── PM-facing tools ────────────────────────────────────────────

    def find_providers(self, ticker: str) -> list[dict]:
        scores = {s.provider_id: s for s in self.scorekeeper.get_scores()}
        results = []
        for card in self.registry.find_providers(ticker):
            score = scores.get(card.provider_id)
            results.append(
                {
                    "provider_id": card.provider_id,
                    "name": card.name,
                    "kind": card.kind,
                    "latency_ms": card.latency_ms,
                    "cost_per_view": card.cost_per_view,
                    "hit_rate": score.hit_rate if score else None,
                    "n_views": score.n_views if score else 0,
                    "ci_low": score.ci_low if score else None,
                    "ci_high": score.ci_high if score else None,
                }
            )
        results.sort(key=lambda r: (r["hit_rate"] is None, -(r["hit_rate"] or 0)))
        return results

    def request_view(
        self, provider_id: str, ticker: str, horizon_minutes: int, deadline_seconds: float, as_of_minute: int
    ) -> dict:
        provider = self._providers.get(provider_id)
        if provider is None:
            raise ValueError(f"unknown provider_id {provider_id!r}")

        start = time.monotonic()
        view = provider.answer_view(ticker, horizon_minutes, as_of_minute, self.market)
        elapsed = time.monotonic() - start

        if view is None or elapsed > deadline_seconds:
            reason = "no view returned" if view is None else f"exceeded {deadline_seconds}s deadline ({elapsed:.1f}s)"
            no_show = NoShow(provider_id=provider_id, ticker=ticker, reason=reason, timestamp=iso(now_utc()))
            row_hash = self.view_log.log_no_show(no_show, as_of_minute)
            return {"status": "no_show", "reason": reason, "log_hash": row_hash}

        view.validate()
        row_hash = self.view_log.log_view(view, as_of_minute)
        return {"status": "ok", "view": view.__dict__, "log_hash": row_hash}

    def get_scores(self, as_of_minute: int | None = None, provider_id: str | None = None) -> list[dict]:
        if as_of_minute is not None:
            self.scorekeeper.score_all(as_of_minute)
        records = self.scorekeeper.get_scores(provider_id)
        return [r.__dict__ for r in records]

    def place_order(self, ticker: str, side: str, position_pct: float, delegation_token: str, as_of_minute: int) -> dict:
        decision = self.risk_gate.place_order(ticker, side, position_pct, delegation_token, as_of_minute)
        return decision.__dict__

    # ── demo/orchestration helpers (not PM-facing MCP tools) ────────

    def mark_to_market(self, delegation_token: str, as_of_minute: int) -> dict | None:
        decision = self.risk_gate.mark_to_market_and_check_loss_stop(delegation_token, as_of_minute)
        return decision.__dict__ if decision else None

    def close_day(self, as_of_minute: int) -> dict:
        self.risk_gate.flatten(as_of_minute, reason="end of session (flat by close)")
        chain_ok, chain_message = self.view_log.verify_chain()
        scores = self.get_scores(as_of_minute=as_of_minute)
        return {
            "equity": self.risk_gate.equity(as_of_minute),
            "orders": self.risk_gate.order_log(),
            "scores": scores,
            "chain_verified": chain_ok,
            "chain_message": chain_message,
        }

    def close(self) -> None:
        self.registry.close()
        self.view_log.close()
        self.authority.close()
        self.risk_gate.close()
        self.scorekeeper.close()
