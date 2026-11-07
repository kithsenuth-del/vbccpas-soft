@echo off
setlocal
cd /d "%~dp0"
if not exist "%~dp0VBCCPAS-Soft.exe" (
    echo VBCCPAS-Soft.exe was not found. Keep this file beside the application.
    pause
    exit /b 1
)
echo Creating your VBCCPAS Soft desktop shortcut...
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0Create-Desktop-Shortcut.ps1"
if errorlevel 1 (
    echo Could not create the shortcut. You can still run VBCCPAS-Soft.exe directly.
    pause
    exit /b 1
)
echo Starting VBCCPAS Soft...
start "" "%~dp0VBCCPAS-Soft.exe"
exit /b 0
