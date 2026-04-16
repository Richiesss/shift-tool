"""設定画面（バックアップ・インポート）"""
from __future__ import annotations
import shutil
from datetime import datetime
from pathlib import Path
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QPushButton, QLabel,
    QGroupBox, QFileDialog, QMessageBox
)
from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QFont
from db.database import DB_PATH
from utils.theme import theme


class SettingsView(QWidget):
    # DBが入れ替わったときに発火 → MainWindow が全ビューをリフレッシュ
    db_imported = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._build_ui()

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(16)
        layout.setAlignment(Qt.AlignmentFlag.AlignTop)

        title = QLabel("設定")
        title.setFont(QFont("", 16, QFont.Weight.Bold))
        layout.addWidget(title)

        # ── DB パス表示 ───────────────────────────────────────────────
        path_group = QGroupBox("データベースの保存場所")
        path_layout = QVBoxLayout(path_group)
        self._path_label = QLabel(str(DB_PATH))
        self._path_label.setWordWrap(True)
        self._path_label.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse)
        path_layout.addWidget(self._path_label)
        layout.addWidget(path_group)

        # ── バックアップ ──────────────────────────────────────────────
        backup_group = QGroupBox("バックアップ")
        backup_layout = QVBoxLayout(backup_group)
        backup_layout.setSpacing(8)

        note_b = QLabel("現在のデータをファイルに保存します。\n"
                         "バージョンアップ前に実行しておくと安心です。")
        note_b.setWordWrap(True)
        backup_layout.addWidget(note_b)

        self._btn_backup = QPushButton("バックアップを保存...")
        self._btn_backup.setFixedHeight(36)
        self._btn_backup.clicked.connect(self._on_backup)
        backup_layout.addWidget(self._btn_backup)
        layout.addWidget(backup_group)

        # ── インポート（復元）────────────────────────────────────────
        import_group = QGroupBox("インポート（復元）")
        import_layout = QVBoxLayout(import_group)
        import_layout.setSpacing(8)

        note_i = QLabel("バックアップファイルからデータを復元します。\n"
                         "⚠️ 現在のデータはすべて上書きされます。")
        note_i.setWordWrap(True)
        import_layout.addWidget(note_i)

        self._btn_import = QPushButton("バックアップからインポート...")
        self._btn_import.setFixedHeight(36)
        self._btn_import.clicked.connect(self._on_import)
        import_layout.addWidget(self._btn_import)
        layout.addWidget(import_group)

        self._apply_styles()

    def _apply_styles(self):
        c = theme.c
        self._btn_backup.setStyleSheet(
            f"QPushButton {{ background:{c['primary']}; color:white; border-radius:6px; padding:0 16px; font-weight:bold; }}"
            f" QPushButton:hover {{ background:{c['primary_hover']}; }}"
        )
        self._btn_import.setStyleSheet(
            f"QPushButton {{ background:{c['surface']}; border:1px solid {c['border2']}; "
            f"border-radius:6px; padding:0 16px; color:{c['text']}; }}"
            f" QPushButton:hover {{ background:{c['surface2']}; }}"
        )
        self._path_label.setStyleSheet(
            f"color:{c['text2']}; font-size:11px; font-family:monospace;"
        )

    def apply_theme(self):
        self._apply_styles()

    # ── バックアップ ──────────────────────────────────────────────────

    def _on_backup(self):
        default_name = f"shift_tool_backup_{datetime.now().strftime('%Y%m%d_%H%M%S')}.db"
        import os
        default_dir = os.path.expanduser("~/Desktop")
        path, _ = QFileDialog.getSaveFileName(
            self, "バックアップ先を選択",
            str(Path(default_dir) / default_name),
            "SQLite Database (*.db)"
        )
        if not path:
            return
        if not path.endswith(".db"):
            path += ".db"
        try:
            shutil.copy2(str(DB_PATH), path)
            QMessageBox.information(
                self, "バックアップ完了",
                f"バックアップを保存しました:\n{path}"
            )
        except Exception as e:
            QMessageBox.critical(self, "エラー", f"バックアップに失敗しました:\n{e}")

    # ── インポート ────────────────────────────────────────────────────

    def _on_import(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "バックアップファイルを選択",
            str(Path.home() / "Desktop"),
            "SQLite Database (*.db)"
        )
        if not path:
            return

        reply = QMessageBox.warning(
            self, "インポート確認",
            f"以下のファイルからインポートします:\n{path}\n\n"
            "⚠️ 現在のデータはすべて上書きされます。続けますか？",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No
        )
        if reply != QMessageBox.StandardButton.Yes:
            return

        try:
            DB_PATH.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, str(DB_PATH))
            # スキーマを最新化（新カラムがあれば自動追加）
            from db.database import initialize_db
            initialize_db()
            QMessageBox.information(
                self, "インポート完了",
                "データを復元しました。画面の内容を更新します。"
            )
            self.db_imported.emit()
        except Exception as e:
            QMessageBox.critical(self, "エラー", f"インポートに失敗しました:\n{e}")
