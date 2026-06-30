"""Testy alertów operatora poza GUI: routing eventów, próg severity, throttling,
CRITICAL nigdy nietłumiony, format, konfiguracja z env."""
import asyncio

from backend.core.bus import EventBus
from backend.core.events import Event, EventType, Severity
from backend.monitoring import AlertManager, BufferSink, LogSink, sinks_from_env
from backend.monitoring.alerts import Alert, WebhookSink


def _mgr(**kw) -> tuple[AlertManager, BufferSink]:
    buf = BufferSink()
    mgr = AlertManager([buf], **kw)
    return mgr, buf


def _run(mgr: AlertManager, *events: Event) -> None:
    async def go():
        for e in events:
            await mgr._on_event(e)
    asyncio.run(go())


def _ev(etype, ts, payload, severity=Severity.WARNING) -> Event:
    return Event(etype, ts, "test", severity, payload=payload)


# -- routing + format ------------------------------------------------------- #
def test_margin_flatten_is_critical_alert():
    mgr, buf = _mgr()
    _run(mgr, _ev(EventType.MARGIN_WARNING, 1.0,
                  {"asset": "BTC", "health": 1.21, "action": "flatten"}, Severity.CRITICAL))
    assert len(buf.alerts) == 1
    a = buf.alerts[0]
    assert a.severity == Severity.CRITICAL
    assert a.asset == "BTC"
    assert "Margin flatten BTC" in a.title
    assert "health=1.21" in a.text()


def test_emergency_and_kill_are_always_critical():
    mgr, buf = _mgr()
    # nawet z severity INFO na evencie — kill/emergency podnosimy do CRITICAL
    _run(mgr,
         _ev(EventType.EMERGENCY_STOP, 1.0, {"reason": "dzienny limit straty"}, Severity.INFO),
         _ev(EventType.KILL_SWITCH, 2.0, {"reason": "operator kill"}, Severity.INFO))
    assert [a.severity for a in buf.alerts] == [Severity.CRITICAL, Severity.CRITICAL]
    assert "EMERGENCY STOP" in buf.alerts[0].text()
    assert "operator kill" in buf.alerts[1].text()


def test_risk_limit_breach_lists_reasons():
    mgr, buf = _mgr()
    _run(mgr, _ev(EventType.RISK_LIMIT_BREACH, 1.0,
                  {"asset": "ETH", "action": "circuit_open", "reasons": ["premium spike", "OI jump"]},
                  Severity.CRITICAL))
    assert "Risk limit circuit_open ETH" in buf.alerts[0].title
    assert "premium spike, OI jump" in buf.alerts[0].text()


# -- próg severity ---------------------------------------------------------- #
def test_info_below_threshold_is_dropped():
    mgr, buf = _mgr(min_severity=Severity.WARNING)
    # circuit_close idzie jako INFO → poniżej progu
    _run(mgr, _ev(EventType.RISK_LIMIT_BREACH, 1.0,
                  {"asset": "ETH", "action": "circuit_close"}, Severity.INFO))
    assert buf.alerts == []


def test_critical_threshold_drops_warnings():
    mgr, buf = _mgr(min_severity=Severity.CRITICAL)
    _run(mgr, _ev(EventType.DATA_LAG_WARNING, 1.0, {"asset": "BTC", "data_lag_ms": 1500}, Severity.WARNING))
    assert buf.alerts == []


# -- throttling ------------------------------------------------------------- #
def test_throttle_same_kind_asset_within_cooldown():
    mgr, buf = _mgr(cooldown_s=60.0)
    _run(mgr,
         _ev(EventType.DATA_LAG_WARNING, 0.0, {"asset": "BTC", "data_lag_ms": 1200}),
         _ev(EventType.DATA_LAG_WARNING, 30.0, {"asset": "BTC", "data_lag_ms": 1300}),  # w cooldownie
         _ev(EventType.DATA_LAG_WARNING, 90.0, {"asset": "BTC", "data_lag_ms": 1400}))  # po cooldownie
    assert len(buf.alerts) == 2
    assert mgr.suppressed == 1


def test_throttle_is_per_asset():
    mgr, buf = _mgr(cooldown_s=60.0)
    _run(mgr,
         _ev(EventType.DATA_LAG_WARNING, 0.0, {"asset": "BTC", "data_lag_ms": 1200}),
         _ev(EventType.DATA_LAG_WARNING, 1.0, {"asset": "ETH", "data_lag_ms": 1200}))  # inne aktywo
    assert len(buf.alerts) == 2          # różne klucze → oba przechodzą


def test_critical_bypasses_cooldown():
    mgr, buf = _mgr(cooldown_s=600.0)
    _run(mgr,
         _ev(EventType.MARGIN_WARNING, 0.0, {"asset": "BTC", "health": 1.2, "action": "flatten"}, Severity.CRITICAL),
         _ev(EventType.MARGIN_WARNING, 1.0, {"asset": "BTC", "health": 1.1, "action": "flatten"}, Severity.CRITICAL))
    assert len(buf.alerts) == 2          # CRITICAL nigdy nietłumiony
    assert mgr.suppressed == 0


# -- integracja z szyną ----------------------------------------------------- #
def test_attach_subscribes_and_receives():
    bus = EventBus()
    buf = BufferSink()
    AlertManager([buf]).attach(bus)
    asyncio.run(bus.publish(Event(EventType.EMERGENCY_STOP, 5.0, "monitor",
                                  Severity.CRITICAL, payload={"reason": "test"})))
    assert len(buf.alerts) == 1 and buf.alerts[0].kind == "EMERGENCY_STOP"


# -- konfiguracja z env ----------------------------------------------------- #
def test_sinks_from_env_default_log_only():
    sinks = sinks_from_env({})
    assert len(sinks) == 1 and isinstance(sinks[0], LogSink)


def test_sinks_from_env_adds_discord_and_telegram():
    sinks = sinks_from_env({
        "ONEWISH_ALERT_DISCORD_WEBHOOK": "https://discord/x",
        "ONEWISH_ALERT_TELEGRAM_TOKEN": "tok",
        "ONEWISH_ALERT_TELEGRAM_CHAT": "42",
    })
    assert len(sinks) == 3
    assert sum(isinstance(s, WebhookSink) for s in sinks) == 2


def test_telegram_token_without_chat_is_ignored():
    sinks = sinks_from_env({"ONEWISH_ALERT_TELEGRAM_TOKEN": "tok"})   # brak chat_id
    assert len(sinks) == 1 and isinstance(sinks[0], LogSink)


# -- format webhooków ------------------------------------------------------- #
def test_webhook_formats_discord_and_telegram():
    a = Alert(ts=1.0, severity=Severity.CRITICAL, kind="EMERGENCY_STOP",
              title="EMERGENCY STOP", detail="x")
    discord = WebhookSink("u")
    telegram = WebhookSink("u", fmt=lambda al: {"chat_id": "1", "text": al.text()})
    assert discord.fmt(a) == {"content": "[CRITICAL] EMERGENCY STOP — x"}
    assert telegram.fmt(a)["text"] == "[CRITICAL] EMERGENCY STOP — x"


def test_empty_url_webhook_is_noop():
    asyncio.run(WebhookSink(None).send(
        Alert(ts=1.0, severity=Severity.INFO, kind="X", title="t")))   # brak wyjątku
