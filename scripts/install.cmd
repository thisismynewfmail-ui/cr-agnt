@echo off
REM ============================================================================
REM Curie Agent Installer for Windows (CMD wrapper)
REM ============================================================================
REM This batch file launches the PowerShell installer for users running CMD.
REM
REM Usage:
REM   curl -fsSL https://raw.githubusercontent.com/thisismynewfmail-ui/cru/main/scripts/install.cmd -o install.cmd && install.cmd && del install.cmd
REM
REM Or if you're already in PowerShell, use the direct command instead:
REM   iex (irm https://raw.githubusercontent.com/thisismynewfmail-ui/cru/main/scripts/install.ps1)
REM
REM To install from a fork or a mirror, set CURIE_REPO to its "owner/name"
REM slug before running:  set CURIE_REPO=me/my-fork
REM ============================================================================

REM One slug, and both URLs below derived from it -- the same reason the shell
REM and PowerShell installers keep theirs in one place.
if "%CURIE_REPO%"=="" set "CURIE_REPO=thisismynewfmail-ui/cru"
set "CURIE_PS1_URL=https://raw.githubusercontent.com/%CURIE_REPO%/main/scripts/install.ps1"

echo.
echo  Curie Agent Installer
echo  Launching PowerShell installer...
echo.

powershell -ExecutionPolicy ByPass -NoProfile -Command "iex (irm '%CURIE_PS1_URL%')"

if %ERRORLEVEL% NEQ 0 (
    echo.
    echo  Installation failed. Please try running PowerShell directly:
    echo    powershell -ExecutionPolicy ByPass -c "iex (irm '%CURIE_PS1_URL%')"
    echo.
    pause
    exit /b 1
)
