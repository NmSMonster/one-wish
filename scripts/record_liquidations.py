"""Nagrywa strumień likwidacji Binance (WebSocket) do pliku JSONL.

Uruchom równolegle z record_market.py (osobny terminal), na ten sam okres:
    python scripts/record_liquidations.py --out data/liquidations.jsonl
(Ctrl+C kończy.)
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:  # noqa: BLE001
    pass

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backend.adapters.market.liquidations import LiquidationStream  # noqa: E402
from backend.core.serialize import to_jsonable  # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser(description="One Wish — nagrywanie likwidacji")
    ap.add_argument("--out", default="data/liquidations.jsonl")
    ap.add_argument("--max", type=int, default=0, help="limit zdarzeń (0 = bez limitu)")
    args = ap.parse_args()

    os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)
    fh = open(args.out, "a", encoding="utf-8")

    def on_liq(liq) -> None:
        fh.write(json.dumps(to_jsonable(liq)) + "\n")
        fh.flush()
        print(f"LIQ {liq.asset.value} {liq.side.value} ${liq.notional_usd:,.0f} @ {liq.price}")

    stream = LiquidationStream(on_liquidation=on_liq,
                               max_events=(None if args.max == 0 else args.max))
    print(f"Nasłuchuję likwidacji -> {args.out}")
    try:
        asyncio.run(stream.run())
    except KeyboardInterrupt:
        print("\nPrzerwano.")
    finally:
        fh.close()
        print(f"Zapisano {stream.count} likwidacji do {args.out}")


if __name__ == "__main__":
    main()
