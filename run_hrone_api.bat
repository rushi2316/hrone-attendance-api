@echo off
title HROne Employee Attendance & Analytics API
color 0B
echo ====================================================================
echo             HRONE EMPLOYEE ATTENDANCE & ANALYTICS API
echo ====================================================================
echo.

cd /d "C:\Users\rushi\hrone-attendance-api"

echo [*] Checking MongoDB service...
sc query MongoDB | find "RUNNING" >nul
if %errorlevel% neq 0 (
    echo [!] Attempting to start MongoDB service...
    net start MongoDB 2>nul
)

echo [*] Starting FastAPI Backend Service (Port 8000)...
start "HROne API - FastAPI Server (Port 8000)" cmd /k "cd /d C:\Users\rushi\hrone-attendance-api && .venv\Scripts\uvicorn.exe app.main:app --port 8000 --reload"

echo [*] Waiting 3 seconds for server initialization...
timeout /t 3 /nobreak >nul

echo [*] Opening Swagger API Documentation in default browser...
start http://localhost:8000/docs

echo.
echo ====================================================================
echo [OK] HROne API is now running!
echo      - Swagger UI Documentation : http://localhost:8000/docs
echo      - ReDoc Documentation      : http://localhost:8000/redoc
echo      - Health Probe             : http://localhost:8000/health
echo.
echo Keep the server terminal window open/minimized while using the API.
echo When you are finished, simply close the server window.
echo ====================================================================
pause
