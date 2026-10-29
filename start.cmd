@echo off
rem One-command start-up (Windows). See scripts\start.py --help.
cd /d "%~dp0"
where py >nul 2>&1
if %errorlevel%==0 (
  py -3 scripts\start.py %*
) else (
  python scripts\start.py %*
)
