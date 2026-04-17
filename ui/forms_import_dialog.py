"""Google Forms CSV 一括インポートダイアログ"""
from __future__ import annotations
from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QPushButton, QLabel,
    QFileDialog, QTableWidget, QTableWidgetItem, QHeaderView,
    QRadioButton, QGroupBox, QTextEdit, QMessageBox, QWidget,
    QTabWidget, QFrame, QSizePolicy
)
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QFont, QColor, QBrush
from models.schedule import SchedulePeriod
from models.employee import Employee
from utils.theme import theme


class FormsImportDialog(QDialog):
    def __init__(self, period: SchedulePeriod, employees: list[Employee], parent=None):
        super().__init__(parent)
        self._period    = period
        self._employees = employees
        self._csv_path  = ""
        self._result    = None   # ImportResult | None
        self.setWindowTitle("Google Forms CSV 一括インポート")
        self.setMinimumWidth(660)
        self.setMinimumHeight(580)
        self._build_ui()

    # ── UI構築 ────────────────────────────────────────────────────────

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setSpacing(10)

        tabs = QTabWidget()

        # ── タブ1: インポート ────────────────────────────────────────
        import_tab = QWidget()
        il = QVBoxLayout(import_tab)
        il.setSpacing(10)

        # ファイル選択
        file_box = QGroupBox("① CSVファイルを選択")
        fl = QHBoxLayout(file_box)
        self._path_label = QLabel("（未選択）")
        self._path_label.setStyleSheet("color:#6b7280; font-size:11px;")
        self._path_label.setWordWrap(True)
        self._path_label.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        btn_open = QPushButton("ファイルを開く…")
        btn_open.setFixedWidth(130)
        btn_open.setFixedHeight(32)
        btn_open.setToolTip("Google スプレッドシートからダウンロードした CSV を選択します")
        btn_open.clicked.connect(self._on_open_file)
        fl.addWidget(self._path_label)
        fl.addWidget(btn_open)
        il.addWidget(file_box)

        # プレビューテーブル
        prev_box = QGroupBox("② プレビュー（スタッフごとの取込み状況）")
        pl = QVBoxLayout(prev_box)
        self._prev_table = QTableWidget()
        self._prev_table.setColumnCount(4)
        self._prev_table.setHorizontalHeaderLabels(["", "スタッフ名", "件数", "状態"])
        self._prev_table.horizontalHeader().setSectionResizeMode(
            1, QHeaderView.ResizeMode.Stretch)
        self._prev_table.setColumnWidth(0, 28)
        self._prev_table.setColumnWidth(2, 52)
        self._prev_table.setColumnWidth(3, 200)
        self._prev_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self._prev_table.setSelectionMode(QTableWidget.SelectionMode.NoSelection)
        self._prev_table.verticalHeader().setVisible(False)
        self._prev_table.setShowGrid(False)
        self._prev_table.setAlternatingRowColors(True)
        self._prev_table.setFixedHeight(170)
        pl.addWidget(self._prev_table)
        il.addWidget(prev_box)

        # 警告エリア
        self._warn_box = QLabel("")
        self._warn_box.setWordWrap(True)
        self._warn_box.setVisible(False)
        self._warn_box.setStyleSheet(
            "background:#fffbeb; border:1px solid #fcd34d; "
            "border-radius:4px; padding:6px 10px; font-size:11px;"
        )
        il.addWidget(self._warn_box)

        # マージ戦略
        merge_box = QGroupBox("③ 既存データの扱い")
        ml = QHBoxLayout(merge_box)
        self._rb_overwrite = QRadioButton("すべて上書き")
        self._rb_overwrite.setChecked(True)
        self._rb_overwrite.setToolTip(
            "既に入力済みの日付も含め、CSV の内容で全て置き換えます")
        self._rb_fill = QRadioButton("未入力の日付のみ追加")
        self._rb_fill.setToolTip(
            "まだ希望が入力されていない日付だけ追加します（既存データを保持）")
        ml.addWidget(self._rb_overwrite)
        ml.addWidget(self._rb_fill)
        ml.addStretch()
        il.addWidget(merge_box)

        il.addStretch()
        tabs.addTab(import_tab, "インポート")

        # ── タブ2: 設定方法 ──────────────────────────────────────────
        guide_tab = QWidget()
        gl = QVBoxLayout(guide_tab)
        self._guide_text = QTextEdit()
        self._guide_text.setReadOnly(True)
        self._guide_text.setFont(QFont("", 10))
        self._guide_text.setPlainText(self._build_guide_text())
        gl.addWidget(self._guide_text)

        copy_btn = QPushButton("テンプレートをクリップボードにコピー")
        copy_btn.setFixedHeight(30)
        copy_btn.clicked.connect(self._copy_guide)
        gl.addWidget(copy_btn, alignment=Qt.AlignmentFlag.AlignRight)

        tabs.addTab(guide_tab, "Google Forms 設定方法")

        root.addWidget(tabs)

        # ── ボタン行 ─────────────────────────────────────────────────
        sep = QFrame()
        sep.setFrameShape(QFrame.Shape.HLine)
        root.addWidget(sep)

        btn_row = QHBoxLayout()
        btn_cancel = QPushButton("キャンセル")
        btn_cancel.setFixedHeight(36)
        btn_cancel.clicked.connect(self.reject)

        self._btn_import = QPushButton("　インポート実行　")
        self._btn_import.setFixedHeight(36)
        self._btn_import.setFont(QFont("", 10, QFont.Weight.Bold))
        self._btn_import.setEnabled(False)
        self._btn_import.clicked.connect(self._on_import)
        self._apply_btn_styles()

        btn_row.addWidget(btn_cancel)
        btn_row.addStretch()
        btn_row.addWidget(self._btn_import)
        root.addLayout(btn_row)

    def _apply_btn_styles(self):
        c = theme.c
        self._btn_import.setStyleSheet(
            f"QPushButton {{ background:{c['primary']}; color:white; border-radius:6px; padding:0 24px; }}"
            f" QPushButton:hover {{ background:{c['primary_hover']}; }}"
            f" QPushButton:disabled {{ background:{c['primary_dis']}; }}"
        )

    # ── ガイドテキスト ────────────────────────────────────────────────

    def _build_guide_text(self) -> str:
        from utils.forms_csv_parser import build_form_guide
        return build_form_guide(self._period, self._employees)

    def _copy_guide(self):
        from PyQt6.QtWidgets import QApplication
        QApplication.clipboard().setText(self._guide_text.toPlainText())
        from utils.toast import show_toast
        show_toast("テンプレートをクリップボードにコピーしました")

    # ── ファイル選択・プレビュー ──────────────────────────────────────

    def _on_open_file(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Google Forms CSV を選択", "", "CSV ファイル (*.csv);;すべてのファイル (*)"
        )
        if not path:
            return
        self._csv_path = path
        # ファイル名だけ表示（パスが長い場合）
        import os
        self._path_label.setText(os.path.basename(path))
        self._path_label.setToolTip(path)
        self._run_preview()

    def _run_preview(self):
        from utils.forms_csv_parser import parse_forms_csv
        try:
            result = parse_forms_csv(self._csv_path, self._period, self._employees)
        except Exception as e:
            QMessageBox.critical(self, "読み込みエラー", f"CSV の解析に失敗しました:\n{e}")
            return

        self._result = result
        c = theme.c

        # ─ プレビューテーブルを構築 ─
        all_names = list(result.matched.keys()) + result.unmatched_names
        self._prev_table.setRowCount(len(all_names))

        for row, name in enumerate(all_names):
            matched = name in result.matched
            count   = result.matched.get(name, 0)

            icon_item  = QTableWidgetItem("✅" if matched else "❌")
            name_item  = QTableWidgetItem(name)
            count_item = QTableWidgetItem(f"{count}件" if matched else "—")
            state_item = QTableWidgetItem(
                "登録済み" if matched else "⚠️ スタッフ未登録（スキップ）"
            )

            icon_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            count_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)

            if not matched:
                for item in (icon_item, name_item, count_item, state_item):
                    item.setForeground(QBrush(QColor("#b45309")))

            self._prev_table.setItem(row, 0, icon_item)
            self._prev_table.setItem(row, 1, name_item)
            self._prev_table.setItem(row, 2, count_item)
            self._prev_table.setItem(row, 3, state_item)
            self._prev_table.setRowHeight(row, 26)

        # ─ 警告 ─
        if result.warnings:
            shown = result.warnings[:6]
            extra = len(result.warnings) - len(shown)
            text  = f"⚠️ {len(result.warnings)} 件の警告:\n"
            text += "\n".join(f"・{w}" for w in shown)
            if extra > 0:
                text += f"\n  … 他 {extra} 件"
            self._warn_box.setText(text)
            self._warn_box.setVisible(True)
        else:
            self._warn_box.setVisible(False)

        # ─ インポートボタン ─
        self._btn_import.setEnabled(bool(result.matched))

    # ── インポート実行 ─────────────────────────────────────────────────

    def _on_import(self):
        if not self._result:
            return

        result     = self._result
        overwrite  = self._rb_overwrite.isChecked()

        if overwrite:
            requests_to_save = result.requests
        else:
            from db import repositories as repo
            existing = repo.get_shift_requests(self._period.id)
            existing_keys = {(r.employee_id, r.date) for r in existing if r.pattern_id}
            requests_to_save = [
                r for r in result.requests
                if (r.employee_id, r.date) not in existing_keys
            ]

        if not requests_to_save:
            QMessageBox.information(
                self, "インポート",
                "追加・更新するデータがありませんでした。\n"
                "（全日付が既に入力済みか、未回答の CSV かもしれません）"
            )
            return

        # 全スタッフ分をまとめて一括保存
        from db import repositories as repo
        repo.save_shift_requests(self._period.id, requests_to_save)

        from collections import Counter
        emp_count = len(Counter(r.employee_id for r in requests_to_save))
        QMessageBox.information(
            self, "インポート完了",
            f"{len(requests_to_save)} 件のシフト希望をインポートしました。\n"
            f"対象スタッフ: {emp_count} 名"
        )
        self.accept()
