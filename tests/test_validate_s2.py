"""Test automatu oceny S2: na bazie z realnej (syntetycznej) sesji pipeline'u."""
import asyncio
import importlib.util
import os
import sys

_SPEC = importlib.util.spec_from_file_location(
    "validate_s2",
    os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                 "scripts", "validate_s2.py"))
validate_s2 = importlib.util.module_from_spec(_SPEC)
sys.modules["validate_s2"] = validate_s2
_SPEC.loader.exec_module(validate_s2)

from backend.app.runner import OneWishApp  # noqa: E402
from backend.storage import Database  # noqa: E402
from tests.test_runner import _generous  # noqa: E402


def _run_session(db_path: str, steps: int = 300) -> None:
    app = OneWishApp(mode="synthetic", steps=steps, seed=3, gui=False,
                     risk_config=_generous(), notional_usd=200.0, db_path=db_path)
    asyncio.run(app.run())


def test_s2_evaluator_measures_real_session_db(tmp_path):
    path = str(tmp_path / "s2.db")
    _run_session(path)
    checks = validate_s2.evaluate(Database(path), min_hours=0.0)
    by = {c["crit"]: c for c in checks}

    assert by["2.1 czas sesji"]["ok"] is True                 # min_hours=0 → zalicza
    assert by["2.3 świeżość danych"]["ok"] is True            # syntetyk: lag ~5 ms
    assert by["2.4 wejścia z sygnału"]["ok"] is True          # każde wejście z EDGE
    assert by["2.5 delta-neutralność"]["ok"] is True          # pary domknięte per sztuka
    assert by["2.6/2.8 funding+PnL"]["ok"] is True


def test_s2_evaluator_fails_on_too_short_session(tmp_path):
    path = str(tmp_path / "s2short.db")
    _run_session(path, steps=50)
    checks = validate_s2.evaluate(Database(path), min_hours=48.0)
    by = {c["crit"]: c for c in checks}
    assert by["2.1 czas sesji"]["ok"] is False                # 50 kroków ≪ 48 h
