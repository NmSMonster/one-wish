"""AlertManager — alerty operatora POZA GUI (Telegram/Discord/Slack/log).

GUI pokazuje stan, gdy ktoś patrzy. Bot carry żyje całą dobę, a najgroźniejsze
zdarzenia (margin health, orphan leg, kill/emergency stop, zamrożony feed) muszą
dotrzeć do operatora natychmiast, nawet gdy nikt nie patrzy na cockpit. Dlatego
osobny kanał: AlertManager subskrybuje krytyczne eventy szyny i rozsyła je do
pluggable sinków.

Zasady:
- pluggable sinki (Log/Buffer/Webhook) — łatwo dołożyć kanał bez ruszania logiki,
- próg severity (domyślnie WARNING) — INFO nie zalewa kanału,
- throttling per (rodzaj, aktywo) z cooldownem na czasie eventu — bez spamu, ale
  CRITICAL (emergency/kill/flatten) NIGDY nie jest tłumiony,
- sekrety (tokeny webhooków) wyłącznie z env — nigdy w repo,
- błąd sieci sinka nie może zabić bota (łapany, logowany).
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import urllib.error
import urllib.request
from abc import ABC, abstractmethod
from dataclasses import dataclass

from ..core.bus import EventBus
from ..core.events import Event, EventType, Severity

log = logging.getLogger("onewish.alerts")

_SEV_RANK = {Severity.INFO: 0, Severity.WARNING: 1, Severity.CRITICAL: 2}
_LOG_LEVEL = {Severity.INFO: logging.INFO, Severity.WARNING: logging.WARNING,
              Severity.CRITICAL: logging.CRITICAL}


@dataclass
class Alert:
    ts: float
    severity: Severity
    kind: str                  # nazwa EventType (np. "MARGIN_WARNING")
    title: str
    detail: str = ""
    asset: str | None = None

    def text(self) -> str:
        head = f"[{self.severity.value.upper()}] {self.title}"
        return f"{head} — {self.detail}" if self.detail else head


# -- sinki ------------------------------------------------------------------ #
class AlertSink(ABC):
    @abstractmethod
    async def send(self, alert: Alert) -> None:
        raise NotImplementedError


class LogSink(AlertSink):
    """Loguje alert na odpowiednim poziomie (zawsze dostępny fallback)."""

    async def send(self, alert: Alert) -> None:
        log.log(_LOG_LEVEL[alert.severity], "%s", alert.text())


class BufferSink(AlertSink):
    """Trzyma ostatnie alerty w pamięci — do GUI i testów."""

    def __init__(self, maxlen: int = 200) -> None:
        self.alerts: list[Alert] = []
        self.maxlen = maxlen

    async def send(self, alert: Alert) -> None:
        self.alerts.append(alert)
        if len(self.alerts) > self.maxlen:
            self.alerts = self.alerts[-self.maxlen:]


class WebhookSink(AlertSink):
    """POST JSON na webhook (Discord/Slack/Telegram). HTTP w executorze, żeby nie
    blokować pętli; błąd sieci jest łapany i logowany (nie wywala bota). Gdy URL
    pusty — sink jest no-op (bezpieczny default bez konfiguracji)."""

    def __init__(self, url: str | None, *, timeout: float = 5.0, fmt=None) -> None:
        self.url = url
        self.timeout = timeout
        # domyślny format Discord/Slack ("content"); Telegram nadpisuje fmt
        self.fmt = fmt or (lambda a: {"content": a.text()})

    async def send(self, alert: Alert) -> None:
        if not self.url:
            return
        url: str = self.url                   # po guardzie: pewny str (dla domknięcia _post)
        body = json.dumps(self.fmt(alert)).encode("utf-8")

        def _post() -> None:
            req = urllib.request.Request(
                url, data=body,
                headers={"Content-Type": "application/json", "User-Agent": "one-wish/0.1"})
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                resp.read()

        try:
            await asyncio.get_event_loop().run_in_executor(None, _post)
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            log.warning("Webhook alert nieudany (%s): %s", alert.kind, exc)


def sinks_from_env(env: dict | None = None) -> list[AlertSink]:
    """Składa listę sinków z env (sekrety nigdy w repo):

    - ONEWISH_ALERT_DISCORD_WEBHOOK — pełny URL webhooka Discord/Slack,
    - ONEWISH_ALERT_TELEGRAM_TOKEN + ONEWISH_ALERT_TELEGRAM_CHAT — bot Telegram.

    LogSink jest zawsze (lokalny ślad). Brak konfiguracji = tylko log.
    """
    env = dict(os.environ) if env is None else env
    sinks: list[AlertSink] = [LogSink()]

    discord = env.get("ONEWISH_ALERT_DISCORD_WEBHOOK")
    if discord:
        sinks.append(WebhookSink(discord, fmt=lambda a: {"content": a.text()}))

    tg_token = env.get("ONEWISH_ALERT_TELEGRAM_TOKEN")
    tg_chat = env.get("ONEWISH_ALERT_TELEGRAM_CHAT")
    if tg_token and tg_chat:
        url = f"https://api.telegram.org/bot{tg_token}/sendMessage"
        sinks.append(WebhookSink(url, fmt=lambda a: {"chat_id": tg_chat, "text": a.text()}))

    return sinks


# -- manager ---------------------------------------------------------------- #
class AlertManager:
    SOURCE = "alerts"

    ALERT_EVENTS = (
        EventType.MARGIN_WARNING,
        EventType.EMERGENCY_STOP,
        EventType.KILL_SWITCH,
        EventType.RISK_LIMIT_BREACH,
        EventType.STALE_FEED,
        EventType.DATA_LAG_WARNING,
        EventType.ORDER_REJECTED,
    )
    # te zdarzenia są zawsze krytyczne, niezależnie od severity eventu źródłowego
    _ALWAYS_CRITICAL = {EventType.EMERGENCY_STOP, EventType.KILL_SWITCH}

    def __init__(self, sinks: list[AlertSink] | None = None, *,
                 min_severity: Severity = Severity.WARNING,
                 cooldown_s: float = 60.0) -> None:
        self.sinks = list(sinks) if sinks else [LogSink()]
        self.min_severity = min_severity
        self.cooldown_s = cooldown_s
        self._last: dict = {}          # (kind, asset) -> ts ostatniego wysłanego
        self.sent = 0
        self.suppressed = 0

    def attach(self, bus: EventBus) -> None:
        for et in self.ALERT_EVENTS:
            bus.subscribe(et, self._on_event)

    async def _on_event(self, event: Event) -> None:
        alert = self._build(event)
        if alert is None:
            return
        if _SEV_RANK[alert.severity] < _SEV_RANK[self.min_severity]:
            return

        key = (alert.kind, alert.asset)
        critical = alert.severity == Severity.CRITICAL
        last = self._last.get(key)
        if not critical and last is not None and (alert.ts - last) < self.cooldown_s:
            self.suppressed += 1
            return

        self._last[key] = alert.ts
        self.sent += 1
        for sink in self.sinks:
            await sink.send(alert)

    def _build(self, event: Event) -> Alert | None:
        p = event.payload if isinstance(event.payload, dict) else {}
        asset = p.get("asset")
        et = event.type
        sev = Severity.CRITICAL if et in self._ALWAYS_CRITICAL else event.severity

        if et == EventType.MARGIN_WARNING:
            action = p.get("action", "?")
            health = p.get("health")
            title = f"Margin {action} {asset or ''}".strip()
            detail = f"health={health:.2f}" if isinstance(health, (int, float)) else ""
            if action == "flatten":
                sev = Severity.CRITICAL
        elif et == EventType.EMERGENCY_STOP:
            title, detail = "EMERGENCY STOP", str(p.get("reason", ""))
        elif et == EventType.KILL_SWITCH:
            title, detail = "KILL SWITCH", str(p.get("reason", ""))
        elif et == EventType.RISK_LIMIT_BREACH:
            action = p.get("action", "")
            title = f"Risk limit {action} {asset or ''}".strip()
            reasons = p.get("reasons")
            detail = ", ".join(reasons) if isinstance(reasons, (list, tuple)) else ""
        elif et == EventType.STALE_FEED:
            gap = p.get("gap_s")
            title = "Stale feed"
            detail = f"luka {gap:.0f}s" if isinstance(gap, (int, float)) else ""
        elif et == EventType.DATA_LAG_WARNING:
            lag = p.get("data_lag_ms")
            title = f"Data lag {asset or ''}".strip()
            detail = f"{lag:.0f}ms" if isinstance(lag, (int, float)) else ""
        elif et == EventType.ORDER_REJECTED:
            title, detail = "Order rejected", str(p.get("reason", ""))
        else:
            return None

        return Alert(ts=event.ts, severity=sev, kind=et.value, title=title,
                     detail=detail, asset=asset)
