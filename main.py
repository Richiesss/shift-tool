"""エントリーポイント"""
import sys
import os

# パッケージルートをsys.pathに追加
sys.path.insert(0, os.path.dirname(__file__))

from PyQt6.QtWidgets import QApplication
from PyQt6.QtCore import Qt
from db.database import initialize_db
from ui.main_window import MainWindow


def main():
    initialize_db()

    app = QApplication(sys.argv)
    app.setApplicationName("シフト表構築ツール")
    app.setStyle("Fusion")

    # グローバルスタイルシート
    app.setStyleSheet("""
        QWidget {
            font-family: "Meiryo", "Hiragino Sans", "Yu Gothic", "MS Gothic", sans-serif;
            font-size: 10pt;
        }
        QGroupBox {
            font-weight: bold;
            border: 1px solid #d1d5db;
            border-radius: 6px;
            margin-top: 8px;
            padding-top: 8px;
        }
        QGroupBox::title {
            subcontrol-origin: margin;
            left: 10px;
            padding: 0 4px;
            color: #374151;
        }
        QTableWidget {
            border: 1px solid #e5e7eb;
            border-radius: 6px;
            gridline-color: #f3f4f6;
        }
        QTableWidget::item:selected {
            background: #dbeafe;
            color: #1e3a5f;
        }
        QHeaderView::section {
            background: #f8fafc;
            border: none;
            border-bottom: 1px solid #e2e8f0;
            padding: 6px;
            font-weight: bold;
            color: #475569;
        }
        QComboBox {
            border: 1px solid #d1d5db;
            border-radius: 5px;
            padding: 4px 8px;
        }
        QLineEdit {
            border: 1px solid #d1d5db;
            border-radius: 5px;
            padding: 4px 8px;
        }
        QScrollBar:vertical {
            border: none;
            background: #f1f5f9;
            width: 8px;
        }
        QScrollBar::handle:vertical {
            background: #cbd5e1;
            border-radius: 4px;
        }
    """)

    window = MainWindow()
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
