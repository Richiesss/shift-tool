"""メインウィンドウ（サイドバー + コンテンツエリア）"""
from __future__ import annotations
from PyQt6.QtWidgets import (
    QMainWindow, QWidget, QHBoxLayout, QVBoxLayout,
    QPushButton, QLabel, QStackedWidget, QFrame, QSizePolicy,
    QDialog, QProgressBar, QMessageBox
)
from PyQt6.QtCore import Qt, QSize, QThread, pyqtSignal
from PyQt6.QtGui import QFont, QDesktopServices
from PyQt6.QtCore import QUrl

from ui.employee_view import EmployeeView
from ui.shift_input_view import ShiftInputView
from ui.generate_view import GenerateView
from ui.schedule_view import ScheduleView
from ui.settings_view import SettingsView
from ui.help_view import HelpDialog
from utils.theme import theme
from utils.version import APP_VERSION

NAV_ITEMS = [
    ("👥", "従業員管理",    0),
    ("📝", "希望シフト入力", 1),
    ("⚡", "シフト自動生成", 2),
    ("📅", "シフト表示・編集", 3),
    ("⚙️", "設定",          4),
]

SIDEBAR_W = 160


# ── アップデートダイアログ ────────────────────────────────────────────────

class _UpdateDialog(QDialog):
    def __init__(self, tag: str, url: str, parent=None):
        super().__init__(parent)
        self._url  = url
        self._tag  = tag
        self.setWindowTitle("アップデートのお知らせ")
        self.setFixedWidth(480)
        self._build_ui()

    def _build_ui(self):
        import sys
        layout = QVBoxLayout(self)
        layout.setSpacing(12)

        title = QLabel(f"🆕  新しいバージョン {self._tag} が利用可能です")
        title.setFont(QFont("", 12, QFont.Weight.Bold))
        layout.addWidget(title)

        warn = QLabel(
            "⚠️ アップデート前に必ずバックアップを取ってください。\n"
            "「設定」→「バックアップを保存...」からデータをバックアップできます。"
        )
        warn.setWordWrap(True)
        warn.setStyleSheet(
            "background:#fffbeb; border:1px solid #fcd34d; "
            "border-radius:6px; padding:10px;"
        )
        layout.addWidget(warn)

        # 進捗エリア（ダウンロード中のみ表示）
        self._progress_label = QLabel("")
        self._progress_label.setVisible(False)
        layout.addWidget(self._progress_label)

        self._progress_bar = QProgressBar()
        self._progress_bar.setVisible(False)
        self._progress_bar.setRange(0, 100)
        layout.addWidget(self._progress_bar)

        # ボタン行
        btn_row = QHBoxLayout()
        self._btn_later = QPushButton("後で")
        self._btn_later.setFixedHeight(34)
        self._btn_later.clicked.connect(self.reject)

        self._btn_browser = QPushButton("ブラウザで開く")
        self._btn_browser.setFixedHeight(34)
        self._btn_browser.setStyleSheet(
            "QPushButton { border:1px solid #d1d5db; border-radius:5px; padding:0 14px; }"
            " QPushButton:hover { background:#f3f4f6; }"
        )
        self._btn_browser.clicked.connect(self._open_browser)

        btn_row.addWidget(self._btn_later)
        btn_row.addStretch()
        btn_row.addWidget(self._btn_browser)

        # Windows 凍結アプリのみ自動アップデートボタンを表示
        is_frozen_windows = (
            getattr(sys, "frozen", False) and sys.platform == "win32"
        )
        if is_frozen_windows:
            self._btn_auto = QPushButton("⬇ 自動アップデート")
            self._btn_auto.setFixedHeight(34)
            self._btn_auto.setStyleSheet(
                "QPushButton { background:#2563eb; color:white; border-radius:5px; "
                "padding:0 16px; font-weight:bold; }"
                " QPushButton:hover { background:#1d4ed8; }"
            )
            self._btn_auto.clicked.connect(self._start_auto_update)
            btn_row.addWidget(self._btn_auto)

        layout.addLayout(btn_row)

    def _open_browser(self):
        QDesktopServices.openUrl(QUrl(self._url))
        self.accept()

    def _start_auto_update(self):
        """ダウンロードスレッドを起動してプログレスバーを表示する"""
        self._btn_auto.setEnabled(False)
        self._btn_browser.setEnabled(False)
        self._btn_later.setEnabled(False)

        self._progress_label.setText("リリース情報を取得中...")
        self._progress_label.setVisible(True)
        self._progress_bar.setValue(0)
        self._progress_bar.setVisible(True)

        self._dl_thread = _DownloadThread()
        self._dl_thread.progress.connect(self._on_progress)
        self._dl_thread.finished_ok.connect(self._on_download_done)
        self._dl_thread.finished_err.connect(self._on_download_error)
        self._dl_thread.start()

    def _on_progress(self, downloaded: int, total: int, label: str):
        self._progress_label.setText(label)
        if total > 0:
            self._progress_bar.setValue(int(downloaded * 100 / total))
        else:
            self._progress_bar.setRange(0, 0)  # indeterminate

    def _on_download_done(self, new_exe: str):
        self._progress_bar.setValue(100)
        self._progress_bar.setRange(0, 100)
        self._progress_label.setText("✅ ダウンロード完了")

        reply = QMessageBox.question(
            self, "アップデート適用",
            "ダウンロードが完了しました。\n"
            "アプリを終了してアップデートを適用しますか？\n\n"
            "（アプリが自動的に再起動されます）",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            self._btn_later.setEnabled(True)
            self._btn_browser.setEnabled(True)
            return

        from utils.updater import get_current_exe, launch_windows_updater
        old_exe = get_current_exe()
        if not old_exe:
            QMessageBox.critical(self, "エラー", "現在の EXE パスを取得できませんでした。")
            return

        if not launch_windows_updater(old_exe, new_exe):
            QMessageBox.critical(self, "エラー", "アップデーターの起動に失敗しました。\n手動でアップデートしてください。")
            return

        from PyQt6.QtWidgets import QApplication
        QApplication.instance().quit()

    def _on_download_error(self, msg: str):
        self._progress_bar.setVisible(False)
        self._progress_label.setText(f"❌ エラー: {msg}")
        self._btn_later.setEnabled(True)
        self._btn_browser.setEnabled(True)
        self._btn_auto.setEnabled(True)


# ── ダウンロードスレッド ───────────────────────────────────────────────────

class _DownloadThread(QThread):
    progress    = pyqtSignal(int, int, str)   # (downloaded, total, label)
    finished_ok  = pyqtSignal(str)             # new_exe_path
    finished_err = pyqtSignal(str)             # error message

    def run(self):
        import tempfile
        try:
            from utils.updater import fetch_latest_release_info, download_file
            self.progress.emit(0, 0, "リリース情報を取得中...")
            info = fetch_latest_release_info()
            exe_url = info.get("exe_url", "")
            if not exe_url:
                self.finished_err.emit("ダウンロード URL が見つかりませんでした。")
                return

            fd, tmp_path = tempfile.mkstemp(suffix=".exe", prefix="SDU-Shift_new_")
            import os
            os.close(fd)

            def progress_cb(downloaded, total):
                if total:
                    mb_dl = downloaded / 1_048_576
                    mb_tot = total / 1_048_576
                    label = f"ダウンロード中... {mb_dl:.1f} / {mb_tot:.1f} MB"
                else:
                    mb_dl = downloaded / 1_048_576
                    label = f"ダウンロード中... {mb_dl:.1f} MB"
                self.progress.emit(downloaded, total, label)

            download_file(exe_url, tmp_path, progress_cb)
            self.finished_ok.emit(tmp_path)

        except Exception as e:
            self.finished_err.emit(str(e))


# ── アップデートチェッカー（バックグラウンドスレッド）──────────────────────

class _UpdateChecker(QThread):
    result = pyqtSignal(bool, str, str)  # (is_newer, tag, url)

    def run(self):
        from utils.version import check_for_update
        is_newer, tag, url = check_for_update()
        self.result.emit(is_newer, tag, url)


# ── サイドバーボタン ──────────────────────────────────────────────────────

class SidebarButton(QPushButton):
    def __init__(self, icon: str, text: str, parent=None):
        super().__init__(parent)
        self.setText(f" {icon}\n {text}")
        self.setFixedHeight(64)
        self.setFixedWidth(SIDEBAR_W)
        self.setCheckable(True)
        self.setFont(QFont("", 10))
        self._active = False
        self._update_style()

    def _update_style(self):
        c = theme.c
        bg    = c["sidebar_active"] if self._active else "transparent"
        hover = c["sidebar_active"] if self._active else c["sidebar_hover"]
        self.setStyleSheet(f"""
            QPushButton {{
                background: {bg};
                color: {c["sidebar_text"]};
                border: none;
                text-align: left;
                padding-left: 16px;
                border-radius: 0;
            }}
            QPushButton:hover {{ background: {hover}; }}
        """)

    def setActive(self, active: bool):
        self._active = active
        self.setChecked(active)
        self._update_style()

    def apply_theme(self):
        self._update_style()


# ── メインウィンドウ ──────────────────────────────────────────────────────

class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("SDU-Shift")
        self.setMinimumSize(QSize(1100, 700))
        self._build_ui()
        self._nav_to(0)
        theme.changed.connect(self._on_theme_changed)
        self._start_update_check()

    def _build_ui(self):
        central = QWidget()
        self.setCentralWidget(central)
        root = QHBoxLayout(central)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # ── サイドバー ───────────────────────────────────────────────────
        self._sidebar = QWidget()
        self._sidebar.setFixedWidth(SIDEBAR_W)
        sb_layout = QVBoxLayout(self._sidebar)
        sb_layout.setContentsMargins(0, 0, 0, 0)
        sb_layout.setSpacing(0)

        self._title_label = QLabel("Shift Tool")
        self._title_label.setFixedHeight(56)
        self._title_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._title_label.setFont(QFont("", 13, QFont.Weight.Bold))
        sb_layout.addWidget(self._title_label)

        self._nav_buttons: list[SidebarButton] = []
        for icon, text, idx in NAV_ITEMS:
            btn = SidebarButton(icon, text)
            btn.clicked.connect(lambda _, i=idx: self._nav_to(i))
            self._nav_buttons.append(btn)
            sb_layout.addWidget(btn)

        sb_layout.addStretch()

        # ヘルプボタン
        self._btn_help = QPushButton(" ❓\n ヘルプ")
        self._btn_help.setFixedHeight(56)
        self._btn_help.setFixedWidth(SIDEBAR_W)
        self._btn_help.setFont(QFont("", 10))
        self._btn_help.clicked.connect(self._on_help)
        sb_layout.addWidget(self._btn_help)

        # アップデート通知ラベル（初期非表示）
        self._update_label = QLabel("")
        self._update_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._update_label.setWordWrap(True)
        self._update_label.setVisible(False)
        self._update_label.setCursor(Qt.CursorShape.PointingHandCursor)
        self._update_label.mousePressEvent = self._on_update_label_clicked
        sb_layout.addWidget(self._update_label)

        self._ver_label = QLabel(APP_VERSION)
        self._ver_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        sb_layout.addWidget(self._ver_label)

        root.addWidget(self._sidebar)

        # 区切り線
        self._sep = QFrame()
        self._sep.setFrameShape(QFrame.Shape.VLine)
        root.addWidget(self._sep)

        # ── コンテンツエリア ─────────────────────────────────────────────
        self.stack = QStackedWidget()

        self._employee_view   = EmployeeView()
        self._shift_input_view = ShiftInputView()
        self._generate_view   = GenerateView()
        self._schedule_view   = ScheduleView()
        self._settings_view   = SettingsView()

        self.stack.addWidget(self._employee_view)
        self.stack.addWidget(self._shift_input_view)
        self.stack.addWidget(self._generate_view)
        self.stack.addWidget(self._schedule_view)
        self.stack.addWidget(self._settings_view)

        self._generate_view.schedule_generated.connect(self._on_schedule_generated)
        self._settings_view.db_imported.connect(self._on_db_imported)
        root.addWidget(self.stack)

        self._apply_sidebar_style()

    def _apply_sidebar_style(self):
        c = theme.c
        self._sidebar.setStyleSheet(f"background: {c['sidebar_bg']};")
        self._title_label.setStyleSheet(
            f"color: white; background: {c['sidebar_bg']}; "
            f"border-bottom: 1px solid {c['sidebar_border']};"
        )
        self._ver_label.setStyleSheet(
            f"color: {c['text2']}; font-size: 10px; padding: 8px; "
            f"background: {c['sidebar_bg']};"
        )
        self._btn_help.setStyleSheet(f"""
            QPushButton {{
                background: transparent;
                color: {c["sidebar_text"]};
                border: none;
                text-align: left;
                padding-left: 16px;
                border-radius: 0;
            }}
            QPushButton:hover {{ background: {c["sidebar_hover"]}; }}
        """)
        self._sep.setStyleSheet(f"color: {c['border']};")
        self.stack.setStyleSheet(f"background: {c['bg']};")

    def _on_theme_changed(self):
        self._apply_sidebar_style()
        for btn in self._nav_buttons:
            btn.apply_theme()
        for view in (self._employee_view, self._shift_input_view,
                     self._generate_view, self._schedule_view, self._settings_view):
            if hasattr(view, "apply_theme"):
                view.apply_theme()

    def _nav_to(self, idx: int):
        self.stack.setCurrentIndex(idx)
        for i, btn in enumerate(self._nav_buttons):
            btn.setActive(i == idx)
        if idx == 2:
            self._generate_view.refresh()
        elif idx == 3:
            self._schedule_view.refresh()

    def _on_schedule_generated(self, period_id: int):
        self._schedule_view.refresh(period_id)
        self._nav_to(3)

    def _on_db_imported(self):
        self._employee_view.refresh()
        self._shift_input_view._load_periods()
        self._generate_view.refresh()
        self._schedule_view.refresh()

    def _on_help(self):
        dlg = HelpDialog(parent=self)
        dlg.exec()

    # ── アップデートチェック ─────────────────────────────────────────────

    def _start_update_check(self):
        self._updater = _UpdateChecker()
        self._updater.result.connect(self._on_update_result)
        self._updater.start()

    def _on_update_result(self, is_newer: bool, tag: str, url: str):
        self._update_url = url
        self._update_tag = tag
        if not is_newer:
            return

        # サイドバーに常駐バナーを表示
        self._update_label.setText(f"🆕 {tag}\n利用可能\nクリックで開く")
        self._update_label.setVisible(True)
        self._update_label.setStyleSheet(
            "background:#1d4ed8; color:white; font-size:10px; "
            "padding:6px 4px; border-radius:4px; margin:4px;"
        )

        # 初回通知ダイアログを表示
        dlg = _UpdateDialog(tag, url, parent=self)
        dlg.exec()

    def _on_update_label_clicked(self, event):
        url = getattr(self, "_update_url", "")
        if url:
            tag = getattr(self, "_update_tag", "")
            dlg = _UpdateDialog(tag, url, parent=self)
            dlg.exec()
