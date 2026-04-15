@echo off
chcp 65001 > nul
setlocal enabledelayedexpansion

echo ============================================================
echo   シフト表構築ツール  Windows EXE ビルドスクリプト
echo ============================================================
echo.

REM ── Python チェック ─────────────────────────────────────────
python --version > nul 2>&1
if errorlevel 1 (
    echo [ERROR] Python が見つかりません。
    echo         https://www.python.org/downloads/ から
    echo         Python 3.11 以上をインストールしてください。
    pause & exit /b 1
)

for /f "tokens=2 delims= " %%v in ('python --version 2^>^&1') do set PY_VER=%%v
echo Python バージョン: %PY_VER%

REM ── 仮想環境 ────────────────────────────────────────────────
if not exist ".venv" (
    echo.
    echo [1/5] 仮想環境を作成中...
    python -m venv .venv
    if errorlevel 1 ( echo [ERROR] 仮想環境の作成失敗 & pause & exit /b 1 )
) else (
    echo [1/5] 仮想環境を確認済み
)

echo [2/5] 仮想環境を有効化中...
call .venv\Scripts\activate.bat
if errorlevel 1 ( echo [ERROR] 仮想環境の有効化失敗 & pause & exit /b 1 )

REM ── パッケージインストール ───────────────────────────────────
echo [3/5] 依存パッケージをインストール中...
pip install -r requirements.txt --quiet --disable-pip-version-check
if errorlevel 1 ( echo [ERROR] パッケージインストール失敗 & pause & exit /b 1 )
echo       完了

REM ── 旧ビルドを削除 ──────────────────────────────────────────
echo [4/5] 旧ビルドを削除中...
if exist "dist\シフト表構築ツール.exe" del /q "dist\シフト表構築ツール.exe"
if exist "build\shift_tool"           rmdir /s /q "build\shift_tool"

REM ── PyInstaller ビルド ────────────────────────────────────
echo [5/5] EXE をビルド中（数分かかります）...
pyinstaller shift_tool.spec --clean --noconfirm
if errorlevel 1 (
    echo.
    echo [ERROR] ビルドに失敗しました。
    echo         build\shift_tool\warn-shift_tool.txt を確認してください。
    pause & exit /b 1
)

REM ── 完了 ─────────────────────────────────────────────────────
echo.
echo ============================================================
echo   ビルド完了！
echo.
echo   実行ファイル: dist\シフト表構築ツール.exe
echo.
echo   ※ 初回起動時に Windows Defender のスキャンで
echo     少し時間がかかる場合があります
echo ============================================================
echo.

set /p OPEN="EXEを今すぐ起動しますか？ (Y/N): "
if /i "!OPEN!"=="Y" (
    start "" "dist\シフト表構築ツール.exe"
)
pause
