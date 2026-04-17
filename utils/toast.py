"""トースト通知ウィジェット（item 5）"""
from __future__ import annotations
from PyQt6.QtWidgets import QWidget, QLabel, QHBoxLayout
from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtGui import QFont


class Toast(QWidget):
    """メインウィンドウ右下に表示される自動消滅通知。"""

    _COLORS = {
        "success": ("#15803d", "#f0fdf4", "#86efac"),
        "info":    ("#1d4ed8", "#eff6ff", "#93c5fd"),
        "warn":    ("#b45309", "#fffbeb", "#fcd34d"),
        "error":   ("#dc2626", "#fef2f2", "#fca5a5"),
    }

    def __init__(self, parent: QWidget):
        super().__init__(parent, Qt.WindowType.SubWindow)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.setFixedWidth(300)

        self._label = QLabel()
        self._label.setWordWrap(True)
        self._label.setFont(QFont("", 10))

        lay = QHBoxLayout(self)
        lay.setContentsMargins(14, 10, 14, 10)
        lay.addWidget(self._label)

        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self.hide)

        self.hide()

    def show_message(self, msg: str, level: str = "success"):
        text_c, bg_c, border_c = self._COLORS.get(level, self._COLORS["info"])
        self._label.setText(msg)
        self.setStyleSheet(
            f"background:{bg_c}; border:1.5px solid {border_c}; border-radius:8px;"
        )
        self._label.setStyleSheet(f"color:{text_c}; font-weight:bold;")
        self.adjustSize()
        self._reposition()
        self.show()
        self.raise_()
        self._timer.start(2500)

    def _reposition(self):
        p = self.parent()
        if p:
            self.move(p.width() - self.width() - 20, p.height() - self.height() - 48)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._reposition()


def show_toast(msg: str, level: str = "success"):
    """どこからでも呼べるトースト表示ヘルパー。"""
    from PyQt6.QtWidgets import QApplication
    app = QApplication.instance()
    if app is None:
        return
    for w in app.topLevelWidgets():
        if hasattr(w, "_toast"):
            w._toast.show_message(msg, level)
            return
