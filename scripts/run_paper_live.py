"""Runner paper-live One Wish.

Tryb synthetic (offline, deterministyczny) lub live (realne dane Binance read-only,
egzekucja nadal PAPER). Realny handel jest osobny i domyślnie zablokowany.

Użycie:
    python scripts/run_paper_live.py --mode synthetic --watch
    python scripts/run_paper_live.py --mode live --cycles 60
"""
from __future__ import annotations

import argparse
import asyncio
import os
import sys

try:  # Windows: konsola cp1250 nie koduje części znaków UTF-8 (np. strzałek)
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:  # noqa: BLE001
    pass

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backend.app.runner import OneWishApp  # noqa: E402
from backend.risk import RiskConfig  # noqa: E402

_RISK_YAML = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "risk_config.yaml")


def main() -> None:
    ap = argparse.ArgumentParser(description="One Wish paper-live runner")
    ap.add_argument("--mode", choices=["synthetic", "live"], default="synthetic")
    ap.add_argument("--steps", type=int, default=600)
    ap.add_argument("--cycles", type=int, default=None, help="liczba cykli pobrań w trybie live")
    ap.add_argument("--no-gui", action="store_true")
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--watch", action="store_true", help="paceuj synthetic w czasie ~rzeczywistym (do GUI)")
    args = ap.parse_args()

    risk = RiskConfig.from_yaml(_RISK_YAML) if os.path.exists(_RISK_YAML) else RiskConfig()

    app = OneWishApp(
        mode=args.mode,
        steps=args.steps,
        pace=args.watch,
        gui=not args.no_gui,
        gui_port=args.port,
        live_cycles=args.cycles,
        risk_config=risk,
    )
    if not args.no_gui:
        print(f"GUI: otworz index.html (config adapter=\"ws\") -> ws://127.0.0.1:{args.port}/gui")
    report = asyncio.run(app.run())
    print()
    print(report.summary())


if __name__ == "__main__":
    main()
