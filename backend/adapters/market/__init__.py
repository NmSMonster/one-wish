"""Adaptery danych rynkowych (read-only)."""
from .base import MarketDataAdapter, MarketSource
from .replay import ReplaySource
from .synthetic import SyntheticSource

__all__ = ["MarketDataAdapter", "MarketSource", "ReplaySource", "SyntheticSource"]
