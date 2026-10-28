"""The MCP server: this is the "Interface: MCP tools for PMs" from the
proposal. A PM agent (pm_agent.py, or any other MCP client — Claude
Desktop, another classmate's agent) talks to the exchange only through
these tools. It never touches the SQLite files or the Risk Gate directly.

Run standalone for manual testing:
    python -m opentradingdesk.server
"""

from __future__ import annotations

import os
from pathlib import Path

from mcp.server.fastmcp import FastMCP

from .exchange import Exchange

DATA_DIR = Path(os.environ.get("OTD_DATA_DIR", Path(__file__).resolve().parents[2] / "data"))

mcp = FastMCP("open-trading-desk")
_exchange = Exchange(DATA_DIR)


@mcp.tool()
def find_providers(ticker: str) -> list[dict]:
    """List registered provider agents that cover `ticker`, ranked by
    current hit rate (providers with no track record yet sort last)."""
    return _exchange.find_providers(ticker)


@mcp.tool()
def request_view(
    provider_id: str,
    ticker: str,
    horizon_minutes: int = 30,
    deadline_seconds: float = 20.0,
    as_of_minute: int = 0,
) -> dict:
    """Ask one provider for a view on `ticker`. `as_of_minute` is minutes
    since the 09:30 ET open on the exchange's synthetic trading day (0-390).
    Returns either {"status": "ok", "view": {...}} or
    {"status": "no_show", "reason": "..."} if the provider missed the
    deadline or declined to answer. Every outcome is written to the
    append-only view log either way."""
    return _exchange.request_view(provider_id, ticker, horizon_minutes, deadline_seconds, as_of_minute)


@mcp.tool()
def get_scores(as_of_minute: int | None = None, provider_id: str | None = None) -> list[dict]:
    """Published track record per provider: hit rate with a 95% confidence
    interval, plus no-shows (scored separately from accuracy). Pass
    `as_of_minute` to (re)score every logged view against the market as of
    that minute first; omit it to just read the last published scores."""
    return _exchange.get_scores(as_of_minute, provider_id)


@mcp.tool()
def issue_delegation(
    owner: str = "ido",
    max_position_pct: float = 0.10,
    long_only: bool = True,
    flat_by_minute: int = 385,
    daily_loss_stop_pct: float = 0.03,
    ttl_minutes: int = 480,
) -> str:
    """Demo-only convenience: in production the owner signs this outside
    the PM agent's reach and only ever hands the PM the resulting token.
    Returns a delegation token place_order() can spend against."""
    return _exchange.authority.issue(owner, max_position_pct, long_only, flat_by_minute, daily_loss_stop_pct, ttl_minutes)


@mcp.tool()
def revoke_delegation(delegation_token: str) -> str:
    """Owner-side kill switch: revokes a token so every subsequent
    place_order() call using it is refused, even if it hasn't expired."""
    _exchange.authority.revoke(delegation_token)
    return "revoked"


@mcp.tool()
def place_order(ticker: str, side: str, position_pct: float, delegation_token: str, as_of_minute: int = 0) -> dict:
    """Route an order through the Risk Gate. Approved or refused with a
    machine-readable `rule_triggered` reason (e.g. POSITION_LIMIT_EXCEEDED,
    SHORT_NOT_ALLOWED, AFTER_FLAT_BY, DELEGATION_REVOKED,
    DELEGATION_EXPIRED, DAILY_LOSS_STOP). The gate holds the (simulated)
    broker access, never the PM agent."""
    return _exchange.place_order(ticker, side, position_pct, delegation_token, as_of_minute)


@mcp.tool()
def close_day(as_of_minute: int = 390) -> dict:
    """End-of-session: flattens any open positions, runs the Scorekeeper
    over every logged view, and verifies the view log's hash chain. This is
    the "independent recomputation" check — chain_verified=True means no
    row in the log was edited or backdated after it was written."""
    return _exchange.close_day(as_of_minute)


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
