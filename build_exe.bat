@echo off
chcp 65001 > nul
echo ==============================================================
echo  PMS 주간보고 자동화 (.exe) 빌드 스크립트 (PyInstaller)
echo ==============================================================
echo.

REM 1. PyInstaller 설치 여부 확인
python -m pip show pyinstaller > nul 2>&1
if errorlevel 1 (
    echo [*] PyInstaller가 설치되어 있지 않아 설치를 진행합니다...
    python -m pip install pyinstaller
)

REM 2. Playwright 패키지 확인
python -m pip show playwright > nul 2>&1
if errorlevel 1 (
    echo [*] Playwright 패키지 설치 중...
    python -m pip install playwright
)

echo [*] PyInstaller 단일 실행 파일(.exe) 빌드를 시작합니다...
echo     (Playwright 드라이버 리소스를 완전 포함 번들링)
echo.

cd /d "%~dp0"
python -m PyInstaller --onefile --noconfirm --clean ^
    --name "PMS_Weekly_Report" ^
    --collect-all playwright ^
    run_pms_sync.py

if errorlevel 1 (
    echo.
    echo [!] 빌드 중 오류가 발생했습니다.
    pause
    exit /b 1
)

echo.
echo ==============================================================
echo  [성공] 빌드가 완료되었습니다!
echo  생성 위치: %~dp0dist\PMS_Weekly_Report.exe
echo ==============================================================
echo.
pause
