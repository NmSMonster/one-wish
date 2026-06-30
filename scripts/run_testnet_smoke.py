"""Smoke-test realnego transportu na TESTNECIE Binance (fałszywe pieniądze).

Składa pojedyncze, malutkie zlecenia MARKET (spot BUY + perp SELL) na testnecie,
żeby zweryfikować, że podpisany transport, parsowanie fillów, reconcile i odczyt
stanu konta działają end-to-end z realną giełdą — ZANIM cokolwiek tknie mainnet.

Bezpieczeństwo:
- działa wyłącznie na TESTNECIE (testnet=True); mainnet jest osobno zablokowany,
- wymaga kluczy TESTNET z env (ONEWISH_BINANCE_KEY/SECRET),
- wymaga jawnego --yes oraz tokenu potwierdzenia,
- twardy limit nominału na zlecenie.

Klucze testnet zakładasz na: https://testnet.binance.vision (spot) oraz
https://testnet.binancefuture.com (futures). To NIE są klucze mainnet.

Przykład:
    ONEWISH_BINANCE_KEY=... ONEWISH_BINANCE_SECRET=... \
      python scripts/run_testnet_smoke.py --qty 0.001 --max-notional 200 --yes
"""
from __future__ import annotations

import argparse
import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backend.adapters.exchange.binance_live import (  # noqa: E402
    BinanceLiveAdapter,
    LiveTradingBlocked,
)
from backend.core.types import Asset, Leg, OrderRequest, OrderType, Side  # noqa: E402

_CONFIRM_TOKEN = "I_UNDERSTAND_REAL_MONEY"


async def _run(args) -> int:
    adapter = BinanceLiveAdapter.from_env(
        live_enabled=True, transport_implemented=True, testnet=True,
        allow_mainnet=False, max_notional_usd=args.max_notional)
    try:
        adapter.arm(_CONFIRM_TOKEN)
    except LiveTradingBlocked as exc:
        print(f"NIE uzbrojono: {exc}")
        return 2

    print(f"UZBROJONY na TESTNECIE (spot={adapter.spot_base}, fut={adapter.fut_base})")
    print("Konto przed:", await adapter.account_state() or "(brak/odmowa)")

    asset = Asset[args.asset]
    spot_req = OrderRequest("ow-smoke-spot", asset, Leg.SPOT, Side.BUY,
                            OrderType.MARKET, args.price, args.qty, 0.0, "OPEN")
    perp_req = OrderRequest("ow-smoke-perp", asset, Leg.PERP, Side.SELL,
                            OrderType.MARKET, args.price, args.qty, 0.0, "OPEN")

    spot_res = await adapter.submit(spot_req)
    print("SPOT  ->", spot_res.status.value, spot_res.reason or spot_res.fills)
    perp_res = await adapter.submit(perp_req)
    print("PERP  ->", perp_res.status.value, perp_res.reason or perp_res.fills)

    print("Otwarte zlecenia (reconcile):", await adapter.reconcile())
    print("Konto po:", await adapter.account_state() or "(brak/odmowa)")
    return 0


def main() -> None:
    ap = argparse.ArgumentParser(description="One Wish — smoke-test transportu (TESTNET)")
    ap.add_argument("--asset", default="BTC", choices=["BTC", "ETH", "SOL", "XRP"])
    ap.add_argument("--qty", type=float, default=0.001, help="ilość w jednostkach bazowych")
    ap.add_argument("--price", type=float, default=0.0,
                    help="cena referencyjna do limitu nominału (0 = pomiń kontrolę po cenie)")
    ap.add_argument("--max-notional", type=float, default=200.0)
    ap.add_argument("--yes", action="store_true", help="wymagane potwierdzenie uruchomienia")
    args = ap.parse_args()

    if not args.yes:
        print("Dopisz --yes, żeby potwierdzić wysyłkę zleceń na TESTNET.")
        raise SystemExit(1)
    if not (os.environ.get("ONEWISH_BINANCE_KEY") and os.environ.get("ONEWISH_BINANCE_SECRET")):
        print("Brak kluczy TESTNET w env (ONEWISH_BINANCE_KEY/SECRET).")
        raise SystemExit(2)

    raise SystemExit(asyncio.run(_run(args)))


if __name__ == "__main__":
    main()
