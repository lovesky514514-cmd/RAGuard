@echo off
if not exist "%~dp0scripts\windows_entry.cmd" (
  echo Project files are incomplete. Extract the full ZIP first.
  pause
  exit /b 1
)
call "%~dp0scripts\windows_entry.cmd" start
