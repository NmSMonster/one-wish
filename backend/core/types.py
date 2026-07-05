"""Typy domenowe One Wish — enumeracje i niemutowalne dataklasy stanu.

Konwencje:
- Ceny i kwoty w USDT (≈ USD). Na etapie paper/backtest używamy float; w M12
  (realny kapitał) rozważymy Decimal dla księgowania.
- "bps" = punkty bazowe (1 bps = 0.01%). Basis i edge wyrażamy w bps.
- Wszystkie znaczniki czasu to epoch seconds (float, UTC).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


# --------------------------------------------------------------------------- #
# Enumeracje
# --------------------------------------------------------------------------- #
class Asset(str, Enum):
    BTC = "BTC"
    ETH = "ETH"
    SOL = "SOL"
    XRP = "XRP"
    # Rozszerzone uniwersum (UNIVERSE_SCAN.md: wysoki, trwały funding + płynność ≥ $50M).
    # Wyższy funding alta zwykle = wyższe ryzyko — chronią płynność, margin watchdog i
    # konserwatywne brackety maintenance (backend/risk/margin.py).
    DOGE = "DOGE"
    ZEC = "ZEC"
    VELVET = "VELVET"
    TAC = "TAC"
    HYPE = "HYPE"


class Venue(str, Enum):
    """Giełda/venue perpa. Multi-venue carry: shortujemy perp tam, gdzie funding
    (annualizowany) jest najwyższy; spot trzymamy na najpłynniejszym venue."""
    BINANCE = "BINANCE"
    BYBIT = "BYBIT"
    OKX = "OKX"
    HYPERLIQUID = "HYPERLIQUID"


#: Stała lista obsługiwanych aktywów (kolejność deterministyczna).
ASSETS: tuple["Asset", ...] = (
    Asset.BTC, Asset.ETH, Asset.SOL, Asset.XRP,
    Asset.DOGE, Asset.ZEC, Asset.VELVET, Asset.TAC, Asset.HYPE,
)

#: Mapowanie na symbole Binance (spot i USDT-M perp mają ten sam symbol).
BINANCE_SYMBOL: dict["Asset", str] = {
    Asset.BTC: "BTCUSDT",
    Asset.ETH: "ETHUSDT",
    Asset.SOL: "SOLUSDT",
    Asset.XRP: "XRPUSDT",
    Asset.DOGE: "DOGEUSDT",
    Asset.ZEC: "ZECUSDT",
    Asset.VELVET: "VELVETUSDT",
    Asset.TAC: "TACUSDT",
    Asset.HYPE: "HYPEUSDT",
}


class Leg(str, Enum):
    """Noga pozycji delta-neutral."""
    SPOT = "SPOT"
    PERP = "PERP"


class Side(str, Enum):
    BUY = "BUY"
    SELL = "SELL"


class OrderType(str, Enum):
    LIMIT = "LIMIT"
    MARKET = "MARKET"


class OrderStatus(str, Enum):
    NEW = "NEW"
    PARTIALLY_FILLED = "PARTIALLY_FILLED"
    FILLED = "FILLED"
    REJECTED = "REJECTED"
    CANCELED = "CANCELED"


class SignalState(str, Enum):
    EDGE_DETECTED = "EDGE_DETECTED"
    EDGE_LOST = "EDGE_LOST"
    NO_TRADE = "NO_TRADE"


class BotState(str, Enum):
    RUNNING = "RUNNING"
    PAUSED = "PAUSED"
    KILLED = "KILLED"


class ConnectionState(str, Enum):
    LIVE = "LIVE"
    RECONNECTING = "RECONNECTING"
    DISCONNECTED = "DISCONNECTED"
    SIMULATION = "SIMULATION"


# --------------------------------------------------------------------------- #
# Dane rynkowe
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class MarketTick:
    """Migawka rynku dla jednego aktywa: spot + perp + funding + top-of-book.

    `index` to cena indeksu Binance (podstawa funding/marku perpa). Basis liczymy
    względem indeksu, bo to on definiuje "uczciwą" relację perp↔spot.
    """
    asset: Asset
    ts: float
    spot: float
    perp: float
    index: float
    funding_rate: float          # bieżąca stawka funding (ułamek na cykl 8h)
    predicted_funding: float     # przewidywany funding do najbliższego rozliczenia
    next_funding_ts: float       # epoch najbliższego rozliczenia funding
    spot_bid: float
    spot_ask: float
    perp_bid: float
    perp_ask: float
    spot_depth_usd: float        # dostępny nominał przy topie (do estymacji poślizgu)
    perp_depth_usd: float
    data_lag_ms: float = 0.0
    # wzbogacony kontekst rynkowy (domyślnie 0 → syntetyk/replay bez nich działa)
    mark_price: float = 0.0          # cena mark (baza funding/likwidacji)
    open_interest_usd: float = 0.0   # łączny otwarty interes na perpie (nominał USD)
    interest_rate: float = 0.0       # składnik bazowy funding (z premiumIndex)

    @property
    def basis_bps(self) -> float:
        """Basis = (perp − index) / index, w bps."""
        if self.index <= 0:
            return 0.0
        return (self.perp - self.index) / self.index * 1e4

    @property
    def premium_bps(self) -> float:
        """Premia mark względem indeksu (w bps) — sterownik forward funding."""
        if self.index <= 0 or self.mark_price <= 0:
            return 0.0
        return (self.mark_price - self.index) / self.index * 1e4

    @property
    def spot_spread_bps(self) -> float:
        if self.spot <= 0:
            return 0.0
        return (self.spot_ask - self.spot_bid) / self.spot * 1e4

    @property
    def perp_spread_bps(self) -> float:
        if self.perp <= 0:
            return 0.0
        return (self.perp_ask - self.perp_bid) / self.perp * 1e4

    @property
    def seconds_to_funding(self) -> float:
        return max(0.0, self.next_funding_ts - self.ts)


# --------------------------------------------------------------------------- #
# Model wartości uczciwej i sygnał
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class FairValue:
    """Wyjście Fair Value Model (M4)."""
    asset: Asset
    ts: float
    fair_basis_bps: float        # uczciwy basis = oczekiwany carry funding do settle
    expected_funding_bps: float  # składnik: funding do najbliższego rozliczenia
    carry_cost_bps: float        # składnik: koszt nośności
    confidence: float            # 0..1
    reason: str = ""


@dataclass(frozen=True)
class CostEstimate:
    """Estymata kosztów round-trip (wejście+wyjście, obie nogi) w bps."""
    fees_bps: float
    slippage_bps: float
    spread_bps: float

    @property
    def total_bps(self) -> float:
        return self.fees_bps + self.slippage_bps + self.spread_bps


@dataclass(frozen=True)
class Signal:
    """Wyjście Repricing Detectora (M5)."""
    asset: Asset
    ts: float
    state: SignalState
    observed_basis_bps: float
    fair_basis_bps: float
    dislocation_bps: float        # observed − fair
    expected_net_edge_bps: float  # po odjęciu kosztów + dodaniu carry
    cost_bps: float
    reason: str = ""


# --------------------------------------------------------------------------- #
# Decyzje i egzekucja
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class TradeIntent:
    """Zamiar transakcyjny ze Strategy Policy."""
    asset: Asset
    ts: float
    action: str                  # "OPEN" | "CLOSE"
    notional_usd: float
    expected_net_edge_bps: float
    reason: str = ""


@dataclass(frozen=True)
class RiskDecision:
    approved: bool
    ts: float
    reason: str = ""


@dataclass(frozen=True)
class OrderRequest:
    client_order_id: str
    asset: Asset
    leg: Leg
    side: Side
    order_type: OrderType
    price: float
    qty: float                   # ilość w jednostkach bazowych (np. BTC)
    ts: float
    intent_action: str = "OPEN"  # do parowania nóg i reconcyliacji


@dataclass(frozen=True)
class OrderUpdate:
    client_order_id: str
    asset: Asset
    leg: Leg
    side: Side
    status: OrderStatus
    filled_qty: float
    avg_price: float
    ts: float
    reason: str = ""


@dataclass(frozen=True)
class Fill:
    client_order_id: str
    asset: Asset
    leg: Leg
    side: Side
    price: float
    qty: float
    fee: float
    ts: float


# --------------------------------------------------------------------------- #
# Pozycje i PnL
# --------------------------------------------------------------------------- #
@dataclass
class Position:
    """Para delta-neutral: long spot + short perp na jednym aktywie."""
    id: str
    asset: Asset
    spot_qty: float = 0.0
    spot_entry: float = 0.0
    perp_qty: float = 0.0        # ujemne = short perp
    perp_entry: float = 0.0
    funding_accrued: float = 0.0
    realized: float = 0.0        # zrealizowany PnL tej pozycji (do metryk per-trade)
    fees: float = 0.0            # prowizje tej pozycji
    opened_ts: float = 0.0
    closed_ts: float | None = None

    @property
    def net_delta(self) -> float:
        """Ekspozycja kierunkowa w jednostkach bazowych (cel ≈ 0)."""
        return self.spot_qty + self.perp_qty

    @property
    def is_open(self) -> bool:
        return self.closed_ts is None and (abs(self.spot_qty) > 1e-12 or abs(self.perp_qty) > 1e-12)

    def price_pnl(self, spot_mark: float, perp_mark: float) -> float:
        """Czysty mark-to-market obu nóg (BEZ funding). Dla pary delta-neutral bliski
        zeru — kierunkowy ruch znosi się między long spot a short perp."""
        spot_pnl = self.spot_qty * (spot_mark - self.spot_entry)
        perp_pnl = self.perp_qty * (perp_mark - self.perp_entry)
        return spot_pnl + perp_pnl

    def unrealized_pnl(self, spot_mark: float, perp_mark: float) -> float:
        """Niezrealizowany wynik pozycji: mark-to-market + zainkasowany funding.
        UWAGA: to jest wielkość PER POZYCJA (karta GUI). NIE sumuj jej z osobnym
        `funding_collected` książki — funding byłby policzony dwukrotnie. Do agregatu
        PnL książki używaj `price_pnl` + `funding_collected` (patrz PositionBook.snapshot)."""
        return self.price_pnl(spot_mark, perp_mark) + self.funding_accrued


@dataclass(frozen=True)
class PnLSnapshot:
    ts: float
    realized: float
    unrealized: float
    funding_collected: float
    fees_paid: float

    @property
    def net(self) -> float:
        return self.realized + self.unrealized + self.funding_collected - self.fees_paid
