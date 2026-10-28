from __future__ import annotations

from abc import ABC, abstractmethod

from ..market_data import SyntheticMarket
from ..schemas import ProviderCard, View


class Provider(ABC):
    """Interface every provider agent implements. The exchange only ever
    talks to providers through these two methods — swapping a rule-based
    provider for a real external agent means implementing this interface,
    nothing about the registry/log/gate/scorekeeper changes."""

    @abstractmethod
    def card(self) -> ProviderCard: ...

    @abstractmethod
    def answer_view(
        self, ticker: str, horizon_minutes: int, as_of_minute: int, market: SyntheticMarket
    ) -> View | None:
        """Return a View, or None to simulate a no-show/timeout."""
        ...
