"""The "outside provider" stand-in — represents an agent run by someone
else (a classmate, a different model) that joins the exchange and gets
judged purely on its track record, same as our own providers.

If ANTHROPIC_API_KEY is set, this really is a separate agent: it calls
Claude with the recent price action and asks for a view. If no key is
set, it falls back to a simple deterministic mean-reversion rule so the
end-to-end demo still runs offline — see README "Known limitations".
"""

from __future__ import annotations

import json
import os
import re

from ..market_data import SyntheticMarket
from ..schemas import ProviderCard, View, now_utc, iso
from .base import Provider

SYSTEM_PROMPT = """You are an independent intraday trading analyst agent taking part in a \
shared signal exchange. You will be given the last 60 minutes of 1-minute bars for a ticker. \
Respond with ONLY a JSON object, no markdown, no prose:
{"direction": "long" | "avoid", "confidence": 0.0-1.0, "rationale": "<=2 sentences"}
"long" means you'd buy now. "avoid" means you see no clean setup. Be decisive — do not hedge."""


def _extract_json(text: str) -> dict:
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if match:
        return json.loads(match.group(0))
    raise ValueError(f"no JSON found in: {text[:200]}")


class ClaudeAnalystProvider(Provider):
    def __init__(
        self,
        provider_id: str = "claude-reasoner-01",
        coverage: list[str] | None = None,
        model: str = "claude-haiku-4-5-20251001",
    ):
        self._provider_id = provider_id
        self._coverage = coverage or ["*"]
        self._model = model
        self._client = None
        if os.environ.get("ANTHROPIC_API_KEY"):
            import anthropic

            self._client = anthropic.Anthropic()

    def card(self) -> ProviderCard:
        return ProviderCard(
            provider_id=self._provider_id,
            name="Claude Reasoner (outside provider)",
            kind="llm-reasoner",
            coverage=self._coverage,
            latency_ms=1500 if self._client else 10,
            cost_per_view=0.002 if self._client else 0.0,
        )

    def answer_view(
        self, ticker: str, horizon_minutes: int, as_of_minute: int, market: SyntheticMarket
    ) -> View | None:
        bars = market.bars_up_to(ticker, as_of_minute)[-60:]
        if len(bars) < 10:
            return None
        price = bars[-1].price

        if self._client is None:
            return self._fallback_view(ticker, horizon_minutes, as_of_minute, market)

        bar_text = ", ".join(f"{b.price:.2f}" for b in bars)
        user_message = f"Ticker: {ticker}\nLast {len(bars)} 1-min closes: [{bar_text}]"
        try:
            response = self._client.messages.create(
                model=self._model,
                max_tokens=256,
                system=SYSTEM_PROMPT,
                messages=[{"role": "user", "content": user_message}],
            )
            data = _extract_json(response.content[0].text)
            direction = data["direction"]
            confidence = float(data["confidence"])
            rationale = str(data["rationale"])
        except Exception as e:
            rationale = f"LLM call failed ({e}); falling back to deterministic rule."
            return self._fallback_view(ticker, horizon_minutes, as_of_minute, market, note=rationale)

        if direction == "long":
            return View(
                provider_id=self._provider_id,
                ticker=ticker,
                direction="long",
                entry=round(price, 2),
                stop=round(price * 0.99, 2),
                target=round(price * 1.012, 2),
                horizon_minutes=horizon_minutes,
                confidence=round(max(0.0, min(1.0, confidence)), 3),
                rationale=rationale,
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
            confidence=round(max(0.0, min(1.0, confidence)), 3),
            rationale=rationale,
            timestamp=iso(now_utc()),
        )

    def _fallback_view(
        self, ticker: str, horizon_minutes: int, as_of_minute: int, market: SyntheticMarket, note: str | None = None
    ) -> View:
        """No ANTHROPIC_API_KEY set: a simple mean-reversion rule stands in
        for the LLM call so the exchange still has a third, independent
        voice to demo offline."""
        bars = market.bars_up_to(ticker, as_of_minute)[-20:]
        price = bars[-1].price
        avg = sum(b.price for b in bars) / len(bars)
        dip_pct = (avg - price) / avg
        rationale = note or (
            f"[no ANTHROPIC_API_KEY — deterministic fallback] price {price:.2f} vs 20-min avg "
            f"{avg:.2f} ({dip_pct:+.2%}); mean-reversion rule."
        )
        if dip_pct > 0.003:
            return View(
                provider_id=self._provider_id,
                ticker=ticker,
                direction="long",
                entry=round(price, 2),
                stop=round(price * 0.99, 2),
                target=round(avg, 2) if avg > price else round(price * 1.008, 2),
                horizon_minutes=horizon_minutes,
                confidence=round(min(0.85, 0.5 + dip_pct * 20), 3),
                rationale=rationale,
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
            confidence=0.55,
            rationale=rationale,
            timestamp=iso(now_utc()),
        )
