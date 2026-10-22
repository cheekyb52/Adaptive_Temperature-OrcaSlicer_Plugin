@echo off
setlocal
set "ADAPTIVE_GIT=%LOCALAPPDATA%\AdaptiveTemperatureTools\Git\cmd\git.exe"
if exist "%ADAPTIVE_GIT%" (
  "%ADAPTIVE_GIT%" %*
  exit /b
)
where git.exe >nul 2>nul
if errorlevel 1 (
  echo Install Git for Windows or GitHub Desktop, then reopen the terminal.
  exit /b 1
)
git.exe %*
