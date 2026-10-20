@echo off
rem Startet das Ambilight-Programm und oeffnet die Oberflaeche im Browser.
rem Zusaetzliche Argumente werden durchgereicht, z.B.  start.bat --dry-run
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
    echo Erster Start: richte Python-Umgebung ein ...
    python -m venv .venv || goto :error
    ".venv\Scripts\python.exe" -m pip install -q -r requirements.txt || goto :error
)

rem Browser erst nach kurzer Pause oeffnen, damit der Server schon laeuft
rem (nicht, wenn EDITH das Programm startet: AMBILIGHT_NO_BROWSER=1)
if not defined AMBILIGHT_NO_BROWSER start "" /b cmd /c "timeout /t 3 /nobreak >nul & start "" http://localhost:8765"
rem Bei einem Absturz die Ursache anzeigen und das Fenster offen lassen
set PYTHONFAULTHANDLER=1
".venv\Scripts\python.exe" tools\ambilight.py %*
if %errorlevel% neq 0 (
    echo.
    echo Programm abgestuerzt, Code %errorlevel%. Bitte die Meldung oben weitergeben.
    pause
)
goto :eof

:error
echo Einrichtung fehlgeschlagen.
pause
