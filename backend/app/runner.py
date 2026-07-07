"""OneWishApp (M11) — runner paper-live spinający cały system.

Składa: źródło danych (synthetic albo live Binance read-only) → Pipeline (paper
execution) → Monitoring → GUI API (WebSocket) → Storage (audit) → raport dzienny.

Tryby:
- "synthetic": deterministyczny strumień offline (demo/GUI/test), bez sieci,
- "live":      realne dane Binance read-only, ale egzekucja NADAL PAPER.

Realny handel (M12) jest osobny i domyślnie zablokowany — ten runner nigdy nie
wysyła prawdziwych zleceń.
"""
from __future__ import annotations

import logging

from ..adapters.exchange.paper import PaperBrokerAdapter
from ..adapters.market.base import MarketDataAdapter
from ..adapters.market.binance_public import BinancePublicSource
from ..adapters.market.synthetic import SyntheticSource
from ..api.gui_ws import GuiApiServer
from ..core.bus import EventBus
from ..core.clock import RealClock, SimClock
from ..monitoring import AlertManager, CostTelemetry, Monitor, sinks_from_env
from ..risk import RiskConfig
from ..storage import Database
from .budget import BudgetTracker, budget_risk_config, pln_to_usd
from .pipeline import Pipeline
from .report import DailyReport, build_report

log = logging.getLogger("onewish.app")


