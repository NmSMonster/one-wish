"""One Wish — backend bota tradingowego (delta-neutral basis/funding na Binance).

Pakiet jest event-driven: moduły komunikują się przez `core.bus.EventBus`.
Domyślny tryb to PAPER — realny handel jest za podwójną blokadą (M12).
"""

__version__ = "0.1.0"
