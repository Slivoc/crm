@echo off
title Flask App Server
echo.
echo ===============================================
echo        Starting Your Flask Application
echo ===============================================
echo.

REM Check if Python is installed
python --version >nul 2>&1
if %errorlevel% neq 0 (
    echo ❌ Python is not installed or not in PATH
    echo Please install Python and try again
    pause
    exit /b 1
)

REM Check if waitress is installed
pip show waitress >nul 2>&1
if %errorlevel% neq 0 (
    echo 📦 Installing Waitress...
    pip install waitress
    if %errorlevel% neq 0 (
        echo ❌ Failed to install Waitress
        pause
        exit /b 1
    )
)

REM Start the server
echo 🚀 Starting server...
echo.
python wsgi.py

echo.
echo Server has stopped.
pause