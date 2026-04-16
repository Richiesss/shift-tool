"""メインウィンドウ（サイドバー + コンテンツエリア）"""
from __future__ import annotations
from PyQt6.QtWidgets import (
    QMainWindow, QWidget, QHBoxLayout, QVBoxLayout,
    QPushButton, QLabel, QStackedWidget, QFrame, QSizePolicy,
    QDialog
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
        self._url = url
        self.setWindowTitle("アップデートのお知らせ")
        self.setFixedWidth(460)

        layout = QVBoxLayout(self)
        layout.setSpacing(12)

        # タイトル
        title = QLabel(f"🆕  新しいバージョン {tag} が利用可能です")
        from PyQt6.QtGui import QFont
        title.setFont(QFont("", 12, QFont.Weight.Bold))
        layout.addWidget(title)

        # 手順説明
        steps = QLabel(
            "ダウンロード・インストール前に以下の手順を必ず守ってください。\n\n"
            "　① 「設定」画面の「バックアップ」ボタンでデータを保存する\n"
            "　② 旧バージョンの EXE ファイルを削除する\n"
            "　③ 新しい EXE をダウンロードして起動する\n\n"
            "※ バックアップを取らずに更新すると、まれにデータが引き継げない場合があります。"
        )
        steps.setWordWrap(True)
        steps.setStyleSheet(
            "background:#fffbeb; border:1px solid #fcd34d; "
            "border-radius:6px; padding:10px; line-height:1.6;"
        )
        layout.addWidget(steps)

        # ボタン行
        btn_row = QHBoxLayout()
        btn_later = QPushButton("後で")
        btn_later.setFixedHeight(34)
        btn_later.clicked.connect(self.reject)

        btn_open = QPushButton("ダウンロードページを開く →")
        btn_open.setFixedHeight(34)
        btn_open.setStyleSheet(
            "QPushButton { background:#2563eb; color:white; border-radius:5px; "
            "padding:0 16px; font-weight:bold; }"
            " QPushButton:hover { background:#1d4ed8; }"
        )
        btn_open.clicked.connect(self._open_and_close)

        btn_row.addWidget(btn_later)
        btn_row.addStretch()
        btn_row.addWidget(btn_open)
        layout.addLayout(btn_row)

    def _open_and_close(self):
        from PyQt6.QtGui import QDesktopServices
        from PyQt6.QtCore import QUrl
        QDesktopServices.openUrl(QUrl(self._url))
        self.accept()


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
