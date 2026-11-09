@echo off
setlocal
set "APP_DIR=%~dp0"
set "TARGET=%APP_DIR%VBCCPAS-Soft.exe"
set "RECOVERY=%LOCALAPPDATA%\VBCCPAS\Recovery\updater-recovery.ps1"

if exist "%RECOVERY%" (
    echo Checking for an interrupted VBCCPAS installation...
    powershell.exe -NoProfile -NonInteractive -ExecutionPolicy Bypass -File "%RECOVERY%" -TargetExe "%TARGET%" -RecoverOnly -RestartAfterRecovery
    if errorlevel 1 (
        echo Recovery could not be completed. See:
        echo %LOCALAPPDATA%\VBCCPAS\update.log
        pause
        exit /b 1
    )
)

if exist "%APP_DIR%VBCCPAS-Updater.exe" (
    start "" "%APP_DIR%VBCCPAS-Updater.exe"
    exit /b 0
)
if exist "%TARGET%" (
    start "" "%TARGET%"
    exit /b 0
)

echo Neither the main application nor its standalone updater is available.
echo Re-extract the complete VBCCPAS-Soft-Windows.zip package to this folder.
pause
exit /b 1
