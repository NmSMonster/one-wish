"""Backtest na danych syntetycznych (deterministyczny).

Użycie:
    python scripts/run_backtest.py
"""
from __future__ import annotations

import os
import sys

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:  # noqa: BLE001
    pass

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backend.adapters.market import SyntheticSource  # noqa: E402
from backend.backtest import Backtester  # noqa: E402
from backend.risk import RiskConfig  # noqa: E402


def main() -> None:
    import argparse
    ap = argparse.ArgumentParser(description="One Wish backtest")
    ap.add_argument("--data", default=None, help="backtest carry na nagranym pliku JSONL")
    args = ap.parse_args()

    risk = RiskConfig(max_trade_notional_usd=1000.0, max_asset_exposure_usd=1_000_000.0,
                      max_total_exposure_usd=1e12, max_open_positions=10,
                      max_trades_per_day=10**9, max_spread_bps=100.0, min_depth_usd=0.0)

    if args.data:
        from backend.adapters.market import ReplaySource
        from backend.research.recorder import load_ticks
        ticks = load_ticks(args.data)
        if not ticks:
            print(f"brak danych w {args.data}")
            return
        result = Backtester(risk_config=risk, notional_usd=200.0,
                            policy_mode="carry").run(ReplaySource(ticks))
        print(f"WERDYKT CARRY na realnych danych: {args.data} ({len(ticks)} tickow)\n")
        print(result.summary())
        print("\nMiarodajny tylko, jesli plik obejmuje wiele godzin (kilka rozliczen funding).")
        print("Jesli netto/funding dodatnie z zapasem przez rozne rezimy -> edge jest realny.")
        return

    print("Porównanie strategii (synthetic, funding co 120s, ~16 rozliczeń):\n")
    print(f"{'tryb':7} {'wejscia':>7} {'wyjscia':>7} {'funding':>9} {'prowizje':>9} {'netto':>9}")
    for mode in ("scalp", "carry"):
        src = SyntheticSource(steps=2000, seed=3, funding_period_s=120.0)
        r = Backtester(risk_config=risk, notional_usd=200.0, policy_mode=mode).run(src)
        print(f"{mode:7} {r.entries:7d} {r.exits:7d} {r.final_funding:9.2f} "
              f"{r.final_fees:9.2f} {r.final_net:9.2f}")

    print("\nWniosek: carry trzyma parę delta-neutral i inkasuje funding zamiast palić")
    print("prowizje na churnie. To pokazuje, ze MECHANIZM jest poprawny.")
    print("UWAGA: dane SYNTETYCZNE (staly dodatni funding). To NIE jest teza o realnym")
    print("zysku — realny werdykt daje brama edge na danych Binance (EDGE_VALIDATION.md).")


if __name__ == "__main__":
    main()
