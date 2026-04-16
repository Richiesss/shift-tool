"""テーマ管理 — Light / Dark モード"""
from __future__ import annotations
from PyQt6.QtGui import QPalette, QColor
from PyQt6.QtWidgets import QApplication
from PyQt6.QtCore import QObject, pyqtSignal

# ── カラーパレット定義 ────────────────────────────────────────────────────

LIGHT: dict[str, str] = {
    # 背景
    "bg":               "#ffffff",
    "surface":          "#f8fafc",
    "surface2":         "#f1f5f9",
    # 罫線
    "border":           "#e5e7eb",
    "border2":          "#d1d5db",
    # テキスト
    "text":             "#1e293b",
    "text2":            "#6b7280",
    "text3":            "#475569",
    # アクション
    "primary":          "#2563eb",
    "primary_hover":    "#1d4ed8",
    "primary_dis":      "#93c5fd",
    "success":          "#16a34a",
    "success_hover":    "#15803d",
    "success_dis":      "#86efac",
    "danger_bg":        "#fee2e2",
    "danger_border":    "#fca5a5",
    "danger_text":      "#b91c1c",
    "purple":           "#7c3aed",
    "purple_hover":     "#6d28d9",
    # テーブル選択
    "selected_bg":      "#dbeafe",
    "selected_text":    "#1e3a5f",
    # シフト種別セル色
    "cell_breakfast":   "#dbeafe",
    "cell_dinner":      "#fce7f3",
    "cell_double":      "#d1fae5",
    "cell_none":        "#f9fafb",
    # 集計ステータス
    "status_ok":        "#d1fae5",
    "status_warn":      "#fef3c7",
    "status_err":       "#fee2e2",
    # 曜日
    "sat_text":         "#2563eb",
    "sun_text":         "#dc2626",
    "sat_cell":         "#dbeafe",
    "sun_cell":         "#fce7f3",
    "day_header":       "#eff6ff",
    # スケジュールビュー
    "cell_assigned":    "#bfdbfe",
    "cell_available":   "#f0fdf4",
    "cell_unavail":     "#f9fafb",
    "cell_avail_text":  "#6b7280",
    # スクロールバー
    "scroll_bg":        "#f1f5f9",
    "scroll_handle":    "#cbd5e1",
    # サイドバー（ダークのまま固定）
    "sidebar_bg":       "#1e293b",
    "sidebar_active":   "#2563eb",
    "sidebar_hover":    "#334155",
    "sidebar_text":     "#e2e8f0",
    "sidebar_border":   "#334155",
}

DARK: dict[str, str] = {
    # 背景
    "bg":               "#0f172a",
    "surface":          "#1e293b",
    "surface2":         "#273549",
    # 罫線
    "border":           "#334155",
    "border2":          "#475569",
    # テキスト
    "text":             "#f1f5f9",
    "text2":            "#94a3b8",
    "text3":            "#cbd5e1",
    # アクション
    "primary":          "#3b82f6",
    "primary_hover":    "#2563eb",
    "primary_dis":      "#1e40af",
    "success":          "#22c55e",
    "success_hover":    "#16a34a",
    "success_dis":      "#14532d",
    "danger_bg":        "#3f1515",
    "danger_border":    "#7f1d1d",
    "danger_text":      "#f87171",
    "purple":           "#8b5cf6",
    "purple_hover":     "#7c3aed",
    # テーブル選択
    "selected_bg":      "#1e3a5f",
    "selected_text":    "#93c5fd",
    # シフト種別セル色
    "cell_breakfast":   "#1e3a5f",
    "cell_dinner":      "#3b1535",
    "cell_double":      "#14352a",
    "cell_none":        "#1e293b",
    # 集計ステータス
    "status_ok":        "#14532d",
    "status_warn":      "#451a03",
    "status_err":       "#3f1515",
    # 曜日
    "sat_text":         "#60a5fa",
    "sun_text":         "#f87171",
    "sat_cell":         "#1e3a5f",
    "sun_cell":         "#3b1535",
    "day_header":       "#1e293b",
    # スケジュールビュー
    "cell_assigned":    "#1e4080",
    "cell_available":   "#14352a",
    "cell_unavail":     "#1e293b",
    "cell_avail_text":  "#64748b",
    # スクロールバー
    "scroll_bg":        "#1e293b",
    "scroll_handle":    "#475569",
    # サイドバー
    "sidebar_bg":       "#020617",
    "sidebar_active":   "#2563eb",
    "sidebar_hover":    "#1e293b",
    "sidebar_text":     "#e2e8f0",
    "sidebar_border":   "#1e293b",
}


# ── ThemeManager ─────────────────────────────────────────────────────────

