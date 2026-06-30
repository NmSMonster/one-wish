"""Typy eventów i opakowanie Event dla szyny zdarzeń.

Katalog eventów odpowiada sekcji §4 ARCHITECTURE.md. Każdy istotny krok w
systemie jest eventem — to podstawa audytu i odtwarzalności (M8).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from uuid import uuid4


class EventType(str, Enum):
    # Rynek
    MARKET_TICK = "MARKET_TICK"
    FAIR_VALUE = "FAIR_VALUE"
    DATA_LAG_WARNING = "DATA_LAG_WARNING"
    STALE_FEED = "STALE_FEED"
    # Sygnał
    EDGE_DETECTED = "EDGE_DETECTED"
    EDGE_LOST = "EDGE_LOST"
    NO_TRADE_CONDITION = "NO_TRADE_CONDITION"
    # Decyzja
    TRADE_INTENT = "TRADE_INTENT"
    RISK_APPROVED = "RISK_APPROVED"
    RISK_REJECTED = "RISK_REJECTED"
    # Egzekucja
    ORDER_REQUEST = "ORDER_REQUEST"
    ORDER_UPDATE = "ORDER_UPDATE"
    FILL = "FILL"
    PARTIAL_FILL = "PARTIAL_FILL"
    ORDER_REJECTED = "ORDER_REJECTED"
    FLATTEN = "FLATTEN"
    # Pozycja / PnL
    POSITION_OPENED = "POSITION_OPENED"
    POSITION_UPDATED = "POSITION_UPDATED"
    POSITION_CLOSED = "POSITION_CLOSED"
    FUNDING_ACCRUED = "FUNDING_ACCRUED"
    PNL_UPDATE = "PNL_UPDATE"
    # Ryzyko / awaria
    RISK_LIMIT_BREACH = "RISK_LIMIT_BREACH"
    MARGIN_WARNING = "MARGIN_WARNING"
    EMERGENCY_STOP = "EMERGENCY_STOP"
    KILL_SWITCH = "KILL_SWITCH"
    # Operator
    OPERATOR_COMMAND = "OPERATOR_COMMAND"


class Severity(str, Enum):
    INFO = "info"
    WARNING = "warning"
    CRITICAL = "critical"


@dataclass
class Event:
    """Pojedyncze zdarzenie na szynie.

    `payload` to dowolne dane (dataklasa domenowa lub dict). Serializację do GUI
    obsługuje warstwa GUI API, nie sam event.
    """
    type: EventType
    ts: float
    source: str
    severity: Severity = Severity.INFO
    payload: object = None
    id: str = field(default_factory=lambda: uuid4().hex)
