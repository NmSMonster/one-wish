"""RiskManager (M6) — moduł, który może powiedzieć „NIE".

Nawet jeśli strategia chce wejść, Risk Manager ma prawo weta. Sprawdza limity:
dzienna strata, nominał transakcji, ekspozycja na aktywo, łączna ekspozycja
(proxy na korelację), liczba pozycji, liczba transakcji, spread, płynność, oraz
stan kill switch. Zamknięcia (CLOSE / FLATTEN) są zawsze dozwolone — redukują
ryzyko, więc nie blokujemy ich nigdy, nawet po kill switchu.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, fields

from ..core.bus import EventBus
from ..core.clock import Clock, RealClock
from ..core.events import Event, EventType, Severity
from ..core.types import Asset, MarketTick, PnLSnapshot, Position, RiskDecision, TradeIntent

log = logging.getLogger("onewish.risk")


@dataclass
class RiskConfig:
    max_daily_loss_usd: float = 50.0
    max_trade_notional_usd: float = 250.0
    max_asset_exposure_usd: float = 500.0
    max_total_exposure_usd: float = 1500.0
    max_open_positions: int = 6
    max_trades_per_day: int = 200
    max_spread_bps: float = 8.0
    min_depth_usd: float = 50_000.0
    # --- margin / likwidacja nogi perp ---
    perp_leverage: float = 3.0              # niska dźwignia → liquidation daleko (przeżyj ~+33%)
    maintenance_margin_rate: float = 0.005
    margin_warn_health: float = 1.8         # ostrzeżenie, gdy zdrowie marginu spada
    margin_flatten_health: float = 1.3      # kontrolowany flatten ZANIM likwidacja (health=1)
    live_trading_enabled: bool = False

    @classmethod
    def from_yaml(cls, path: str) -> "RiskConfig":
        import yaml
        with open(path, "r", encoding="utf-8") as fh:
            data = yaml.safe_load(fh) or {}
        known = {f.name for f in fields(cls)}
        return cls(**{k: v for k, v in data.items() if k in known})


class RiskManager:
    SOURCE = "risk_manager"

    def __init__(self, config: RiskConfig, clock: Clock | None = None,
                 bus: EventBus | None = None, circuit=None) -> None:
        self.config = config
        self._clock = clock or RealClock()
        self._bus = bus
        self._circuit = circuit          # CircuitBreaker (opt-in): blokuje wejścia w anomalii reżimu
        self._killed = False
        self._kill_reason = ""
        self.realized_pnl_today = 0.0
        self.trades_today = 0
        self.exposure: dict[Asset, float] = {}
        self._last_tick: dict[Asset, MarketTick] = {}

    # -- stan ---------------------------------------------------------------- #
    @property
    def open_positions(self) -> int:
        return sum(1 for v in self.exposure.values() if v > 1e-9)

    @property
    def total_exposure(self) -> float:
        return sum(self.exposure.values())

    @property
    def is_killed(self) -> bool:
        return self._killed

    def kill(self, reason: str = "") -> None:
        self._killed = True
        self._kill_reason = reason
        log.warning("KILL SWITCH: %s", reason)

    def reset_day(self) -> None:
        self.realized_pnl_today = 0.0
        self.trades_today = 0

    def set_realized_pnl_today(self, value: float) -> None:
        self.realized_pnl_today = value

    def register_open(self, asset: Asset, notional_usd: float) -> None:
        self.exposure[asset] = self.exposure.get(asset, 0.0) + notional_usd
        self.trades_today += 1

    def register_close(self, asset: Asset) -> None:
        self.exposure.pop(asset, None)

    # -- decyzja ------------------------------------------------------------- #
    def approve(self, intent: TradeIntent, tick: MarketTick | None = None) -> RiskDecision:
        ts = self._clock.now()

        # Zamknięcia zawsze dozwolone — redukują ryzyko.
        if intent.action == "CLOSE":
            return RiskDecision(approved=True, ts=ts, reason="close (redukcja ryzyka)")

        reasons: list[str] = []

        if self._killed:
            return RiskDecision(approved=False, ts=ts,
                                reason=f"kill switch aktywny: {self._kill_reason}")

        if self._circuit is not None and self._circuit.is_open(intent.asset):
            reasons.append("circuit breaker (anomalia reżimu)")

        if self.realized_pnl_today <= -self.config.max_daily_loss_usd:
            reasons.append(f"dzienny limit straty ({self.realized_pnl_today:.2f}$)")

        if intent.notional_usd > self.config.max_trade_notional_usd:
            reasons.append(f"nominał transakcji {intent.notional_usd:.0f}$ > "
                           f"{self.config.max_trade_notional_usd:.0f}$")

        new_asset_exp = self.exposure.get(intent.asset, 0.0) + intent.notional_usd
        if new_asset_exp > self.config.max_asset_exposure_usd:
            reasons.append(f"ekspozycja na {intent.asset.value} {new_asset_exp:.0f}$ > "
                           f"{self.config.max_asset_exposure_usd:.0f}$")

        if self.total_exposure + intent.notional_usd > self.config.max_total_exposure_usd:
            reasons.append("łączna ekspozycja przekroczona")

        is_new_asset = self.exposure.get(intent.asset, 0.0) <= 1e-9
        if is_new_asset and self.open_positions >= self.config.max_open_positions:
            reasons.append(f"limit otwartych pozycji ({self.config.max_open_positions})")

        if self.trades_today >= self.config.max_trades_per_day:
            reasons.append(f"limit transakcji/dzień ({self.config.max_trades_per_day})")

        if tick is not None:
            if max(tick.spot_spread_bps, tick.perp_spread_bps) > self.config.max_spread_bps:
                reasons.append("spread za szeroki")
            if min(tick.spot_depth_usd, tick.perp_depth_usd) < self.config.min_depth_usd:
                reasons.append("za mała płynność")

        if reasons:
            return RiskDecision(approved=False, ts=ts, reason="; ".join(reasons))
        return RiskDecision(approved=True, ts=ts, reason="OK")

    # -- wiring na szynę ----------------------------------------------------- #
    def attach(self, bus: EventBus) -> None:
        self._bus = bus
        bus.subscribe(EventType.MARKET_TICK, self._on_tick)
        bus.subscribe(EventType.TRADE_INTENT, self._on_intent)
        bus.subscribe(EventType.POSITION_OPENED, self._on_position_opened)
        bus.subscribe(EventType.POSITION_CLOSED, self._on_position_closed)
        bus.subscribe(EventType.PNL_UPDATE, self._on_pnl)
        bus.subscribe(EventType.EMERGENCY_STOP, self._on_emergency)
        bus.subscribe(EventType.KILL_SWITCH, self._on_emergency)

    async def _on_tick(self, event: Event) -> None:
        tick = event.payload
        if isinstance(tick, MarketTick):
            self._last_tick[tick.asset] = tick

    async def _on_position_opened(self, event: Event) -> None:
        p = event.payload
        if isinstance(p, Position):
            # realna ekspozycja = nominał nogi spot; aktualizuje też trades_today
            self.register_open(p.asset, abs(p.spot_qty * p.spot_entry))

    async def _on_position_closed(self, event: Event) -> None:
        p = event.payload
        if isinstance(p, Position):
            self.register_close(p.asset)

    async def _on_pnl(self, event: Event) -> None:
        snap = event.payload
        if isinstance(snap, PnLSnapshot):
            self.realized_pnl_today = snap.realized

    async def _on_intent(self, event: Event) -> None:
        intent = event.payload
        if not isinstance(intent, TradeIntent) or self._bus is None:
            return
        decision = self.approve(intent, self._last_tick.get(intent.asset))
        etype = EventType.RISK_APPROVED if decision.approved else EventType.RISK_REJECTED
        sev = Severity.INFO if decision.approved else Severity.WARNING
        await self._bus.publish(Event(etype, decision.ts, self.SOURCE, sev,
                                      payload={"intent": intent, "decision": decision}))

    async def _on_emergency(self, event: Event) -> None:
        reason = ""
        if isinstance(event.payload, dict):
            reason = event.payload.get("reason", "")
        self.kill(reason or "emergency stop")