class ThemeManager(QObject):
    changed = pyqtSignal()

    def __init__(self):
        super().__init__()
        self._dark = False

    @property
    def is_dark(self) -> bool:
        return self._dark

    @property
    def c(self) -> dict[str, str]:
        """現在のカラーパレット辞書"""
        return DARK if self._dark else LIGHT

    def detect_system(self) -> bool:
        """OS のカラースキームを検出して返す（True=ダーク）"""
        try:
            from PyQt6.QtGui import QGuiApplication
            from PyQt6.QtCore import Qt
            scheme = QGuiApplication.styleHints().colorScheme()
            return scheme == Qt.ColorScheme.Dark
        except Exception:
            palette = QApplication.palette()
            return palette.color(QPalette.ColorRole.Window).lightness() < 128

    def apply(self, dark: bool | None = None):
        """テーマを適用する。dark=None なら OS 設定を自動検出。"""
        if dark is None:
            dark = self.detect_system()
        self._dark = dark
        app = QApplication.instance()
        if app is None:
            return
        app.setPalette(self._make_palette())
        app.setStyleSheet(self._global_stylesheet())
        self.changed.emit()

    def _make_palette(self) -> QPalette:
        p = QPalette()
        c = self.c
        if self._dark:
            p.setColor(QPalette.ColorRole.Window,          QColor(c["surface"]))
            p.setColor(QPalette.ColorRole.WindowText,      QColor(c["text"]))
            p.setColor(QPalette.ColorRole.Base,            QColor(c["bg"]))
            p.setColor(QPalette.ColorRole.AlternateBase,   QColor(c["surface2"]))
            p.setColor(QPalette.ColorRole.Text,            QColor(c["text"]))
            p.setColor(QPalette.ColorRole.BrightText,      QColor("#ffffff"))
            p.setColor(QPalette.ColorRole.Button,          QColor(c["surface2"]))
            p.setColor(QPalette.ColorRole.ButtonText,      QColor(c["text"]))
            p.setColor(QPalette.ColorRole.ToolTipBase,     QColor(c["surface2"]))
            p.setColor(QPalette.ColorRole.ToolTipText,     QColor(c["text"]))
            p.setColor(QPalette.ColorRole.Link,            QColor(c["primary"]))
            p.setColor(QPalette.ColorRole.Highlight,       QColor(c["primary"]))
            p.setColor(QPalette.ColorRole.HighlightedText, QColor("#ffffff"))
            p.setColor(QPalette.ColorGroup.Disabled,
                       QPalette.ColorRole.Text,       QColor(c["text2"]))
            p.setColor(QPalette.ColorGroup.Disabled,
                       QPalette.ColorRole.ButtonText, QColor(c["text2"]))
        return p

    def _global_stylesheet(self) -> str:
        c = self.c
        return f"""
        QWidget {{
            font-family: "Meiryo", "Hiragino Sans", "Yu Gothic", "MS Gothic", sans-serif;
            font-size: 10pt;
        }}
        QGroupBox {{
            font-weight: bold;
            border: 1px solid {c["border"]};
            border-radius: 6px;
            margin-top: 8px;
            padding-top: 8px;
            color: {c["text"]};
        }}
        QGroupBox::title {{
            subcontrol-origin: margin;
            left: 10px;
            padding: 0 4px;
            color: {c["text3"]};
        }}
        QTableWidget {{
            border: 1px solid {c["border"]};
            border-radius: 6px;
            gridline-color: {c["border"]};
            background: {c["bg"]};
            color: {c["text"]};
        }}
        QTableWidget::item:selected {{
            background: {c["selected_bg"]};
            color: {c["selected_text"]};
        }}
        QHeaderView::section {{
            background: {c["surface"]};
            border: none;
            border-bottom: 1px solid {c["border"]};
            padding: 6px;
            font-weight: bold;
            color: {c["text3"]};
        }}
        QComboBox {{
            border: 1px solid {c["border2"]};
            border-radius: 5px;
            padding: 4px 8px;
            background: {c["bg"]};
            color: {c["text"]};
        }}
        QComboBox QAbstractItemView {{
            background: {c["surface"]};
            color: {c["text"]};
            selection-background-color: {c["selected_bg"]};
        }}
        QLineEdit {{
            border: 1px solid {c["border2"]};
            border-radius: 5px;
            padding: 4px 8px;
            background: {c["bg"]};
            color: {c["text"]};
        }}
        QScrollBar:vertical {{
            border: none;
            background: {c["scroll_bg"]};
            width: 8px;
        }}
        QScrollBar::handle:vertical {{
            background: {c["scroll_handle"]};
            border-radius: 4px;
        }}
        QScrollBar:horizontal {{
            border: none;
            background: {c["scroll_bg"]};
            height: 8px;
        }}
        QScrollBar::handle:horizontal {{
            background: {c["scroll_handle"]};
            border-radius: 4px;
        }}
        QLabel {{ color: {c["text"]}; }}
        QDialog {{ background: {c["surface"]}; }}
        QMessageBox {{ background: {c["surface"]}; }}
        QScrollArea {{ background: {c["bg"]}; border: none; }}
        """


# グローバルシングルトン
theme = ThemeManager()
