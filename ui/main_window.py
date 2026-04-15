"""メインウィンドウ（サイドバー + コンテンツエリア）"""
from __future__ import annotations
from PyQt6.QtWidgets import (
    QMainWindow, QWidget, QHBoxLayout, QVBoxLayout,
    QPushButton, QLabel, QStackedWidget, QFrame, QSizePolicy
)
from PyQt6.QtCore import Qt, QSize
from PyQt6.QtGui import QFont, QColor, QPalette

from ui.employee_view import EmployeeView
from ui.shift_input_view import ShiftInputView
from ui.generate_view import GenerateView
from ui.schedule_view import ScheduleView

NAV_ITEMS = [
    ("👥", "従業員管理", 0),
    ("📝", "希望シフト入力", 1),
    ("⚡", "シフト自動生成", 2),
    ("📅", "シフト表示・編集", 3),
]

SIDEBAR_W = 160
SIDEBAR_BG = "#1e293b"
SIDEBAR_ACTIVE = "#2563eb"
SIDEBAR_HOVER = "#334155"
SIDEBAR_TEXT = "#e2e8f0"


class SidebarButton(QPushButton):
    def __init__(self, icon: str, text: str, parent=None):
        super().__init__(parent)
        self.setText(f" {icon}\n {text}")
        self.setFixedHeight(64)
        self.setFixedWidth(SIDEBAR_W)
        self.setCheckable(True)
        self.setFont(QFont("", 10))
        self._update_style(False)

    def _update_style(self, active: bool):
        bg = SIDEBAR_ACTIVE if active else "transparent"
        self.setStyleSheet(f"""
            QPushButton {{
                background: {bg};
                color: {SIDEBAR_TEXT};
                border: none;
                text-align: left;
                padding-left: 16px;
                border-radius: 0;
            }}
            QPushButton:hover {{
                background: {SIDEBAR_HOVER if not active else SIDEBAR_ACTIVE};
            }}
        """)

    def setActive(self, active: bool):
        self.setChecked(active)
        self._update_style(active)


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("シフト表構築ツール")
        self.setMinimumSize(QSize(1100, 700))
        self._build_ui()
        self._nav_to(0)

    def _build_ui(self):
        central = QWidget()
        self.setCentralWidget(central)
        root = QHBoxLayout(central)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # サイドバー
        sidebar = QWidget()
        sidebar.setFixedWidth(SIDEBAR_W)
        sidebar.setStyleSheet(f"background: {SIDEBAR_BG};")
        sb_layout = QVBoxLayout(sidebar)
        sb_layout.setContentsMargins(0, 0, 0, 0)
        sb_layout.setSpacing(0)

        # アプリタイトル
        title_label = QLabel("Shift Tool")
        title_label.setFixedHeight(56)
        title_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        title_label.setFont(QFont("", 13, QFont.Weight.Bold))
        title_label.setStyleSheet(f"color: white; background: {SIDEBAR_BG}; border-bottom: 1px solid #334155;")
        sb_layout.addWidget(title_label)

        self._nav_buttons: list[SidebarButton] = []
        for icon, text, idx in NAV_ITEMS:
            btn = SidebarButton(icon, text)
            btn.clicked.connect(lambda _, i=idx: self._nav_to(i))
            self._nav_buttons.append(btn)
            sb_layout.addWidget(btn)

        sb_layout.addStretch()

        # バージョン
        ver = QLabel("v1.0.0")
        ver.setAlignment(Qt.AlignmentFlag.AlignCenter)
        ver.setStyleSheet(f"color: #64748b; font-size: 10px; padding: 8px;")
        sb_layout.addWidget(ver)

        root.addWidget(sidebar)

        # 区切り線
        sep = QFrame()
        sep.setFrameShape(QFrame.Shape.VLine)
        sep.setStyleSheet("color: #e2e8f0;")
        root.addWidget(sep)

        # コンテンツエリア
        self.stack = QStackedWidget()
        self.stack.setStyleSheet("background: white;")

        self._employee_view = EmployeeView()
        self._shift_input_view = ShiftInputView()
        self._generate_view = GenerateView()
        self._schedule_view = ScheduleView()

        self.stack.addWidget(self._employee_view)       # 0
        self.stack.addWidget(self._shift_input_view)    # 1
        self.stack.addWidget(self._generate_view)       # 2
        self.stack.addWidget(self._schedule_view)        # 3

        # 生成完了 → シフト編集画面に自動遷移
        self._generate_view.schedule_generated.connect(self._on_schedule_generated)

        root.addWidget(self.stack)

    def _nav_to(self, idx: int):
        self.stack.setCurrentIndex(idx)
        for i, btn in enumerate(self._nav_buttons):
            btn.setActive(i == idx)

        # 画面切替時にデータをリフレッシュ
        if idx == 2:
            self._generate_view.refresh()
        elif idx == 3:
            self._schedule_view.refresh()

    def _on_schedule_generated(self, period_id: int):
        self._schedule_view.refresh(period_id)
        self._nav_to(3)
