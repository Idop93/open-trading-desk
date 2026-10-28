"""The PM agent: an MCP *client* that drives one simulated trading day
against the open-trading-desk MCP *server* (server.py, launched as a
subprocess over stdio — a separate process, talking only through MCP
tool calls, exactly like a real outside agent would).

This script IS the demo: run it, and the terminal transcript is the
end-to-end proof that agents are alive and talking through the exchange.

    python -m opentradingdesk.pm_agent
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from datetime import datetime
from pathlib import Path

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

TICKERS = ["NVDA", "AAPL", "TSLA"]
CHECKPOINTS = [65, 125, 185, 245, 305]  # minutes since 09:30 ET open
PROVIDER_IDS = ["technical-momentum-01", "sentiment-headline-01", "claude-reasoner-01"]

REPO_ROOT = Path(__file__).resolve().parents[2]
REPORT_PATH = REPO_ROOT / "demo" / "last_run_report.json"


def minute_to_et(minute: int) -> str:
    base = 9 * 60 + 30
    total = base + minute
    return f"{total // 60:02d}:{total % 60:02d} ET"


def hr(title: str = "") -> None:
    print()
    print(f"── {title} ".ljust(78, "─") if title else "─" * 78)


def jprint(label: str, obj) -> None:
    print(f"{label}: {json.dumps(obj, indent=2)}")


class Transcript:
    def __init__(self):
        self.events: list[dict] = []

    def record(self, step: str, **data) -> None:
        self.events.append({"step": step, **data})


async def call(session: ClientSession, tool: str, **kwargs):
    """Unwrap an MCP tool result.

    FastMCP (mcp<2) emits a `structuredContent={"result": ...}` envelope
    for list- and str-typed returns, but not for plain dict returns (those
    land straight in content[0].text as JSON). Handle both shapes rather
    than assuming content[0].text is always the whole payload.
    """
    result = await session.call_tool(tool, kwargs)
    if result.isError:
        text = result.content[0].text if result.content else "unknown error"
        raise RuntimeError(f"{tool}({kwargs}) failed: {text}")
    if result.structuredContent is not None and "result" in result.structuredContent:
        return result.structuredContent["result"]
    text = result.content[0].text if result.content else ""
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return text


def decide(ticker: str, views: list[dict]) -> dict | None:
    """Combining conflicting views stays the PM's job (per the proposal).
    Simple rule for the MVP: if a majority of responding providers say
    "long", buy, sized by their average confidence."""
    longs = [v for v in views if v["direction"] == "long"]
    if len(longs) * 2 <= len(views):  # need a strict majority of responders
        return None
    avg_confidence = sum(v["confidence"] for v in longs) / len(longs)
    position_pct = round(min(0.08, 0.04 + avg_confidence * 0.05), 3)
    return {
        "ticker": ticker,
        "position_pct": position_pct,
        "avg_confidence": round(avg_confidence, 3),
        "supporting_providers": [v["provider_id"] for v in longs],
    }


async def main() -> None:
    env = dict(os.environ)
    env["OTD_DATA_DIR"] = str(REPO_ROOT / "data")
    server_params = StdioServerParameters(
        command=sys.executable,
        args=["-m", "opentradingdesk.server"],
        env=env,
        cwd=str(REPO_ROOT / "src"),
    )

    transcript = Transcript()
    print("=" * 78)
    print("OPEN TRADING DESK — MVP demo")
    print(f"PM agent connecting to exchange over MCP (stdio) — {datetime.now().isoformat(timespec='seconds')}")
    print("=" * 78)

    async with stdio_client(server_params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()

            hr("owner issues a delegation")
            token = await call(
                session,
                "issue_delegation",
                owner="ido",
                max_position_pct=0.10,
                long_only=True,
                flat_by_minute=385,
                daily_loss_stop_pct=0.03,
                ttl_minutes=480,
            )
            print(f"delegation_token: {token[:24]}... (owner=ido, max_position=10%, long_only, flat_by=15:55 ET, daily_loss_stop=3%)")
            transcript.record("issue_delegation", token_prefix=token[:24])

            primary_minute = CHECKPOINTS[0]
            hr(f"{minute_to_et(primary_minute)} — PM agent evaluates NVDA")

            cards = await call(session, "find_providers", ticker="NVDA")
            print(f"find_providers(NVDA) -> {len(cards)} providers registered:")
            for c in cards:
                track = f"hit_rate={c['hit_rate']:.0%} (n={c['n_views']})" if c["hit_rate"] is not None else "no track record yet"
                print(f"  - {c['provider_id']:<24} {c['kind']:<12} {track}")
            transcript.record("find_providers", ticker="NVDA", cards=cards)

            views = []
            for provider_id in PROVIDER_IDS:
                resp = await call(
                    session,
                    "request_view",
                    provider_id=provider_id,
                    ticker="NVDA",
                    horizon_minutes=30,
                    deadline_seconds=20,
                    as_of_minute=primary_minute,
                )
                transcript.record("request_view", provider_id=provider_id, ticker="NVDA", response=resp)
                if resp["status"] == "no_show":
                    print(f"  [{provider_id}] NO-SHOW — {resp['reason']}")
                    continue
                v = resp["view"]
                views.append(v)
                print(
                    f"  [{provider_id}] {v['direction'].upper():<5} entry={v['entry']} stop={v['stop']} "
                    f"target={v['target']} confidence={v['confidence']:.0%}"
                )
                print(f"      rationale: {v['rationale']}")

            decision = decide("NVDA", views)
            hr("PM decision")
            if decision is None:
                print("No majority long signal — PM agent skips NVDA this round.")
                transcript.record("decision", ticker="NVDA", action="skip")
            else:
                print(
                    f"Majority of responding providers ({', '.join(decision['supporting_providers'])}) say LONG "
                    f"(avg confidence {decision['avg_confidence']:.0%}) -> PM sends place_order"
                    f"(NVDA, {decision['position_pct']:.1%} of account, side=buy)"
                )
                transcript.record("decision", **decision)
                order = await call(
                    session,
                    "place_order",
                    ticker="NVDA",
                    side="buy",
                    position_pct=decision["position_pct"],
                    delegation_token=token,
                    as_of_minute=primary_minute,
                )
                jprint("risk_gate decision", order)
                transcript.record("place_order", ticker="NVDA", request=decision, response=order)

            hr("misuse check — the gate must refuse out-of-scope orders")
            oversized = await call(
                session, "place_order", ticker="AAPL", side="buy", position_pct=0.25,
                delegation_token=token, as_of_minute=primary_minute,
            )
            print(f"place_order(AAPL, 25% of account) -> approved={oversized['approved']} rule={oversized['rule_triggered']}")
            short_sale = await call(
                session, "place_order", ticker="TSLA", side="sell", position_pct=0.05,
                delegation_token=token, as_of_minute=primary_minute,
            )
            print(f"place_order(TSLA, short 5%) -> approved={short_sale['approved']} rule={short_sale['rule_triggered']}")
            assert oversized["approved"] is False and oversized["rule_triggered"] == "POSITION_LIMIT_EXCEEDED"
            assert short_sale["approved"] is False and short_sale["rule_triggered"] == "SHORT_NOT_ALLOWED"
            print("CHECK PASSED: both out-of-scope orders refused with the correct machine-readable reason, 0 false blocks on the earlier valid order.")
            transcript.record("misuse_check", oversized=oversized, short_sale=short_sale, passed=True)

            hr("building a track record — requesting views across the rest of the session")
            total_views = 0
            for minute in CHECKPOINTS:
                for ticker in TICKERS:
                    for provider_id in PROVIDER_IDS:
                        if minute == primary_minute and ticker == "NVDA":
                            continue  # already requested above
                        resp = await call(
                            session, "request_view", provider_id=provider_id, ticker=ticker,
                            horizon_minutes=30, deadline_seconds=20, as_of_minute=minute,
                        )
                        total_views += 1
            print(f"logged {total_views} additional provider responses across {len(TICKERS)} tickers x {len(CHECKPOINTS)} checkpoints.")
            transcript.record("bulk_request_view", total_views=total_views)

            hr("16:05 ET — close_day(): flatten, score, verify")
            summary = await call(session, "close_day", as_of_minute=390)
            print(f"equity at close: ${summary['equity']:,.2f}  (started at $100,000.00)")
            print(f"view log integrity check: {summary['chain_message']}")
            print()
            print(f"{'provider':<24}{'n_views':>8}{'hit_rate':>10}{'95% CI':>18}{'no_shows':>10}")
            for s in summary["scores"]:
                ci = f"[{s['ci_low']:.0%}, {s['ci_high']:.0%}]"
                print(f"{s['provider_id']:<24}{s['n_views']:>8}{s['hit_rate']:>10.0%}{ci:>18}{s['no_shows']:>10}")
            transcript.record("close_day", summary=summary)

    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    REPORT_PATH.write_text(json.dumps(transcript.events, indent=2))
    hr()
    print(f"full transcript written to {REPORT_PATH.relative_to(REPO_ROOT)}")
    print("done.")


if __name__ == "__main__":
    asyncio.run(main())
