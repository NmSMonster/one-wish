"""Backtester (M10) — replay danych przez pełny pipeline + zbiór metryk.

Puszcza dowolne źródło (ReplaySource z realnych danych albo SyntheticSource)
przez ten sam Pipeline co tryb live, na paper brokerze, i liczy statystyki:
liczbę sygnałów, wejść, odrzuceń ryzyka oraz metryki PnL per transakcja.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass

from ..adapters.exchange.paper import PaperBrokerAdapter
from ..adapters.market.base import MarketDataAdapter, MarketSource
from ..app.pipeline import Pipeline
from ..core.bus import EventBus
from ..core.clock import SimClock
from ..core.events import Event, EventType
from ..risk import RiskConfig
from .metrics import Metrics, compute_metrics


@dataclass
class BacktestResult:
    ticks: int
    signals: int
    entries: int
    exits: int
    risk_rejected: int
    avg_signal_edge_bps: float
    metrics: Metrics
    final_realized: float
    final_fees: float
    final_funding: float
    final_net: float

    def summary(self) -> str:
        m = self.metrics
        pf = "∞" if m.profit_factor == float("inf") else f"{m.profit_factor:.2f}"
        return "\n".join([
            "=== BACKTEST ===",
            f"ticki:            {self.ticks}",
            f"sygnały (EDGE):   {self.signals}",
            f"wejścia:          {self.entries}",
            f"wyjścia:          {self.exits}",
            f"odrzucenia ryzyka:{self.risk_rejected}",
            f"śr. edge sygnału: {self.avg_signal_edge_bps:+.2f} bps",
            f"transakcje:       {m.num_trades}  (win rate {m.win_rate*100:.1f}%)",
            f"profit factor:    {pf}",
            f"max drawdown:     {m.max_drawdown:.2f}$",
            f"funding zebrany:  {self.final_funding:+.2f}$",
            f"prowizje:         {self.final_fees:.2f}$",
            f"PnL NETTO:        {self.final_net:+.2f}$",
        ])


class _Collector:
    def __init__(self) -> None:
        self.signal_edges: list[float] = []
        self.entries = 0
        self.exits = 0
        self.rejected = 0
        self.closed: list = []

    def attach(self, bus: EventBus) -> None:
        bus.subscribe(EventType.EDGE_DETECTED, self._on_signal)
        bus.subscribe(EventType.POSITION_OPENED, self._on_open)
        bus.subscribe(EventType.POSITION_CLOSED, self._on_close)
        bus.subscribe(EventType.RISK_REJECTED, self._on_reject)

    def _on_signal(self, e: Event) -> None:
        self.signal_edges.append(e.payload.expected_net_edge_bps)

    def _on_open(self, e: Event) -> None:
        self.entries += 1

    def _on_close(self, e: Event) -> None:
        self.exits += 1
        self.closed.append(e.payload)

    def _on_reject(self, e: Event) -> None:
        self.rejected += 1


class Backtester:
    def __init__(self, *, risk_config: RiskConfig | None = None,
                 notional_usd: float = 200.0, broker=None,
                 policy_mode: str = "carry") -> None:
        self.risk_config = risk_config
        self.notional_usd = notional_usd
        self.broker = broker
        self.policy_mode = policy_mode

    def run(self, source: MarketSource) -> BacktestResult:
        bus = EventBus()
        clock = SimClock()
        broker = self.broker or PaperBrokerAdapter(slippage_bps=1.0, seed=1, clock=clock)
        pipe = Pipeline(bus, risk_config=self.risk_config, notional_usd=self.notional_usd,
                        broker=broker, clock=clock, policy_mode=self.policy_mode)
        collector = _Collector()
        collector.attach(bus)

        adapter = MarketDataAdapter(source, bus, clock)
        ticks = asyncio.run(adapter.run())

        trades = [(p.realized - p.fees + p.funding_accrued, p.realized) for p in collector.closed]
        metrics = compute_metrics(trades)
        avg_edge = (sum(collector.signal_edges) / len(collector.signal_edges)
                    if collector.signal_edges else 0.0)

        return BacktestResult(
            ticks=ticks,
            signals=len(collector.signal_edges),
            entries=collector.entries,
            exits=collector.exits,
            risk_rejected=collector.rejected,
            avg_signal_edge_bps=avg_edge,
            metrics=metrics,
            final_realized=pipe.book.realized_pnl,
            final_fees=pipe.book.fees_paid,
            final_funding=pipe.book.funding_collected,
            final_net=pipe.book.realized_pnl - pipe.book.fees_paid + pipe.book.funding_collected,
        )
