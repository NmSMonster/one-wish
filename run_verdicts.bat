@echo off
REM One Wish — werdykty pomiarowe (etap S3 z VALIDATION.md):
REM   1) study_funding: aktualny carry Tier A na realnej historii -> CARRY_VERDICT.md
REM   2) study_venues:  uplift multi-venue Tier B                 -> VENUE_SCAN.md
cd /d "%~dp0"
python -m pip install -q -r requirements.txt
echo === TIER A: carry na realnej historii funding ===
python scripts\study_funding.py
echo.
echo === TIER B: porownanie venue (Binance vs Bybit vs OKX) ===
python scripts\study_venues.py
echo.
echo Gotowe. Wyniki: CARRY_VERDICT.md i VENUE_SCAN.md — wyslij je do sesji Claude.
pause
