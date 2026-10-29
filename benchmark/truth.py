"""The independent result check: did the price actually go the way the view said?

Deliberately does NOT use the Scorekeeper's code. A LONG view is correct if the price at
entry + horizon is above the entry price; an AVOID view is correct if it is not.
"""
from __future__ import annotations

from opentradingdesk.market_data import SyntheticMarket


def view_is_correct(view: dict, market: SyntheticMarket, as_of_minute: int) -> bool:
    end_price = market.price_at(view["ticker"], as_of_minute + view["horizon_minutes"])
    went_up = end_price > market.price_at(view["ticker"], as_of_minute)
    return went_up if view["direction"] == "long" else not went_up


def spearman(xs: list[float], ys: list[float]) -> float:
    """Spearman rank correlation with average ranks for ties (no scipy needed)."""
    def ranks(v: list[float]) -> list[float]:
        order = sorted(range(len(v)), key=lambda i: v[i])
        r = [0.0] * len(v)
        i = 0
        while i < len(order):
            j = i
            while j + 1 < len(order) and v[order[j + 1]] == v[order[i]]:
                j += 1
            for k in range(i, j + 1):
                r[order[k]] = (i + j) / 2
            i = j + 1
        return r
    rx, ry = ranks(xs), ranks(ys)
    mx, my = sum(rx) / len(rx), sum(ry) / len(ry)
    cov = sum((a - mx) * (b - my) for a, b in zip(rx, ry))
    var = (sum((a - mx) ** 2 for a in rx) * sum((b - my) ** 2 for b in ry)) ** 0.5
    return cov / var if var else 0.0
