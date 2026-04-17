"""設定画面（バックアップ・インポート）"""
from __future__ import annotations
import shutil
from datetime import datetime
from pathlib import Path
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QPushButton, QLabel,
    QGroupBox, QFileDialog, QMessageBox, QFormLayout, QSpinBox,
    QScrollArea
)
from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QFont
from db.database import DB_PATH
from utils.theme import theme
from utils.constants import TimeSlot, Position


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
        self._btn_backup.setToolTip("現在のDBファイルを任意の場所にコピーして保存します")
        self._btn_backup.clicked.connect(self._on_backup)
        backup_layout.addWidget(self._btn_backup)
        layout.addWidget(backup_group)

        # ── シフト人員制約 ────────────────────────────────────────────
        constraint_group = QGroupBox("シフト人員設定")
        constraint_layout = QVBoxLayout(constraint_group)
        constraint_layout.setSpacing(8)

        note_c = QLabel("各時間帯・ポジションの最低/最大人数とリーダー最低人数を設定します。")
        note_c.setWordWrap(True)
        constraint_layout.addWidget(note_c)

        grid_widget = QWidget()
        grid_layout = QFormLayout(grid_widget)
        grid_layout.setSpacing(6)

        # (TimeSlot, Position) -> (min_spin, max_spin, leader_spin)
        self._constraint_spins: dict[tuple, tuple] = {}

        _LABELS = {
            (TimeSlot.BREAKFAST, Position.HALL):    "朝食 ホール",
            (TimeSlot.BREAKFAST, Position.KITCHEN): "朝食 キッチン",
            (TimeSlot.DINNER,    Position.HALL):    "ディナー ホール",
            (TimeSlot.DINNER,    Position.KITCHEN): "ディナー キッチン",
        }
        for key, label in _LABELS.items():
            row_widget = QWidget()
            row_layout = QHBoxLayout(row_widget)
            row_layout.setContentsMargins(0, 0, 0, 0)
            row_layout.setSpacing(8)

            min_spin = QSpinBox(); min_spin.setRange(0, 20); min_spin.setFixedWidth(60)
            max_spin = QSpinBox(); max_spin.setRange(0, 20); max_spin.setFixedWidth(60)
            ldr_spin = QSpinBox(); ldr_spin.setRange(0, 20); ldr_spin.setFixedWidth(60)
            min_spin.setToolTip(f"{label}: 1日あたりの最低必要人数\nこの人数を下回るとシフト表で警告が表示されます")
            max_spin.setToolTip(f"{label}: 1日あたりの最大配置人数\nこれを超えるとアサインできません")
            ldr_spin.setToolTip(f"{label}: 1日あたりのリーダー最低必要人数\nリーダー不足もシフト表で警告されます")

            row_layout.addWidget(QLabel("最低:"))
            row_layout.addWidget(min_spin)
            row_layout.addWidget(QLabel("最大:"))
            row_layout.addWidget(max_spin)
            row_layout.addWidget(QLabel("リーダー最低:"))
            row_layout.addWidget(ldr_spin)
            row_layout.addStretch()

            grid_layout.addRow(label, row_widget)
            self._constraint_spins[key] = (min_spin, max_spin, ldr_spin)

        constraint_layout.addWidget(grid_widget)

        self._btn_save_constraints = QPushButton("人員設定を保存")
        self._btn_save_constraints.setFixedHeight(36)
        self._btn_save_constraints.setToolTip("シフト人員制約をDBに保存します。次回のシフト生成から反映されます。")
        self._btn_save_constraints.clicked.connect(self._on_save_constraints)
        constraint_layout.addWidget(self._btn_save_constraints)
        layout.addWidget(constraint_group)

        self._load_constraints()

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
        self._btn_import.setToolTip("バックアップファイルで現在のDBを上書き復元します\n⚠️ 現在のデータはすべて失われます")
        self._btn_import.clicked.connect(self._on_import)
        import_layout.addWidget(self._btn_import)
        layout.addWidget(import_group)

        self._apply_styles()

    def _load_constraints(self):
        from db import repositories as repo
        constraints = repo.get_shift_constraints()
        for key, (mn_spin, mx_spin, ldr_spin) in self._constraint_spins.items():
            c = constraints.get(key, {"min": 0, "max": 0, "min_leader": 0})
            mn_spin.setValue(c["min"])
            mx_spin.setValue(c["max"])
            ldr_spin.setValue(c["min_leader"])

    def _on_save_constraints(self):
        from db import repositories as repo
        constraints = {}
        for key, (mn_spin, mx_spin, ldr_spin) in self._constraint_spins.items():
            constraints[key] = {
                "min":        mn_spin.value(),
                "max":        mx_spin.value(),
                "min_leader": ldr_spin.value(),
            }
        repo.save_shift_constraints(constraints)
        QMessageBox.information(self, "保存完了", "シフト人員設定を保存しました。")

    def _apply_styles(self):
        c = theme.c
        self._btn_save_constraints.setStyleSheet(
            f"QPushButton {{ background:{c['surface']}; border:1px solid {c['border2']}; "
            f"border-radius:6px; padding:0 16px; color:{c['text']}; }}"
            f" QPushButton:hover {{ background:{c['surface2']}; }}"
        )
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
