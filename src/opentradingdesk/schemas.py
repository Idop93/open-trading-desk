"""Data contracts for the exchange.

Every message a PM agent, provider agent, or the Risk Gate exchanges is one
of these shapes. This is the "one standard view schema" the proposal calls
for — providers must answer in this shape or the exchange rejects the view.
"""

from __future__ import annotations

from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from typing import Literal, Optional


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat()


@dataclass
class ProviderCard:
    """AgentFacts-style identity card a provider registers once."""

    provider_id: str
    name: str
    kind: str  # "technical" | "sentiment" | "llm-reasoner" | ...
    coverage: list[str]  # tickers this provider will answer for
    latency_ms: int  # typical time to answer request_view
    cost_per_view: float  # arbitrary unit cost, for the PM's cost-per-trade metric
    registered_at: str = field(default_factory=lambda: iso(now_utc()))


@dataclass
class View:
    """A provider's call on one ticker. This is the wire format for
    request_view — anything that doesn't match this shape is rejected."""

    provider_id: str
    ticker: str
    direction: Literal["long", "avoid"]
    entry: float
    stop: float
    target: float
    horizon_minutes: int
    confidence: float  # 0.0-1.0
    rationale: str
    timestamp: str = field(default_factory=lambda: iso(now_utc()))

    def validate(self) -> None:
        if self.direction == "long":
            if not (self.stop < self.entry < self.target):
                raise ValueError(
                    f"View({self.provider_id}, {self.ticker}): long view needs "
                    f"stop < entry < target, got {self.stop} < {self.entry} < {self.target}"
                )
        if not (0.0 <= self.confidence <= 1.0):
            raise ValueError(f"View({self.provider_id}): confidence must be in [0,1]")
        if self.horizon_minutes <= 0:
            raise ValueError(f"View({self.provider_id}): horizon_minutes must be > 0")


@dataclass
class NoShow:
    """What request_view logs when a provider misses its deadline.

    Scored separately from accuracy (availability, not a wrong call) per
    the proposal's failure handling: "a timeout is a no-show."
    """

    provider_id: str
    ticker: str
    reason: str
    timestamp: str = field(default_factory=lambda: iso(now_utc()))


@dataclass
class Delegation:
    """The limits an owner signs off on, decoded from a delegation token."""

    owner: str
    max_position_pct: float
    long_only: bool
    flat_by_minute: int  # minutes since market open, e.g. 385 = 15:55 on a 09:30 open
    daily_loss_stop_pct: float
    expires_at: str
    token_id: str


@dataclass
class RiskDecision:
    approved: bool
    reason: str
    rule_triggered: Optional[str] = None


def to_dict(obj) -> dict:
    return asdict(obj)
