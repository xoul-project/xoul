@echo off
chcp 65001 >nul 2>&1
title Xoul 설치

echo.
echo   ========================================
echo     Xoul AI Agent 설치
echo   ========================================
echo.

set "TARGET=C:\xoul"

:: xoul.zip 확인
set "ZIPFILE=%~dp0xoul.zip"

if not exist "%ZIPFILE%" (
    echo   ❌ xoul.zip 파일을 찾을 수 없습니다.
    echo      install.bat과 같은 폴더에 xoul.zip이 있어야 합니다.
    pause
    exit /b 1
)

echo   📦 %ZIPFILE% 발견
echo   📂 %TARGET% 에 설치합니다...

:: 대상 폴더 생성
if not exist "%TARGET%" mkdir "%TARGET%"

:: zip을 C:\xoul로 복사
copy /Y "%ZIPFILE%" "%TARGET%\xoul.zip" >nul 2>&1

:: 압축 해제
echo   📂 압축 해제 중...
powershell -Command "Expand-Archive -Path '%TARGET%\xoul.zip' -DestinationPath '%TARGET%' -Force"

:: 압축 파일 정리
del "%TARGET%\xoul.zip" >nul 2>&1

:: VM 이미지 복사 (있으면)
set "VMIMAGE=%~dp0xoul.qcow2"
if exist "%VMIMAGE%" (
    echo   📦 VM 이미지 발견 — 복사 중... 수 분 소요
    if not exist "%TARGET%\vm" mkdir "%TARGET%\vm"
    copy /Y "%VMIMAGE%" "%TARGET%\vm\xoul.qcow2" >nul 2>&1
    echo   ✅ VM 이미지 복사 완료
) else (
    echo   ⚠ xoul.qcow2 없음 — setup에서 새로 생성합니다
)

echo   ✅ 설치 파일 준비 완료
echo.

:: setup 실행
cd /d "%TARGET%"
echo   🚀 설치를 시작합니다...
echo.
powershell -ExecutionPolicy Bypass -File "%TARGET%\scripts\setup_env.ps1"
pause
