"""エントリーポイント"""
import sys
import os
import traceback
import subprocess
from pathlib import Path

# パッケージルートをsys.pathに追加
sys.path.insert(0, os.path.dirname(__file__))

# frozen（EXE/app）実行時はクラッシュログをホームに出力
if getattr(sys, "frozen", False):
    _log_path = Path.home() / "SDU-Shift-error.log"
    try:
        _log_path.unlink(missing_ok=True)
    except Exception:
        pass
    sys.stdout = open(_log_path, "w", encoding="utf-8")
    sys.stderr = sys.stdout

from PyQt6.QtWidgets import QApplication
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QIcon
from db.database import initialize_db
from ui.main_window import MainWindow
from utils.theme import theme


def _get_base_path() -> Path:
    """frozen EXE では _MEIPASS、開発環境ではスクリプトのディレクトリを返す"""
    if getattr(sys, "frozen", False):
        return Path(sys._MEIPASS)
    return Path(__file__).parent


def _setup_windows_taskbar():
    """Windows タスクバーにアイコンを正しく表示するための設定"""
    if sys.platform != "win32":
        return
    try:
        import ctypes
        # プロセスに固有の AppUserModelID を設定する。
        # これがないと Windows が本プロセスを python.exe と同一グループ扱いにする。
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("com.sdu.shift")
    except Exception:
        pass


def _create_desktop_shortcut():
    """初回起動時にデスクトップへショートカット（.lnk）を作成する"""
    if sys.platform != "win32" or not getattr(sys, "frozen", False):
        return
    try:
        desktop = Path(os.environ.get("USERPROFILE", Path.home())) / "Desktop"
        shortcut = desktop / "SDU-Shift.lnk"
        if shortcut.exists():
            return
        exe = Path(sys.executable)
        # パスのバックスラッシュをエスケープ
        exe_str = str(exe).replace("\\", "\\\\")
        lnk_str = str(shortcut).replace("\\", "\\\\")
        script = (
            f'$s=(New-Object -COM WScript.Shell).CreateShortcut("{lnk_str}");'
            f'$s.TargetPath="{exe_str}";'
            f'$s.Description="SDU-Shift";'
            f'$s.Save()'
        )
        subprocess.run(
            ["powershell", "-WindowStyle", "Hidden", "-NonInteractive", "-Command", script],
            capture_output=True,
            timeout=10,
        )
    except Exception:
        pass


def main():
    _setup_windows_taskbar()
    _create_desktop_shortcut()

    initialize_db()

    app = QApplication(sys.argv)
    app.setApplicationName("SDU-Shift")
    app.setStyle("Fusion")

    # アイコンを明示的にセット（タスクバー・ウィンドウ枠に反映）
    icon_path = _get_base_path() / "assets" / "icon.ico"
    if icon_path.exists():
        app.setWindowIcon(QIcon(str(icon_path)))

    # OS のカラースキームを検出してテーマを適用
    theme.apply()

    window = MainWindow()
    window.show()

    # OS のダークモード切り替えをリアルタイムで反映
    try:
        app.styleHints().colorSchemeChanged.connect(
            lambda: theme.apply()
        )
    except Exception:
        pass

    sys.exit(app.exec())


if __name__ == "__main__":
    try:
        main()
    except Exception:
        traceback.print_exc()
        if getattr(sys, "frozen", False):
            sys.stdout.flush()
        raise
