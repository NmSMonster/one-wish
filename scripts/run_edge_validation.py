"""Uruchamia bramę walidacji edge (M3.5) na danych syntetycznych i — jeśli jest
sieć — na realnym snapshotcie Binance.

Użycie:
    python scripts/run_edge_validation.py
"""
from __future__ import annotations

import asyncio
import os
import sys

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:  # noqa: BLE001
    pass

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backend.adapters.market import SyntheticSource  # noqa: E402
from backend.adapters.market.binance_public import BinancePublicSource  # noqa: E402
from backend.model.costs import CostModel  # noqa: E402
from backend.model.fair_value import FairValueModel  # noqa: E402
from backend.research import EdgeValidator  # noqa: E402
from backend.signal.repricing import RepricingDetector  # noqa: E402


async def _collect(source, max_ticks=None):
    out = []
    async for tick in source.ticks():
        out.append(tick)
        if max_ticks is not None and len(out) >= max_ticks:
            break
    return out


def build_detector() -> RepricingDetector:
    # ta brama bada stronę DYSLOKACJI; carry ma osobny werdykt (study_funding.py)
    return RepricingDetector(FairValueModel(), CostModel(), entry_mode="dislocation")


def run_synthetic() -> None:
    ticks = asyncio.run(_collect(SyntheticSource(steps=500, seed=3)))
    report = EdgeValidator(build_detector()).analyze(ticks, label="SYNTETYK")
    print(report.summary())
    print()


def run_live(cycles: int = 30, interval: float = 1.0) -> None:
    # fetch_depth=True → realna głębokość orderbooka (uczciwy slippage), nie placeholder
    src = BinancePublicSource(poll_interval=interval, max_cycles=cycles, fetch_depth=True)
    try:
        ticks = asyncio.run(_collect(src))
    except Exception as exc:  # noqa: BLE001
        print(f"=== EDGE VALIDATION (LIVE) ===\nbrak sieci / błąd: {exc}\n")
        return
    if not ticks:
        print("=== EDGE VALIDATION (LIVE) ===\nbrak danych live\n")
        return
    report = EdgeValidator(build_detector()).analyze(ticks, label="LIVE BINANCE")
    print(report.summary())
    print()


def run_from_file(path: str) -> None:
    from backend.research.recorder import load_ticks
    ticks = load_ticks(path)
    if not ticks:
        print(f"=== EDGE VALIDATION (NAGRANE) ===\nbrak danych w {path}\n")
        return
    report = EdgeValidator(build_detector()).analyze(ticks, label=f"NAGRANE {path}")
    print(report.summary())
    print()


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser(description="One Wish — brama walidacji edge")
    ap.add_argument("--data", default=None, help="walidacja na nagranym pliku JSONL")
    args = ap.parse_args()
    if args.data:
        run_from_file(args.data)
    else:
        run_synthetic()
        run_live()
