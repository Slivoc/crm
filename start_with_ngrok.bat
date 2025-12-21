@echo off
title Flask App with ngrok
color 0A
echo.
echo ===============================================
echo     Starting Flask App with ngrok Tunnel
echo ===============================================
echo.

REM Check if ngrok is installed
ngrok version >nul 2>&1
if %errorlevel% neq 0 (
    echo ❌ ngrok is not installed or not in PATH
    echo Please install ngrok from https://ngrok.com/download
    echo and add it to your PATH
    pause
    exit /b 1
)

REM Check if Python is installed
python --version >nul 2>&1
if %errorlevel% neq 0 (
    echo ❌ Python is not installed or not in PATH
    pause
    exit /b 1
)

REM Install waitress if not present
pip show waitress >nul 2>&1
if %errorlevel% neq 0 (
    echo 📦 Installing Waitress...
    pip install waitress
)

echo 🚀 Starting Flask application...
echo.

REM Start Flask app in background
start /B python wsgi.py

REM Wait a bit for the server to start
timeout /t 3 /nobreak >nul

echo 🌐 Starting ngrok tunnel...
echo.

REM Start ngrok with your subdomain and basic auth (new syntax)
ngrok http 8080 --subdomain=sproutt --basic-auth="sproutt:sprouttt"

echo.
echo Tunnel stopped. Cleaning up...

REM Kill the Flask process
taskkill /F /IM python.exe /T >nul 2>&1

echo Server stopped.
pause