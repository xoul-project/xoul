@echo off
:: Xoul Auto Start — Windows 부팅 시 전체 스택 시작
:: 1. Launcher (VM + Ollama + Server + Sync)
:: 2. Desktop Client

cd /d "%~dp0.."

:: 1. Launcher 실행 (VM + Ollama + Server)
start /min "" powershell.exe -ExecutionPolicy Bypass -WindowStyle Minimized -File "scripts\launcher.ps1"

:: launcher가 초기화될 시간 확보
timeout /t 5 /nobreak >nul

:: 2. Desktop 클라이언트 실행
if exist ".venv\Scripts\pythonw.exe" (
    start "" .venv\Scripts\pythonw.exe desktop\xoul.pyw
) else (
    start "" .venv\Scripts\python.exe desktop\xoul.pyw
)
