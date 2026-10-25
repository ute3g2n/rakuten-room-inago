@echo off
setlocal
cd /d "%~dp0"
set "HISTORY_PYTHON=%~dp0.venv-inago\Scripts\python.exe"
if not exist "%HISTORY_PYTHON%" (
  echo Python environment not found: .venv-inago
  pause
  exit /b 1
)
"%HISTORY_PYTHON%" -B -m automation.inago.history_launcher %*
set "HISTORY_EXIT=%ERRORLEVEL%"
if not "%HISTORY_EXIT%"=="0" pause
exit /b %HISTORY_EXIT%
