@echo off
setlocal EnableExtensions
chcp 65001 >nul
set "PYTHONUTF8=1"
set "PYTHONIOENCODING=utf-8"
cd /d "%~dp0.."
if not exist "logs" mkdir "logs"
set "ACTION=%~1"
if "%ACTION%"=="" set "ACTION=start"
set "PYTHON_CMD="
where py >nul 2>nul
if not errorlevel 1 (
  py -3 -c "import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)" >nul 2>nul
  if not errorlevel 1 set "PYTHON_CMD=py -3"
)
if defined PYTHON_CMD goto run
where python >nul 2>nul
if errorlevel 1 goto missing_python
python -c "import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)" >nul 2>nul
if errorlevel 1 goto missing_python
set "PYTHON_CMD=python"
:run
%PYTHON_CMD% -X utf8 scripts\launcher.py %ACTION%
set "CODE=%errorlevel%"
goto finish
:missing_python
echo [ERROR] Python 3.11+ was not found. Install Python and enable PATH.
>> "logs\运行日志.txt" echo Python 3.11+ not found. Install Python and enable PATH.
set "CODE=1"
:finish
echo.
if "%CODE%"=="0" (
  echo 已结束。运行日志：logs\运行日志.txt
) else (
  echo 启动或检查失败，请查看 logs\运行日志.txt
)
if not "%RAGUARD_NO_PAUSE%"=="1" pause
exit /b %CODE%
