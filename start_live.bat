@echo off
REM One Wish — start sesji paper-live na ZYWYCH danych (etap S2 z VALIDATION.md).
REM Egzekucja PAPIEROWA - zero realnych pieniedzy. Zatrzymanie: Ctrl+C (raport
REM wypisze sie sam). Restart po padzie: odpal ponownie - pozycje wroca (recovery).
cd /d "%~dp0"

echo [1/3] Aktualizacja kodu (git pull)...
git pull origin claude/handoff-documentation-tgert9
if errorlevel 1 echo   (pull nie przeszedl - jade na lokalnej wersji)

echo [2/3] Zaleznosci...
python -m pip install -q -r requirements.txt

echo [3/3] Start bota (zywe dane Binance przez WebSocket, budzet 150 zl, paper)...
python scripts\run_paper_live.py --mode live --budget-pln 150 --funding-weighted --transport ws
pause
