"""Testy M10: metryki i Backtester."""
from backend.adapters.market import SyntheticSource
from backend.backtest import Backtester, compute_metrics, max_drawdown
from backend.risk import RiskConfig


def _generous() -> RiskConfig:
    return RiskConfig(max_trade_notional_usd=1000.0, max_asset_exposure_usd=1_000_000.0,
                      max_total_exposure_usd=1e12, max_open_positions=10,
                      max_trades_per_day=10**9, max_spread_bps=100.0, min_depth_usd=0.0)


# -- metryki ---------------------------------------------------------------- #
def test_max_drawdown():
    assert max_drawdown([0, 5, 3, 8, 2]) == -6.0
    assert max_drawdown([1, 2, 3]) == 0.0


def test_compute_metrics_basic():
    m = compute_metrics([(10.0, 12.0), (-5.0, -4.0), (3.0, 5.0)])
    assert m.num_trades == 3
    assert m.wins == 2
    assert abs(m.win_rate - 2 / 3) < 1e-9
    assert m.gross_profit == 13.0
    assert m.gross_loss == 5.0
    assert abs(m.profit_factor - 2.6) < 1e-9
    assert m.net_pnl == 8.0
    assert m.max_drawdown == -5.0


# -- backtester ------------------------------------------------------------- #
def test_backtest_runs_and_reports():
    result = Backtester(risk_config=_generous(), notional_usd=200.0).run(
        SyntheticSource(steps=300, seed=3)
    )
    assert result.ticks > 0
    assert result.signals > 0
    assert result.entries > 0
    assert "BACKTEST" in result.summary()
    assert isinstance(result.final_net, float)


def test_backtest_is_deterministic():
    a = Backtester(risk_config=_generous()).run(SyntheticSource(steps=200, seed=5))
    b = Backtester(risk_config=_generous()).run(SyntheticSource(steps=200, seed=5))
    assert a.final_net == b.final_net
    assert a.entries == b.entries
