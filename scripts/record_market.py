"""Nagrywa realny strumień Binance (read-only) do pliku JSONL do późniejszego replayu.

Uruchom na dłużej (godziny/dni), żeby złapać cykle funding:
    python scripts/record_market.py --out data/binance_ticks.jsonl --cycles 0 --interval 2
(--cycles 0 = bez limitu, zatrzymaj Ctrl+C)
"""
from __future__ import annotations

import argparse
import asyncio
import os
import sys

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:  # noqa: BLE001
    pass

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import json  # noqa: E402

from backend.adapters.market.base import MarketDataAdapter  # noqa: E402
from backend.adapters.market.binance_public import (  # noqa: E402
    BinancePublicSource,
    fetch_funding_history,
)
from backend.core.bus import EventBus  # noqa: E402
from backend.core.clock import RealClock  # noqa: E402
from backend.core.types import ASSETS, BINANCE_SYMBOL  # noqa: E402
from backend.research.recorder import TickRecorder  # noqa: E402


def _save_funding_history(out_path: str) -> None:
    """Sidecar z historią funding (reżim sprzed nagrania) — kontekst do analizy."""
    meta = {}
    for asset in ASSETS:
        symbol = BINANCE_SYMBOL[asset]
        try:
            meta[asset.value] = fetch_funding_history(symbol, limit=100)
        except Exception as exc:  # noqa: BLE001
            meta[asset.value] = {"error": str(exc)}
    path = out_path + ".funding_history.json"
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(meta, fh)
    print(f"Historia funding -> {path}")


def main() -> None:
    ap = argparse.ArgumentParser(description="One Wish — nagrywanie danych Binance")
    ap.add_argument("--out", default="data/binance_ticks.jsonl")
    ap.add_argument("--cycles", type=int, default=30, help="liczba cykli pobrań (0 = bez limitu)")
    ap.add_argument("--interval", type=float, default=2.0)
    args = ap.parse_args()

    os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)
    max_cycles = None if args.cycles == 0 else args.cycles

    import datetime

    def heartbeat(n: int) -> None:
        now = datetime.datetime.now().strftime("%H:%M:%S")
        print(f"[{now}] zapisano {n} tickow...", flush=True)

    src = BinancePublicSource(poll_interval=args.interval, max_cycles=max_cycles,
                              fetch_depth=True, fetch_extras=True)
    bus = EventBus()
    recorder = TickRecorder(args.out, heartbeat_every=40, on_heartbeat=heartbeat)
    recorder.attach(bus)
    adapter = MarketDataAdapter(src, bus, RealClock())

    _save_funding_history(args.out)
    print(f"Nagrywam realne dane Binance (forward funding + mark + OI) -> {args.out} "
          f"(cykle={args.cycles}, interwal={args.interval}s)")
    try:
        asyncio.run(adapter.run())
    except KeyboardInterrupt:
        print("\nPrzerwano.")
    finally:
        recorder.close()
        print(f"Zapisano {recorder.count} tickow do {args.out}")


if __name__ == "__main__":
    main()
