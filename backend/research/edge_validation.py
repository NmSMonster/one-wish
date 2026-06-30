"""M3.5 — brama walidacji edge.

Puszcza strumień ticków przez prawdziwy RepricingDetector i liczy, czy okazje
(dyslokacje basis dające dodatni edge PO KOSZTACH) faktycznie występują z
sensowną częstotliwością i marginesem. To warunek przejścia do dalszej rozbudowy:

  PASS      — edge istnieje z zapasem → budujemy dalej z tą taktyką,
  MARGINAL  — edge bywa, ale cienko → ostrożnie, rozważyć overlay kierunkowy,
  FAIL      — brak edge po kosztach → zmieniamy taktykę zamiast budować dalej.

Werdykt jest opisowy i konserwatywny — ma chronić przed budowaniem na złudzeniu.
"""
from __future__ import annotations

import statistics
from collections import Counter
from dataclasses import dataclass, field

from ..core.types import MarketTick, SignalState
from ..signal.repricing import RepricingDetector

# Mapowanie fragmentów powodu NO_TRADE na czytelne kategorie.
_REASON_KEYS = {
    "funding": "funding<=0",
    "dislocation": "za mała dyslokacja",
    "net_edge": "edge<min po kosztach",
    "spread": "spread za szeroki",
    "płynność": "za mała płynność",
    "nieświeże": "dane nieświeże",
    "rozliczenia": "za blisko rozliczenia",
}


def _percentile(values: list[float], p: float) -> float:
    if not values:
        return 0.0
    s = sorted(values)
    k = (len(s) - 1) * p
    lo = int(k)
    hi = min(lo + 1, len(s) - 1)
    return s[lo] + (s[hi] - s[lo]) * (k - lo)


@dataclass
class EdgeReport:
    label: str
    total_ticks: int
    edges: int
    no_trade: int
    edge_rate: float
    mean_net_edge_bps: float
    mean_net_edge_when_edge: float
    median_dislocation_bps: float
    p95_dislocation_bps: float
    positive_net_fraction: float
    reason_counts: dict = field(default_factory=dict)
    verdict: str = "FAIL"
    notes: str = ""

    def summary(self) -> str:
        lines = [
            f"=== EDGE VALIDATION ({self.label}) ===",
            f"ticki:                 {self.total_ticks}",
            f"EDGE_DETECTED:         {self.edges}  ({self.edge_rate*100:.2f}%)",
            f"NO_TRADE:              {self.no_trade}",
            f"net_edge śr. (wszystko): {self.mean_net_edge_bps:+.2f} bps",
            f"net_edge śr. (gdy edge): {self.mean_net_edge_when_edge:+.2f} bps",
            f"dyslokacja mediana/p95:  {self.median_dislocation_bps:+.2f} / {self.p95_dislocation_bps:+.2f} bps",
            f"% ticków net_edge>0:     {self.positive_net_fraction*100:.2f}%",
            "powody NO_TRADE:",
        ]
        for reason, n in sorted(self.reason_counts.items(), key=lambda kv: -kv[1]):
            lines.append(f"    - {reason}: {n}")
        lines.append(f"WERDYKT: {self.verdict}")
        if self.notes:
            lines.append(f"uwaga: {self.notes}")
        return "\n".join(lines)


class EdgeValidator:
    def __init__(
        self,
        detector: RepricingDetector,
        *,
        min_edges: int = 10,
        min_edge_rate: float = 0.02,
        min_mean_net_margin_bps: float = 3.0,
    ) -> None:
        self.detector = detector
        self.min_edges = min_edges
        self.min_edge_rate = min_edge_rate
        self.min_mean_net_margin_bps = min_mean_net_margin_bps

    def analyze(self, ticks: list[MarketTick], label: str = "") -> EdgeReport:
        net_all: list[float] = []
        net_edge_only: list[float] = []
        disloc: list[float] = []
        reasons: Counter = Counter()
        edges = 0

        for tick in ticks:
            sig = self.detector.evaluate(tick)
            net_all.append(sig.expected_net_edge_bps)
            disloc.append(sig.dislocation_bps)
            if sig.state == SignalState.EDGE_DETECTED:
                edges += 1
                net_edge_only.append(sig.expected_net_edge_bps)
            else:
                for key, label_pl in _REASON_KEYS.items():
                    if key in sig.reason:
                        reasons[label_pl] += 1

        total = len(ticks)
        edge_rate = edges / total if total else 0.0
        mean_net = statistics.fmean(net_all) if net_all else 0.0
        mean_net_edge = statistics.fmean(net_edge_only) if net_edge_only else 0.0
        positive_net = sum(1 for v in net_all if v > 0) / total if total else 0.0

        verdict, notes = self._verdict(edges, edge_rate, mean_net_edge)

        return EdgeReport(
            label=label,
            total_ticks=total,
            edges=edges,
            no_trade=total - edges,
            edge_rate=edge_rate,
            mean_net_edge_bps=mean_net,
            mean_net_edge_when_edge=mean_net_edge,
            median_dislocation_bps=statistics.median(disloc) if disloc else 0.0,
            p95_dislocation_bps=_percentile(disloc, 0.95),
            positive_net_fraction=positive_net,
            reason_counts=dict(reasons),
            verdict=verdict,
            notes=notes,
        )

    def _verdict(self, edges: int, edge_rate: float, mean_net_edge: float) -> tuple[str, str]:
        if edges >= self.min_edges and edge_rate >= self.min_edge_rate \
                and mean_net_edge >= self.min_mean_net_margin_bps:
            return "PASS", "edge występuje z zapasem po kosztach"
        if edges > 0:
            return "MARGINAL", ("edge bywa, ale rzadko lub cienko — ostrożnie; "
                                "rozważyć overlay kierunkowy zanim wejdą pieniądze")
        return "FAIL", ("brak edge po kosztach na tych danych — nie budować live na tej "
                        "taktyce bez zmiany (np. overlay kierunkowy / inne fee tier)")
