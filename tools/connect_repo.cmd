@echo off
setlocal
for %%I in ("%~dp0..") do set "ADAPTIVE_REPO=%%~fI"
call "%~dp0git.cmd" -C "%ADAPTIVE_REPO%" rev-parse --git-dir >nul 2>nul
if errorlevel 1 (
  call "%~dp0git.cmd" -C "%ADAPTIVE_REPO%" init -b main
  if errorlevel 1 exit /b 1
)
call "%~dp0git.cmd" -C "%ADAPTIVE_REPO%" config --local remote.origin.url "https://github.com/cheekyb52/Adaptive_Temperature-OrcaSlicer_Plugin.git"
if errorlevel 1 exit /b 1
call "%~dp0git.cmd" -C "%ADAPTIVE_REPO%" config --local remote.origin.fetch "+refs/heads/*:refs/remotes/origin/*"
if errorlevel 1 exit /b 1
call "%~dp0git.cmd" -C "%ADAPTIVE_REPO%" config --local pull.ff only
if errorlevel 1 exit /b 1
call "%~dp0git.cmd" -C "%ADAPTIVE_REPO%" config --local push.default simple
if errorlevel 1 exit /b 1
call "%~dp0git.cmd" -C "%ADAPTIVE_REPO%" fetch origin main
if errorlevel 1 exit /b 1
call "%~dp0git.cmd" -C "%ADAPTIVE_REPO%" rev-parse --verify HEAD >nul 2>nul
if errorlevel 1 (
  rem Initial checkout only: populate the index without changing working files.
  call "%~dp0git.cmd" -C "%ADAPTIVE_REPO%" reset --mixed origin/main
  if errorlevel 1 exit /b 1
)
call "%~dp0git.cmd" -C "%ADAPTIVE_REPO%" branch --set-upstream-to=origin/main main
if errorlevel 1 exit /b 1
call "%~dp0git.cmd" -C "%ADAPTIVE_REPO%" status --short --branch
