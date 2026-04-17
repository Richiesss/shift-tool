"""自動アップデートユーティリティ"""
from __future__ import annotations
import os
import sys
import json
import urllib.request
from pathlib import Path


def fetch_latest_release_info() -> dict:
    """
    GitHub Releases API から最新リリース情報を取得する。
    戻り値: {"tag": str, "html_url": str, "exe_url": str, "mac_url": str}
    失敗時は空文字列をセットして返す。
    """
    api_url = "https://api.github.com/repos/Richiesss/shift-tool/releases/latest"
    req = urllib.request.Request(
        api_url, headers={"User-Agent": "SDU-Shift-Updater"})
    with urllib.request.urlopen(req, timeout=10) as resp:
        data = json.loads(resp.read())

    tag      = data.get("tag_name", "")
    html_url = data.get("html_url", "")
    exe_url  = ""
    mac_url  = ""
    for asset in data.get("assets", []):
        name = asset.get("name", "")
        dl   = asset.get("browser_download_url", "")
        if name == "SDU-Shift.exe":
            exe_url = dl
        elif name == "SDU-Shift-mac.zip":
            mac_url = dl

    return {"tag": tag, "html_url": html_url, "exe_url": exe_url, "mac_url": mac_url}


def download_file(url: str, dest: str, progress_cb=None):
    """
    url をファイル dest にダウンロードする。
    progress_cb(downloaded: int, total: int) を随時呼び出す（total=0 は不明）。
    """
    req = urllib.request.Request(url, headers={"User-Agent": "SDU-Shift-Updater"})
    with urllib.request.urlopen(req, timeout=120) as resp:
        total      = int(resp.headers.get("Content-Length", 0))
        downloaded = 0
        chunk_size = 65536  # 64 KB
        with open(dest, "wb") as f:
            while True:
                chunk = resp.read(chunk_size)
                if not chunk:
                    break
                f.write(chunk)
                downloaded += len(chunk)
                if progress_cb:
                    progress_cb(downloaded, total)


def get_current_exe() -> str:
    """凍結アプリの EXE パスを返す。未凍結なら空文字列。"""
    if getattr(sys, "frozen", False):
        return sys.executable
    return ""


def launch_windows_updater(old_exe: str, new_exe: str) -> bool:
    """
    バッチスクリプトを生成して現プロセス終了後に:
      1. 旧 EXE を削除
      2. 新 EXE を旧 EXE のパスに移動
      3. バッチ自己削除
    を行う。失敗時は False を返す。

    ※ 自動再起動は行わない。
       PyInstaller one-file モードは起動時に %TEMP%/_MEIxxxxxx を展開するが、
       バッチ経由で start した場合に DLL 検索パスが正しく設定されずクラッシュする。
       ユーザーが手動でアプリを起動することで確実に動作する。
    """
    import tempfile
    import subprocess

    # CREATE_NO_WINDOW: ターミナルウィンドウを非表示にする
    CREATE_NO_WINDOW = 0x08000000

    try:
        fd, bat_path = tempfile.mkstemp(suffix=".bat")
        os.close(fd)
        with open(bat_path, "w", encoding="cp932") as f:
            # ping -n 12 = 約11秒待機
            # QApplication.quit() → atexit → PyInstaller が _MEIxxxxxx を削除するまでの時間を確保
            f.write(f"""@echo off
chcp 932 > nul
ping -n 12 127.0.0.1 > nul
del /f /q "{old_exe}"
move /Y "{new_exe}" "{old_exe}"
del "%~f0"
""")
        subprocess.Popen(
            ["cmd.exe", "/c", bat_path],
            creationflags=(
                CREATE_NO_WINDOW |
                subprocess.CREATE_NEW_PROCESS_GROUP
            ),
            close_fds=True,
        )
        return True
    except Exception:
        return False
