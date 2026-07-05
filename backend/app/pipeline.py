"""Pipeline — montaż wszystkich modułów na wspólnej szynie eventów.

Kolejność podpięcia jest istotna: moduły cache'ujące tick (Risk, Execution) muszą
subskrybować MARKET_TICK PRZED detektorem, który podczas obsługi ticka emituje
sygnał uruchamiający dalszy łańcuch. Używane przez backtest (M10) i runner
paper-live (M11), żeby nie duplikować okablowania.
"""
from __future__ import annotations

from ..adapters.exchange.paper import PaperBrokerAdapter
from ..core.bus import EventBus
from ..core.clock import Clock, RealClock
from ..execution import ExecutionEngine, FundingAccrual, PositionBook
from ..model.costs import CostModel
from ..model.fair_value import FairValueModel
from ..risk import CircuitBreaker, MarginModel, MarginWatchdog, RiskConfig, RiskManager
from ..risk.margin import DEFAULT_MAINTENANCE_BY_ASSET
from ..signal import RepricingDetector
from ..strategy import FundingWeightedSizer, StrategyPolicy


class Pipeline:
    def __init__(
        self,
        bus: EventBus,
        *,
        risk_config: RiskConfig | None = None,
        notional_usd: float = 200.0,
        broker=None,
        clock: Clock | None = None,
        fair_model: FairValueModel | None = None,
        cost_model: CostModel | None = None,
        detector_kwargs: dict | None = None,
        policy_mode: str = "carry",
        funding_weighted: bool = False,
        sizer_kwargs: dict | None = None,
    ) -> None:
        self.bus = bus
        self.clock = clock or RealClock()
        self.book = PositionBook()
        self.broker = broker or PaperBrokerAdapter(clock=self.clock)
        self.fair = fair_model or FairValueModel()
        self.cost = cost_model or CostModel()
        risk_config = risk_config or RiskConfig()
        sizer = None
        if funding_weighted and policy_mode == "carry":
            # Tier A: waż nominał siłą forward funding zamiast płaskiej kwoty na parę.
            # Cap górny = limit RiskManagera, żeby sizer nie proponował nominałów,
            # które i tak zostaną odrzucone. Ten SAM obiekt idzie do detektora (depth-
            # check/koszt) i do polityki (realny nominał zlecenia) — muszą być spójne.
            sk = {"base_notional_usd": notional_usd, "max_notional_usd": risk_config.max_trade_notional_usd}
            sk.update(sizer_kwargs or {})
            sizer = FundingWeightedSizer(**sk)
        self.sizer = sizer
        det_kwargs = {"notional_usd": notional_usd, "sizer": sizer}
        det_kwargs.update(detector_kwargs or {})
        self.detector = RepricingDetector(self.fair, self.cost, **det_kwargs)
        self.policy = StrategyPolicy(notional_usd=notional_usd, mode=policy_mode, sizer=sizer)
        self.circuit = CircuitBreaker()
        self.risk = RiskManager(risk_config, clock=self.clock, circuit=self.circuit)
        self.execution = ExecutionEngine(self.broker, self.book, clock=self.clock)
        self.funding = FundingAccrual(self.book)
        rc = self.risk.config
        self.margin = MarginWatchdog(
            self.book, MarginModel(rc.maintenance_margin_rate),
            perp_leverage=rc.perp_leverage, warn_health=rc.margin_warn_health,
            flatten_health=rc.margin_flatten_health,
            maintenance_by_asset=DEFAULT_MAINTENANCE_BY_ASSET)

        # cache ticka (circuit, risk, execution) PRZED detektorem emitującym sygnał
        self.circuit.attach(bus)
        self.risk.attach(bus)
        self.execution.attach(bus)
        self.funding.attach(bus)
        self.margin.attach(bus)
        self.detector.attach(bus)
        self.policy.attach(bus)
