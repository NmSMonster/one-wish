# Skan uniwersum — ranking carry

> 2026-06-30, `scripts/scan_universe.py`. Carry net = smoothed (po prowizjach), na
> historii funding Binance. Filtr płynności: 24h volume ≥ $50M.

```
 # symbol          n funding/rok   %dod carry net/rok      24h vol
------------------------------------------------------------------
 1 DOGEUSDT     1200      28.43%  84.5%        27.74% $      347M
 2 ZECUSDT      1200      22.60%  88.4%        23.85% $      577M
 3 SOLUSDT      1200      14.64%  79.3%        22.24% $     2713M
 4 XRPUSDT      1200      23.19%  87.0%        21.92% $      493M
 5 ETHUSDT      1200      20.13%  90.9%        18.86% $     8774M
 6 VELVETUSDT   1200      16.63%  99.1%        16.04% $      519M
 7 TACUSDT      1200      12.83%  97.4%        13.01% $      483M
 8 BTCUSDT      1200      11.84%  80.6%        10.52% $    13736M
 9 HYPEUSDT     1200       6.55%  91.2%         4.62% $      917M
```

Selekcja: bot powinien trzymać aktywa z TOPU (wysoki, trwały funding) przy
wystarczającej płynności. Wyższy funding alta zwykle = wyższe ryzyko (depeg,
kaskady likwidacji, manipulacja) — dlatego płynność jest twardym filtrem, a margin
watchdog i niska dźwignia chronią nogę perp.