class OneWishApp:
    def __init__(
        self,
        *,
        mode: str = "synthetic",
        steps: int = 500,
        seed: int = 3,
        pace: bool = False,
        gui: bool = True,
        gui_port: int = 8765,
        db_path: str = ":memory:",
        risk_config: RiskConfig | None = None,
        notional_usd: float = 200.0,
        live_cycles: int | None = None,
        poll_interval: float = 2.0,
        alerts: bool = True,
        budget_pln: float | None = None,
        funding_weighted: bool = False,
        live_transport: str = "rest",
        flatten_on_exit: bool = False,
    ) -> None:
        self.mode = mode
        self.steps = steps
        self.seed = seed
        self.pace = pace
        self.gui = gui
        self.gui_port = gui_port
        self.db_path = db_path
        self.risk_config = risk_config or RiskConfig()
        self.notional_usd = notional_usd
        self.live_cycles = live_cycles
        self.poll_interval = poll_interval
        self.alerts = alerts
        # transport danych live: "rest" (polling, domyślnie — zero regresji) albo
        # "ws" (streamy WebSocket: niższa latencja, świeższy forward funding)
        self.live_transport = live_transport
        # domknij wszystkie pary przy końcu sesji (świadome zejście do flat —
        # nic nie zostaje bez nadzoru; domyślnie OFF: carry trzyma, recovery pilnuje)
        self.flatten_on_exit = flatten_on_exit
        # Tier A: waż nominał siłą forward funding (patrz backend/strategy/sizing.py)
        # zamiast płaskiej kwoty na każdą parę — kapitał przesuwa się w stronę wyżej
        # płacących aktywów. Opt-in, domyślnie wyłączone (zero zmiany zachowania).
        self.funding_weighted = funding_weighted
        self.telemetry: CostTelemetry | None = None
        # Fikcyjny budżet (forward paper-trade): ogranicza ekspozycję do budżetu i
        # śledzi jego wykorzystanie. Tylko paper — nie dotyka realnych pieniędzy.
        self.budget_tracker: BudgetTracker | None = None
        self.budget_report: dict | None = None    # snapshot wykorzystania budżetu po sesji
        if budget_pln is not None:
            self.budget_usd = pln_to_usd(budget_pln)
            self.risk_config = budget_risk_config(
                self.budget_usd, perp_leverage=self.risk_config.perp_leverage,
                base=self.risk_config)
            # Sizing MUSI zmieścić się w budżecie — inaczej ryzyko odrzuci każde wejście
            # (nominał > cap) i bot nic nie zrobi. Tnij rozmiar pozycji do capu ekspozycji.
            self.notional_usd = min(self.notional_usd, self.risk_config.max_trade_notional_usd)
            self.budget_tracker = BudgetTracker(self.budget_usd, self.risk_config.perp_leverage)
            log.info("Budżet fikcyjny: %.0f zł ≈ %.2f$ (cap ekspozycji %.2f$, nominał/para %.2f$)",
                     budget_pln, self.budget_usd, self.risk_config.max_total_exposure_usd,
                     self.notional_usd)
        self.report: DailyReport | None = None

    def _build_source(self):
        if self.mode == "live":
            if self.live_transport == "ws":
                from ..adapters.market.binance_ws import BinanceWsSource
                from ..core.types import ASSETS
                max_ticks = (self.live_cycles * len(ASSETS)
                             if self.live_cycles is not None else None)
                return BinanceWsSource(max_ticks=max_ticks)
            return BinancePublicSource(poll_interval=self.poll_interval, max_cycles=self.live_cycles)
        return SyntheticSource(steps=self.steps, seed=self.seed, pace=self.pace)

    async def run(self) -> DailyReport:
        bus = EventBus()
        clock = RealClock() if self.mode == "live" else SimClock()

        db = Database(self.db_path)
        db.attach(bus)

        broker = PaperBrokerAdapter(slippage_bps=1.0, seed=1, clock=clock)
        pipe = Pipeline(bus, risk_config=self.risk_config, notional_usd=self.notional_usd,
                        broker=broker, clock=clock, funding_weighted=self.funding_weighted)

        monitor = Monitor(bus, max_daily_loss_usd=self.risk_config.max_daily_loss_usd, clock=clock)
        monitor.attach(bus)
        if self.mode == "live":
            # heartbeat łapie feed, który MILKNIE: adapterowy STALE_FEED powstaje
            # dopiero przy następnym ticku — przy pełnej ciszy nigdy by nie przyszedł
            # i bot wisiałby z otwartymi pozycjami na martwych danych.
            monitor.start_heartbeat()

        if self.alerts:
            # alerty operatora poza GUI (margin/kill/emergency/stale...). Sinki z env:
            # bez konfiguracji = tylko log; w live trafią na Telegram/Discord.
            AlertManager(sinks_from_env()).attach(bus)

        # shadow telemetry: realne koszty egzekucji/rynku vs model (obserwacja, bez wpływu)
        self.telemetry = CostTelemetry()
        self.telemetry.attach(bus)

        gui_server = None
        if self.gui:
            conn = "LIVE" if self.mode == "live" else "SIMULATION"
            gui_server = GuiApiServer(bus, risk=pipe.risk, connection=conn,
                                      clock=clock, port=self.gui_port)
            try:
                await gui_server.start()
                log.info("GUI: ws://127.0.0.1:%d/gui", self.gui_port)
            except OSError as exc:
                log.warning("GUI API nie wystartował (%s) — kontynuuję bez GUI", exc)
                gui_server = None

        if self.db_path != ":memory:":
            # crash-safe restart: odbuduj księgę z trwałego audit trailu (fille +
            # funding) i przywróć ekspozycję ryzyka — bot po padzie wie o swoich
            # pozycjach (margin watchdog / wyjścia znów je nadzorują). Blok stoi
            # PO starcie GUI, żeby POSITION_UPDATED trafił też do jego snapshotu.
            from ..core.events import Event, EventType
            from ..execution import restore_book
            restore_book(db, pipe.book)
            for asset, pos in pipe.book.positions.items():
                if pos.is_open:
                    pipe.risk.restore_exposure(asset, abs(pos.spot_qty * pos.spot_entry))
                    pipe.policy.restore_holding(asset)   # nadzór wyjść nad odzyskaną parą
                    # GUI/monitoring widzą odzyskaną pozycję (tabela pozycji + snapshot)
                    await bus.publish(Event(EventType.POSITION_UPDATED, clock.now(),
                                            "recovery", payload=pos))

        adapter = MarketDataAdapter(self._build_source(), bus, clock)
        session_rowid = db.max_event_rowid()   # granica sesji: raport liczy tylko TĘ sesję
        try:
            await adapter.run()
        finally:
            monitor.stop_heartbeat()
            if self.flatten_on_exit and any(p.is_open for p in pipe.book.positions.values()):
                from ..core.events import Event, EventType
                log.info("Flatten na koniec sesji (--flatten-on-exit): domykam pary")
                await bus.publish(Event(EventType.FLATTEN, clock.now(), "runner",
                                        payload={"reason": "koniec sesji (flatten-on-exit)"}))
            # raport z mark-to-market otwartych pozycji (ostatnie znane ceny) — bez
            # tego net udawałby, że trzymane pozycje nie mają wyniku
            self.report = build_report(db, pipe.book, marks=pipe.execution.marks,
                                       since_rowid=session_rowid)
            if self.telemetry is not None:
                snap = self.telemetry.snapshot()
                log.info("Cost telemetry (shadow): slip_spot=%.2fbps slip_perp=%.2fbps "
                         "fee_spot=%.2fbps fee_perp=%.2fbps spread_spot=%.2fbps lag=%.0fms",
                         snap["slippage_bps"]["SPOT"]["mean"], snap["slippage_bps"]["PERP"]["mean"],
                         snap["fee_bps"]["SPOT"]["mean"], snap["fee_bps"]["PERP"]["mean"],
                         snap["spread_bps"]["spot"]["mean"], snap["data_lag_ms"]["mean"])
            if self.budget_tracker is not None:
                snap = self.budget_tracker.snapshot(pipe.book)
                self.budget_report = snap
                log.info("Budżet po sesji: użyte %.2f$ / %.2f$ (%.0f%%), wolne %.2f$",
                         snap["committed_usd"], snap["budget_usd"],
                         snap["utilization_pct"], snap["free_usd"])
            if gui_server:
                await gui_server.stop()
            db.close()
        return self.report
