@echo off
:: Xoul Desktop Client Launcher
:: 콘솔 창 없이 실행합니다

cd /d "%~dp0.."
if exist ".venv\Scripts\pythonw.exe" (
    start "" .venv\Scripts\pythonw.exe desktop\xoul.pyw
) else (
    start "" .venv\Scripts\python.exe desktop\xoul.pyw
)
