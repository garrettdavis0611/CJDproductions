from .base import Broker, CostBreakdown, CostModel, OrderFailed, OrderRejected
from .paper import PaperBroker

__all__ = [
    "Broker",
    "CostBreakdown",
    "CostModel",
    "OrderFailed",
    "OrderRejected",
    "PaperBroker",
]


def __getattr__(name: str):
    """Import the live broker lazily so `solders` stays an optional dependency."""
    if name in ("JupiterBroker", "LiveTradingDisabled"):
        from . import jupiter_broker

        return getattr(jupiter_broker, name)
    raise AttributeError(name)
