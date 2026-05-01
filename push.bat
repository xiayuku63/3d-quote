@echo off
setlocal enabledelayedexpansion

REM push.bat
REM CMD script - auto commit and push to GitHub (Windows)
REM
REM Usage:
REM   push
REM   push "feat: update slicer engine"

set REMOTE=origin
set BRANCH=main

echo.
echo ============================================================
echo   Git Auto Push to GitHub
echo   Remote: %REMOTE%  |  Branch: %BRANCH%
echo ============================================================
echo.

echo [STATUS] Current changes:
git status --short
echo.

git status --porcelain >nul 2>&1
if %errorlevel% neq 0 (
    echo [ERROR] Not a git repository. Run from project root.
    goto :EOF
)

git status --porcelain | findstr /R "." >nul 2>&1
if errorlevel 1 (
    echo [DONE] No changes to commit.
    goto :EOF
)

set MSG=%~1
if "%MSG%"=="" (
    set /p MSG="Enter commit message: "
)
if "%MSG%"=="" (
    echo [ERROR] Commit message cannot be empty.
    exit /b 1
)

echo.
echo [STEP] git add -A
git add -A
if errorlevel 1 (
    echo [ERROR] git add failed.
    exit /b 1
)

echo [STEP] git commit -m "%MSG%"
git commit -m "%MSG%"
if errorlevel 1 (
    echo [ERROR] Commit failed.
    exit /b 1
)
echo [OK] Commit succeeded.

echo.
echo [STEP] git push %REMOTE% %BRANCH%
git push %REMOTE% %BRANCH%
if errorlevel 1 (
    echo.
    echo [ERROR] Push failed. Check network or Git credentials.
    exit /b 1
)

echo.
echo ============================================================
echo    PUSH SUCCESS
echo ============================================================
