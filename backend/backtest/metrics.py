"""Metryki skuteczności z listy zamkniętych transakcji."""
from __future__ import annotations

import math
import statistics
from dataclasses import dataclass


@dataclass
class Metrics:
    num_trades: int
    wins: int
    win_rate: float
    gross_profit: float
    gross_loss: float
    profit_factor: float
    net_pnl: float
    gross_pnl: float
    max_drawdown: float
    avg_trade: float


def max_drawdown(cumulative: list[float]) -> float:
    """Maksymalne obsunięcie kapitału (≤ 0) z krzywej skumulowanego PnL."""
    peak = -math.inf
    mdd = 0.0
    for v in cumulative:
        peak = max(peak, v)
        mdd = min(mdd, v - peak)
    return mdd


def compute_metrics(trades: list[tuple[float, float]]) -> Metrics:
    """trades: lista (net_pnl, gross_pnl) per zamknięta transakcja."""
    nets = [t[0] for t in trades]
    grosses = [t[1] for t in trades]
    n = len(nets)
    wins = sum(1 for x in nets if x > 0)
    gross_profit = sum(x for x in nets if x > 0)
    gross_loss = abs(sum(x for x in nets if x < 0))
    profit_factor = (gross_profit / gross_loss) if gross_loss > 0 else math.inf if gross_profit > 0 else 0.0

    cum = []
    running = 0.0
    for x in nets:
        running += x
        cum.append(running)

    return Metrics(
        num_trades=n,
        wins=wins,
        win_rate=(wins / n) if n else 0.0,
        gross_profit=gross_profit,
        gross_loss=gross_loss,
        profit_factor=profit_factor,
        net_pnl=sum(nets),
        gross_pnl=sum(grosses),
        max_drawdown=max_drawdown(cum),
        avg_trade=statistics.fmean(nets) if nets else 0.0,
    )
