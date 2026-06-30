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
from ..monitoring import Monitor
from ..risk import RiskConfig
from ..storage import Database
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
        self.report: DailyReport | None = None

    def _build_source(self):
        if self.mode == "live":
            return BinancePublicSource(poll_interval=self.poll_interval, max_cycles=self.live_cycles)
        return SyntheticSource(steps=self.steps, seed=self.seed, pace=self.pace)

    async def run(self) -> DailyReport:
        bus = EventBus()
        clock = RealClock() if self.mode == "live" else SimClock()

        db = Database(self.db_path)
        db.attach(bus)

        broker = PaperBrokerAdapter(slippage_bps=1.0, seed=1, clock=clock)
        pipe = Pipeline(bus, risk_config=self.risk_config, notional_usd=self.notional_usd,
                        broker=broker, clock=clock)

        monitor = Monitor(bus, max_daily_loss_usd=self.risk_config.max_daily_loss_usd, clock=clock)
        monitor.attach(bus)

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

        adapter = MarketDataAdapter(self._build_source(), bus, clock)
        try:
            await adapter.run()
        finally:
            self.report = build_report(db, pipe.book)
            if gui_server:
                await gui_server.stop()
            db.close()
        return self.report
