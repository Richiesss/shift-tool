"""設定画面（バックアップ・インポート）"""
from __future__ import annotations
import shutil
from datetime import datetime
from pathlib import Path
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QPushButton, QLabel,
    QGroupBox, QFileDialog, QMessageBox, QFormLayout, QSpinBox,
    QScrollArea, QRadioButton, QButtonGroup, QFrame
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
        # スクロールエリアでラップ（ウィンドウが小さくても潰れない）
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        outer.addWidget(scroll)

        inner = QWidget()
        scroll.setWidget(inner)
        layout = QVBoxLayout(inner)
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

        # ── 外観設定 ──────────────────────────────────────────────────
        appearance_group = QGroupBox("外観")
        appearance_layout = QHBoxLayout(appearance_group)
        appearance_layout.setSpacing(16)

        appearance_layout.addWidget(QLabel("テーマ:"))
        self._theme_group = QButtonGroup(self)
        self._rb_light  = QRadioButton("ライト")
        self._rb_dark   = QRadioButton("ダーク")
        self._rb_system = QRadioButton("システム設定に従う")
        for rb in (self._rb_light, self._rb_dark, self._rb_system):
            self._theme_group.addButton(rb)
            appearance_layout.addWidget(rb)
        appearance_layout.addStretch()

        # 現在のテーマに応じてラジオを初期化
        self._theme_mode = "system"  # "light" / "dark" / "system"
        self._rb_system.setChecked(True)
        self._rb_light.toggled.connect(lambda on: self._on_theme_radio(on, "light"))
        self._rb_dark.toggled.connect(lambda on: self._on_theme_radio(on, "dark"))
        self._rb_system.toggled.connect(lambda on: self._on_theme_radio(on, "system"))
        layout.addWidget(appearance_group)

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

        # ── シフト人員制約（タイムバンド別） ──────────────────────────────
        constraint_group = QGroupBox("シフト人員設定（タイムバンド別）")
        constraint_layout = QVBoxLayout(constraint_group)
        constraint_layout.setSpacing(4)

        note_c = QLabel(
            "各時間帯・ポジションの最低/最大人数とリーダー最低人数を設定します。\n"
            "「開店準備対応可」スタッフは従業員管理で個別に設定してください。"
        )
        note_c.setWordWrap(True)
        constraint_layout.addWidget(note_c)

        # (TimeSlot, Position) -> (min_spin, max_spin, leader_spin)  ← 既存 shift_constraints
        self._constraint_spins: dict[tuple, tuple] = {}
        # (band, position) -> (min_spin, max_spin, leader_spin)      ← 朝食バンド
        self._band_spins: dict[tuple[str, str], tuple] = {}

        def _make_spin_row(form: QFormLayout, row_label: str, key, store: dict, tip_suffix=""):
            w = QWidget()
            hl = QHBoxLayout(w)
            hl.setContentsMargins(0, 0, 0, 0)
            hl.setSpacing(8)
            mn = QSpinBox(); mx = QSpinBox(); ld = QSpinBox()
            for sp in (mn, mx, ld):
                sp.setRange(0, 20); sp.setFixedWidth(60); sp.setMinimumHeight(28)
            mn.setToolTip(f"{row_label}: 最低必要人数{tip_suffix}")
            mx.setToolTip(f"{row_label}: 最大配置人数")
            ld.setToolTip(f"{row_label}: リーダー最低人数")
            hl.addWidget(QLabel("最低:")); hl.addWidget(mn)
            hl.addWidget(QLabel("最大:")); hl.addWidget(mx)
            hl.addWidget(QLabel("リーダー最低:")); hl.addWidget(ld)
            hl.addStretch()
            form.addRow(row_label, w)
            store[key] = (mn, mx, ld)

        # ── 5:45〜6:30 開店準備 ──
        sep0 = QLabel("■ 5:45〜6:30  開店準備")
        sep0.setStyleSheet("font-weight:bold; margin-top:6px;")
        constraint_layout.addWidget(sep0)
        open_tip = "\n※「開店準備対応可」スタッフのみカウントされます"
        open_form_w = QWidget(); open_form = QFormLayout(open_form_w); open_form.setSpacing(4)
        _make_spin_row(open_form, "　ホール",    ("open", "hall"),    self._band_spins, open_tip)
        _make_spin_row(open_form, "　キッチン",  ("open", "kitchen"), self._band_spins, open_tip)
        constraint_layout.addWidget(open_form_w)

        # ── 6:30〜10:00 朝食営業 ──
        sep1 = QLabel("■ 6:30〜10:00  朝食営業")
        sep1.setStyleSheet("font-weight:bold; margin-top:6px;")
        constraint_layout.addWidget(sep1)
        svc_form_w = QWidget(); svc_form = QFormLayout(svc_form_w); svc_form.setSpacing(4)
        _make_spin_row(svc_form, "　ホール",    (TimeSlot.BREAKFAST, Position.HALL),    self._constraint_spins)
        _make_spin_row(svc_form, "　キッチン",  (TimeSlot.BREAKFAST, Position.KITCHEN), self._constraint_spins)
        constraint_layout.addWidget(svc_form_w)

        # ── 10:00〜11:30 片付け ──
        sep2 = QLabel("■ 10:00〜11:30  片付け・レイアウト準備")
        sep2.setStyleSheet("font-weight:bold; margin-top:6px;")
        constraint_layout.addWidget(sep2)
        cln_tip = "\n※ 留まる人数の目安。リーダー最低はリーダー以上が対象です"
        cln_form_w = QWidget(); cln_form = QFormLayout(cln_form_w); cln_form.setSpacing(4)
        _make_spin_row(cln_form, "　ホール",    ("cleanup", "hall"),    self._band_spins, cln_tip)
        _make_spin_row(cln_form, "　キッチン",  ("cleanup", "kitchen"), self._band_spins, cln_tip)
        constraint_layout.addWidget(cln_form_w)

        # ── 17:00〜23:00 ディナー ──
        sep3 = QLabel("■ 17:00〜23:00  ディナー営業")
        sep3.setStyleSheet("font-weight:bold; margin-top:6px;")
        constraint_layout.addWidget(sep3)
        din_form_w = QWidget(); din_form = QFormLayout(din_form_w); din_form.setSpacing(4)
        _make_spin_row(din_form, "　ホール",    (TimeSlot.DINNER, Position.HALL),    self._constraint_spins)
        _make_spin_row(din_form, "　キッチン",  (TimeSlot.DINNER, Position.KITCHEN), self._constraint_spins)
        constraint_layout.addWidget(din_form_w)

        self._btn_save_constraints = QPushButton("人員設定を保存")
        self._btn_save_constraints.setFixedHeight(36)
        self._btn_save_constraints.setToolTip("すべての時間帯・ポジションの人員制約をDBに保存します。")
        self._btn_save_constraints.clicked.connect(self._on_save_constraints)
        constraint_layout.addWidget(self._btn_save_constraints)
        layout.addWidget(constraint_group)

        # ── 予約客数設定 ──────────────────────────────────────────────────
        reserv_group = QGroupBox("予約客数による増員設定")
        reserv_layout = QVBoxLayout(reserv_group)
        reserv_layout.setSpacing(8)
        note_r = QLabel(
            "1日の予約客数が閾値を超えた場合、朝食営業・ディナー営業の最低スタッフ数を増員します。\n"
            "予約客数はシフト表確認・編集画面の各タブで日毎に入力できます。"
        )
        note_r.setWordWrap(True)
        reserv_layout.addWidget(note_r)

        reserv_grid = QWidget()
        reserv_form = QFormLayout(reserv_grid)
        reserv_form.setSpacing(6)

        self._reserv_spins: dict[str, QSpinBox] = {}
        for key, label, default in [
            ("reserv_threshold_breakfast", "朝食: 予約が N 人以上で増員", 100),
            ("reserv_extra_breakfast",     "　　　増員数（人）",           1),
            ("reserv_threshold_dinner",    "ディナー: 予約が N 人以上で増員", 25),
            ("reserv_extra_dinner",        "　　　増員数（人）",            1),
        ]:
            sp = QSpinBox()
            sp.setRange(0, 999)
            sp.setFixedWidth(80)
            sp.setValue(default)
            sp.setMinimumHeight(28)
            self._reserv_spins[key] = sp
            reserv_form.addRow(label, sp)

        reserv_layout.addWidget(reserv_grid)

        btn_save_reserv = QPushButton("予約設定を保存")
        btn_save_reserv.setFixedHeight(36)
        btn_save_reserv.clicked.connect(self._on_save_reserv_settings)
        reserv_layout.addWidget(btn_save_reserv)
        self._btn_save_reserv = btn_save_reserv
        layout.addWidget(reserv_group)

        self._load_constraints()
        self._load_reserv_settings()

        self._apply_styles()

    def _load_constraints(self):
        from db import repositories as repo
        constraints = repo.get_shift_constraints()
        for key, (mn_spin, mx_spin, ldr_spin) in self._constraint_spins.items():
            c = constraints.get(key, {"min": 0, "max": 0, "min_leader": 0})
            mn_spin.setValue(c["min"])
            mx_spin.setValue(c["max"])
            ldr_spin.setValue(c["min_leader"])
        band_constraints = repo.get_breakfast_band_constraints()
        for (band, pos), (mn_spin, mx_spin, ldr_spin) in self._band_spins.items():
            bc = band_constraints.get((band, pos), {"min": 0, "max": 10, "min_leader": 0})
            mn_spin.setValue(bc["min"])
            mx_spin.setValue(bc["max"])
            ldr_spin.setValue(bc["min_leader"])

    def _load_reserv_settings(self):
        from db import repositories as repo
        settings = repo.get_all_app_settings()
        defaults = {
            "reserv_threshold_breakfast": "100",
            "reserv_extra_breakfast":     "1",
            "reserv_threshold_dinner":    "25",
            "reserv_extra_dinner":        "1",
        }
        for key, sp in self._reserv_spins.items():
            sp.setValue(int(settings.get(key, defaults.get(key, "0"))))

    def _on_save_reserv_settings(self):
        from db import repositories as repo
        settings = {key: str(sp.value()) for key, sp in self._reserv_spins.items()}
        repo.save_all_app_settings(settings)
        QMessageBox.information(self, "保存完了", "予約設定を保存しました。")

    def _on_save_constraints(self):
        from db import repositories as repo
        constraints = {}
        for key, (mn_spin, mx_spin, ldr_spin) in self._constraint_spins.items():
            constraints[key] = {
                "min": mn_spin.value(), "max": mx_spin.value(), "min_leader": ldr_spin.value(),
            }
        repo.save_shift_constraints(constraints)
        band_constraints = {}
        for (band, pos), (mn_spin, mx_spin, ldr_spin) in self._band_spins.items():
            band_constraints[(band, pos)] = {
                "min": mn_spin.value(), "max": mx_spin.value(), "min_leader": ldr_spin.value(),
            }
        repo.save_breakfast_band_constraints(band_constraints)
        QMessageBox.information(self, "保存完了", "シフト人員設定を保存しました。")

    def _on_theme_radio(self, checked: bool, mode: str):
        if not checked:
            return
        self._theme_mode = mode
        if mode == "light":
            theme.apply(dark=False)
        elif mode == "dark":
            theme.apply(dark=True)
        else:
            theme.apply(dark=None)  # システム設定を自動検出

    def _apply_styles(self):
        c = theme.c
        self._btn_save_reserv.setStyleSheet(
            f"QPushButton {{ background:{c['surface']}; border:1px solid {c['border2']}; "
            f"border-radius:6px; padding:0 16px; color:{c['text']}; }}"
            f" QPushButton:hover {{ background:{c['surface2']}; }}"
        )
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
