"""希望シフト入力画面"""
from __future__ import annotations
from datetime import date, timedelta
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QPushButton, QLabel,
    QComboBox, QDateEdit, QTableWidget, QTableWidgetItem,
    QHeaderView, QCheckBox, QMessageBox, QFrame, QGroupBox,
    QScrollArea, QLineEdit, QSizePolicy
)
from PyQt6.QtCore import Qt, QDate
from PyQt6.QtGui import QFont, QColor, QBrush
from db import repositories as repo
from models.schedule import SchedulePeriod, ShiftRequest
from utils.constants import DAY_OF_WEEK_LABELS


class ShiftInputView(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._period: SchedulePeriod | None = None
        self._employees = []
        self._current_idx = 0
        self._requests: dict[tuple[int, str], ShiftRequest] = {}
        self._build_ui()
        self._load_periods()

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(12)

        # タイトル
        title = QLabel("希望シフト入力")
        title.setFont(QFont("", 16, QFont.Weight.Bold))
        layout.addWidget(title)

        # 期間選択
        period_group = QGroupBox("対象期間")
        period_layout = QHBoxLayout(period_group)

        self.period_combo = QComboBox()
        self.period_combo.setMinimumWidth(220)
        self.period_combo.currentIndexChanged.connect(self._on_period_changed)
        period_layout.addWidget(QLabel("既存期間:"))
        period_layout.addWidget(self.period_combo)

        period_layout.addWidget(QLabel("  または新規:"))

        self.start_date_edit = QDateEdit()
        self.start_date_edit.setCalendarPopup(True)
        self.start_date_edit.setDate(QDate.currentDate())
        self.start_date_edit.setDisplayFormat("yyyy/MM/dd")

        self.end_date_edit = QDateEdit()
        self.end_date_edit.setCalendarPopup(True)
        self.end_date_edit.setDate(QDate.currentDate().addDays(13))
        self.end_date_edit.setDisplayFormat("yyyy/MM/dd")

        period_layout.addWidget(QLabel("開始:"))
        period_layout.addWidget(self.start_date_edit)
        period_layout.addWidget(QLabel("終了:"))
        period_layout.addWidget(self.end_date_edit)

        btn_set = QPushButton("期間確定")
        btn_set.setFixedHeight(32)
        btn_set.setStyleSheet("QPushButton { background:#2563eb; color:white; border-radius:5px; padding:0 12px; } QPushButton:hover { background:#1d4ed8; }")
        btn_set.clicked.connect(self._on_set_period)
        period_layout.addWidget(btn_set)
        period_layout.addStretch()
        layout.addWidget(period_group)

        # 進捗バー
        self.progress_label = QLabel("")
        self.progress_label.setStyleSheet("color:#6b7280;")
        layout.addWidget(self.progress_label)

        # 従業員選択
        emp_nav = QHBoxLayout()
        self.emp_combo = QComboBox()
        self.emp_combo.setMinimumWidth(180)
        self.emp_combo.currentIndexChanged.connect(self._on_employee_changed)

        btn_prev = QPushButton("◀ 前の従業員")
        btn_prev.clicked.connect(self._on_prev_employee)
        btn_next = QPushButton("次の従業員 ▶")
        btn_next.clicked.connect(self._on_next_employee)
        for b in [btn_prev, btn_next]:
            b.setFixedHeight(32)
            b.setStyleSheet("QPushButton { border:1px solid #d1d5db; border-radius:5px; padding:0 12px; } QPushButton:hover { background:#f3f4f6; }")

        emp_nav.addWidget(QLabel("従業員:"))
        emp_nav.addWidget(self.emp_combo)
        emp_nav.addStretch()
        emp_nav.addWidget(btn_prev)
        emp_nav.addWidget(btn_next)
        layout.addLayout(emp_nav)

        # 入力テーブル
        self.table = QTableWidget()
        self.table.setColumnCount(4)
        self.table.setHorizontalHeaderLabels(["日付", "朝食 (6:00-11:00)", "ディナー (17:00-23:00)", "備考"])
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Fixed)
        self.table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.Fixed)
        self.table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)
        self.table.setColumnWidth(1, 160)
        self.table.setColumnWidth(2, 160)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.verticalHeader().setVisible(False)
        layout.addWidget(self.table)

        # 保存ボタン
        btn_row = QHBoxLayout()
        btn_row.addStretch()
        self.btn_save = QPushButton("この従業員の希望を保存")
        self.btn_save.setFixedHeight(36)
        self.btn_save.setStyleSheet("QPushButton { background:#16a34a; color:white; border-radius:6px; padding:0 20px; font-weight:bold; } QPushButton:hover { background:#15803d; }")
        self.btn_save.clicked.connect(self._on_save)
        btn_row.addWidget(self.btn_save)
        layout.addLayout(btn_row)

    def _load_periods(self):
        periods = repo.get_all_periods()
        self.period_combo.blockSignals(True)
        self.period_combo.clear()
        self.period_combo.addItem("（新規期間を入力）", None)
        for p in periods:
            self.period_combo.addItem(f"{p.start_date} 〜 {p.end_date}", p)
        self.period_combo.blockSignals(False)

    def _on_period_changed(self, idx):
        p = self.period_combo.currentData()
        if p:
            self._period = p
            self._load_employees()

    def _on_set_period(self):
        start = self.start_date_edit.date().toString("yyyy-MM-dd")
        end = self.end_date_edit.date().toString("yyyy-MM-dd")
        if start >= end:
            QMessageBox.warning(self, "入力エラー", "開始日は終了日より前にしてください")
            return
        period = SchedulePeriod(id=None, start_date=start, end_date=end)
        period = repo.save_period(period)
        self._period = period
        self._load_periods()
        # 新しく作った期間を選択
        for i in range(self.period_combo.count()):
            p = self.period_combo.itemData(i)
            if p and p.id == period.id:
                self.period_combo.setCurrentIndex(i)
                break
        self._load_employees()

    def _load_employees(self):
        if not self._period:
            return
        self._employees = repo.get_all_employees()
        existing = repo.get_shift_requests(self._period.id)
        self._requests = {(r.employee_id, r.date): r for r in existing}

        self.emp_combo.blockSignals(True)
        self.emp_combo.clear()
        for emp in self._employees:
            self.emp_combo.addItem(emp.name, emp)
        self.emp_combo.blockSignals(False)

        self._current_idx = 0
        self._update_progress()
        self._render_table()

    def _update_progress(self):
        if not self._period or not self._employees:
            self.progress_label.setText("")
            return
        filled = set(r.employee_id for r in self._requests.values() if r.breakfast or r.dinner)
        total = len(self._employees)
        self.progress_label.setText(f"入力済: {len(filled)} / {total} 名")

    def _on_employee_changed(self, idx):
        self._current_idx = idx
        self._render_table()

    def _on_prev_employee(self):
        if self._current_idx > 0:
            self._current_idx -= 1
            self.emp_combo.setCurrentIndex(self._current_idx)

    def _on_next_employee(self):
        if self._current_idx < len(self._employees) - 1:
            self._current_idx += 1
            self.emp_combo.setCurrentIndex(self._current_idx)

    def _render_table(self):
        if not self._period or not self._employees:
            self.table.setRowCount(0)
            return

        emp = self._employees[self._current_idx]
        dates = self._period.date_range()
        self.table.setRowCount(len(dates))
        self._note_edits = {}
        self._checkboxes = {}

        for row, d in enumerate(dates):
            date_str = d.isoformat()
            dow = d.weekday()
            dow_label = DAY_OF_WEEK_LABELS[dow]
            date_display = f"{d.month}/{d.day}({dow_label})"

            # 日付セル（土日は色付け）
            date_item = QTableWidgetItem(date_display)
            date_item.setData(Qt.ItemDataRole.UserRole, date_str)
            if dow == 5:
                date_item.setForeground(QBrush(QColor("#2563eb")))
            elif dow == 6:
                date_item.setForeground(QBrush(QColor("#dc2626")))
            self.table.setItem(row, 0, date_item)

            req = self._requests.get((emp.id, date_str))

            # 不可日かどうか
            is_unavail = date_str in emp.fixed_unavailable_dates

            # チェックボックス（朝食）
            cb_b_widget = QWidget()
            cb_b_layout = QHBoxLayout(cb_b_widget)
            cb_b_layout.setContentsMargins(0, 0, 0, 0)
            cb_b_layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
            cb_b = QCheckBox("可")
            cb_b.setEnabled(not is_unavail)

            # アルバイトの固定パターンを自動反映
            if emp.has_fixed_pattern():
                p = emp.get_pattern(dow)
                auto_b = p.breakfast if p else False
                cb_b.setChecked(auto_b if req is None else req.breakfast)
            else:
                cb_b.setChecked(req.breakfast if req else False)

            if is_unavail:
                cb_b.setChecked(False)
                cb_b.setToolTip("固定不可日")
            cb_b_layout.addWidget(cb_b)
            self.table.setCellWidget(row, 1, cb_b_widget)

            # チェックボックス（ディナー）
            cb_d_widget = QWidget()
            cb_d_layout = QHBoxLayout(cb_d_widget)
            cb_d_layout.setContentsMargins(0, 0, 0, 0)
            cb_d_layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
            cb_d = QCheckBox("可")
            cb_d.setEnabled(not is_unavail)

            if emp.has_fixed_pattern():
                p = emp.get_pattern(dow)
                auto_d = p.dinner if p else False
                cb_d.setChecked(auto_d if req is None else req.dinner)
            else:
                cb_d.setChecked(req.dinner if req else False)

            if is_unavail:
                cb_d.setChecked(False)
            cb_d_layout.addWidget(cb_d)
            self.table.setCellWidget(row, 2, cb_d_widget)

            # 備考テキスト
            note_item = QTableWidgetItem(req.note if req else "")
            note_item.setFlags(note_item.flags() | Qt.ItemFlag.ItemIsEditable)
            self.table.setItem(row, 3, note_item)

            # グレーアウト（不可日）
            if is_unavail:
                for col in range(4):
                    item = self.table.item(row, col)
                    if item:
                        item.setBackground(QBrush(QColor("#f3f4f6")))

            self.table.setRowHeight(row, 36)
            self._checkboxes[date_str] = (cb_b, cb_d)

    def _on_save(self):
        if not self._period or not self._employees:
            return
        emp = self._employees[self._current_idx]
        dates = self._period.date_range()
        requests = []
        for d in dates:
            date_str = d.isoformat()
            cbs = self._checkboxes.get(date_str)
            note_item = self.table.item(dates.index(d), 3)
            note = note_item.text() if note_item else ""
            if cbs:
                cb_b, cb_d = cbs
                requests.append(ShiftRequest(
                    employee_id=emp.id,
                    date=date_str,
                    breakfast=cb_b.isChecked(),
                    dinner=cb_d.isChecked(),
                    note=note
                ))
                self._requests[(emp.id, date_str)] = requests[-1]

        repo.save_shift_requests(self._period.id, requests)
        self._update_progress()
        QMessageBox.information(self, "保存完了", f"「{emp.name}」の希望シフトを保存しました")
