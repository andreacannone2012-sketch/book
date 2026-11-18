@echo off
setlocal
cd /d "%~dp0"
docker compose down
if errorlevel 1 (
  echo Impossibile arrestare il servizio. Verifica che Docker Desktop sia avviato.
  pause
  exit /b 1
)
echo Schoolbook Studio arrestato. I PDF restano in data\downloads.
pause
endlocal
