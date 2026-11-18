@echo off
setlocal
cd /d "%~dp0"
where docker >nul 2>nul
if errorlevel 1 (
  echo Docker non trovato. Installa e avvia Docker Desktop, poi riprova.
  pause
  exit /b 1
)
echo Avvio Schoolbook Studio...
docker compose up -d --build
if errorlevel 1 (
  echo Avvio non riuscito. Controlla Docker Desktop e riprova.
  pause
  exit /b 1
)
echo.
echo Servizio pronto: http://localhost:8080
start "" "http://localhost:8080"
endlocal
