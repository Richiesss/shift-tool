"""メインウィンドウ（サイドバー + コンテンツエリア）"""
from __future__ import annotations
from PyQt6.QtWidgets import (
    QMainWindow, QWidget, QHBoxLayout, QVBoxLayout,
    QPushButton, QLabel, QStackedWidget, QFrame, QSizePolicy
)
from PyQt6.QtCore import Qt, QSize
from PyQt6.QtGui import QFont

from ui.employee_view import EmployeeView
from ui.shift_input_view import ShiftInputView
from ui.generate_view import GenerateView
from ui.schedule_view import ScheduleView
from ui.settings_view import SettingsView
from utils.theme import theme

NAV_ITEMS = [
    ("👥", "従業員管理", 0),
    ("📝", "希望シフト入力", 1),
    ("⚡", "シフト自動生成", 2),
    ("📅", "シフト表示・編集", 3),
    ("⚙️", "設定", 4),
]

SIDEBAR_W = 160


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
        bg = c["sidebar_active"] if self._active else "transparent"
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


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("SDU-Shift")
        self.setMinimumSize(QSize(1100, 700))
        self._build_ui()
        self._nav_to(0)
        theme.changed.connect(self._on_theme_changed)

    def _build_ui(self):
        central = QWidget()
        self.setCentralWidget(central)
        root = QHBoxLayout(central)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # サイドバー
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

        self._ver_label = QLabel("v1.0.0")
        self._ver_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        sb_layout.addWidget(self._ver_label)

        root.addWidget(self._sidebar)

        # 区切り線
        self._sep = QFrame()
        self._sep.setFrameShape(QFrame.Shape.VLine)
        root.addWidget(self._sep)

        # コンテンツエリア
        self.stack = QStackedWidget()

        self._employee_view = EmployeeView()
        self._shift_input_view = ShiftInputView()
        self._generate_view = GenerateView()
        self._schedule_view = ScheduleView()
        self._settings_view = SettingsView()

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
        self._sep.setStyleSheet(f"color: {c['border']};")
        self.stack.setStyleSheet(f"background: {c['bg']};")

    def _on_theme_changed(self):
        self._apply_sidebar_style()
        for btn in self._nav_buttons:
            btn.apply_theme()
        # 各ビューのテーマ更新
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
        """DB入れ替え後、全ビューのデータを再読み込み"""
        self._employee_view.refresh()
        self._shift_input_view._load_periods()
        self._generate_view.refresh()
        self._schedule_view.refresh()
