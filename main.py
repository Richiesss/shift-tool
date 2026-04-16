"""エントリーポイント"""
import sys
import os
import traceback
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
from db.database import initialize_db
from ui.main_window import MainWindow
from utils.theme import theme


def main():
    initialize_db()

    app = QApplication(sys.argv)
    app.setApplicationName("SDU-Shift")
    app.setStyle("Fusion")

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
