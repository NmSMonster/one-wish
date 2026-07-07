@echo off
REM One Wish — serwuje GUI (cockpit) i otwiera przegladarke.
REM Odpal ROWNOLEGLE z start_live.bat (osobne okno). Wymaga js\config.js
REM z adapter: "ws" (jesli widzisz baner SIMULATION/MOCK - zmien mock -> ws).
cd /d "%~dp0"
start "" http://127.0.0.1:8080/index.html
python -m http.server 8080
pause
