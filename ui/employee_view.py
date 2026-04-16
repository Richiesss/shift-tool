"""従業員管理画面"""
from __future__ import annotations
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QPushButton, QTableWidget,
    QTableWidgetItem, QHeaderView, QDialog, QFormLayout, QLineEdit,
    QComboBox, QGroupBox, QCheckBox, QLabel, QMessageBox,
    QCalendarWidget, QDialogButtonBox, QSizePolicy, QFrame,
    QScrollArea, QGridLayout
)
from PyQt6.QtCore import Qt, QDate
from PyQt6.QtGui import QFont, QColor
from db import repositories as repo
from models.employee import Employee, FixedPattern
from utils.constants import EmploymentType, SkillLevel, PrimaryPosition, TimeSlot, DAY_OF_WEEK_LABELS
from utils.theme import theme


class EmployeeView(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._build_ui()
        self.refresh()

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(12)

        # ヘッダー
        header = QHBoxLayout()
        title = QLabel("従業員管理")
        title.setFont(QFont("", 16, QFont.Weight.Bold))
        header.addWidget(title)
        header.addStretch()
        self._btn_new = QPushButton("＋ 新規登録")
        self._btn_new.setFixedHeight(36)
        self._btn_new.clicked.connect(self._on_new)
        header.addWidget(self._btn_new)
        self._apply_btn_style()
        layout.addLayout(header)

        # テーブル
        self.table = QTableWidget()
        self.table.setColumnCount(7)
        self.table.setHorizontalHeaderLabels(["ID", "氏名", "所属ポジション", "勤務時間帯", "習熟度", "雇用形態", "操作"])
        self.table.horizontalHeader().setSectionResizeMode(4, QHeaderView.ResizeMode.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        self.table.setColumnWidth(0, 40)
        self.table.setColumnWidth(2, 100)
        self.table.setColumnWidth(3, 100)
        self.table.setColumnWidth(5, 80)
        self.table.setColumnWidth(6, 130)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.setAlternatingRowColors(True)
        self.table.verticalHeader().setVisible(False)
        self.table.setShowGrid(False)
        layout.addWidget(self.table)

    def _apply_btn_style(self):
        c = theme.c
        self._btn_new.setStyleSheet(
            f"QPushButton {{ background:{c['primary']}; color:white; border-radius:6px; "
            f"padding:0 16px; font-weight:bold; }} "
            f"QPushButton:hover {{ background:{c['primary_hover']}; }}"
        )

    def apply_theme(self):
        self._apply_btn_style()
        self.refresh()

    def refresh(self):
        self.employees = repo.get_all_employees()
        self.table.setRowCount(len(self.employees))
        for row, emp in enumerate(self.employees):
            pp_text = emp.primary_position.label() if emp.primary_position else "どちらでも"
            pt_text = emp.primary_timeslot.short_label() + "専任" if emp.primary_timeslot else "どちらでも"
            skill_text = f"H:{emp.hall_skill.label()} / K:{emp.kitchen_skill.label()}"
            self.table.setItem(row, 0, QTableWidgetItem(str(emp.id)))
            self.table.setItem(row, 1, QTableWidgetItem(emp.name))
            self.table.setItem(row, 2, QTableWidgetItem(pp_text))
            self.table.setItem(row, 3, QTableWidgetItem(pt_text))
            self.table.setItem(row, 4, QTableWidgetItem(skill_text))
            self.table.setItem(row, 5, QTableWidgetItem(emp.employment_type.label()))

            # 操作ボタン
            btn_widget = QWidget()
            btn_layout = QHBoxLayout(btn_widget)
            btn_layout.setContentsMargins(4, 2, 4, 2)
            btn_layout.setSpacing(6)

            btn_edit = QPushButton("編集")
            btn_edit.setFixedSize(56, 28)
            c = theme.c
            btn_edit.setStyleSheet(
                f"QPushButton {{ background:{c['surface']}; border:1px solid {c['border2']}; "
                f"border-radius:4px; color:{c['text']}; }} "
                f"QPushButton:hover {{ background:{c['surface2']}; }}"
            )
            btn_edit.clicked.connect(lambda _, e=emp: self._on_edit(e))

            btn_del = QPushButton("削除")
            btn_del.setFixedSize(56, 28)
            btn_del.setStyleSheet(
                f"QPushButton {{ background:{c['danger_bg']}; border:1px solid {c['danger_border']}; "
                f"border-radius:4px; color:{c['danger_text']}; }} "
                f"QPushButton:hover {{ background:{c['danger_bg']}; }}"
            )
            btn_del.clicked.connect(lambda _, e=emp: self._on_delete(e))

            btn_layout.addWidget(btn_edit)
            btn_layout.addWidget(btn_del)
            self.table.setCellWidget(row, 6, btn_widget)
            self.table.setRowHeight(row, 40)

    def _on_new(self):
        dlg = EmployeeDialog(parent=self)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            repo.save_employee(dlg.employee)
            self.refresh()

    def _on_edit(self, emp: Employee):
        dlg = EmployeeDialog(employee=emp, parent=self)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            repo.save_employee(dlg.employee)
            self.refresh()

    def _on_delete(self, emp: Employee):
        reply = QMessageBox.question(
            self, "削除確認",
            f"「{emp.name}さん」を削除しますか？\n（過去のシフトデータは保持されます）",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
        )
        if reply == QMessageBox.StandardButton.Yes:
            repo.delete_employee(emp.id)
            self.refresh()


class EmployeeDialog(QDialog):
    def __init__(self, employee: Employee = None, parent=None):
        super().__init__(parent)
        self.employee = employee
        self.setWindowTitle("従業員登録" if employee is None else "従業員編集")
        self.setMinimumWidth(520)
        self._build_ui()
        if employee:
            self._load_employee(employee)

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setSpacing(16)

        # 基本情報
        basic_group = QGroupBox("基本情報")
        form = QFormLayout(basic_group)
        form.setSpacing(10)

        self.name_edit = QLineEdit()
        self.name_edit.setPlaceholderText("例：うおまん 太郎")
        form.addRow("氏名 *", self.name_edit)

        self.emp_type_combo = QComboBox()
        self.emp_type_combo.addItem("正社員", EmploymentType.FULL_TIME)
        self.emp_type_combo.addItem("アルバイト", EmploymentType.PART_TIME)
        self.emp_type_combo.currentIndexChanged.connect(self._on_type_changed)
        form.addRow("雇用形態 *", self.emp_type_combo)

        self.primary_pos_combo = QComboBox()
        self.primary_pos_combo.addItem("どちらでも（制限なし）", None)
        self.primary_pos_combo.addItem("ホール専任", PrimaryPosition.HALL)
        self.primary_pos_combo.addItem("キッチン専任", PrimaryPosition.KITCHEN)
        form.addRow("所属ポジション", self.primary_pos_combo)

        self.primary_ts_combo = QComboBox()
        self.primary_ts_combo.addItem("どちらでも", None)
        self.primary_ts_combo.addItem("朝食専任", TimeSlot.BREAKFAST)
        self.primary_ts_combo.addItem("ディナー専任", TimeSlot.DINNER)
        form.addRow("勤務時間帯", self.primary_ts_combo)
        layout.addWidget(basic_group)

        # 習熟度
        skill_group = QGroupBox("習熟度（ポジション別）")
        skill_layout = QFormLayout(skill_group)
        skill_layout.setSpacing(10)

        self.hall_skill_combo = QComboBox()
        self.kitchen_skill_combo = QComboBox()
        for label, val in [("⭐⭐⭐⭐", SkillLevel.LEADER), ("⭐⭐⭐", SkillLevel.VETERAN),
                            ("⭐⭐", SkillLevel.GENERAL), ("⭐", SkillLevel.BEGINNER)]:
            self.hall_skill_combo.addItem(label, val)
            self.kitchen_skill_combo.addItem(label, val)
        self.hall_skill_combo.setCurrentIndex(3)
        self.kitchen_skill_combo.setCurrentIndex(3)
        skill_layout.addRow("ホール習熟度", self.hall_skill_combo)
        skill_layout.addRow("キッチン習熟度", self.kitchen_skill_combo)
        layout.addWidget(skill_group)

        # アルバイト専用設定
        self.parttime_group = QGroupBox("アルバイト設定")
        pt_layout = QVBoxLayout(self.parttime_group)

        # 固定シフトパターン
        pattern_label = QLabel("固定シフトパターン（毎週の出勤希望）")
        pattern_label.setStyleSheet("font-weight:bold;")
        pt_layout.addWidget(pattern_label)

        pattern_grid = QGridLayout()
        pattern_grid.setSpacing(6)
        pattern_grid.addWidget(QLabel("曜日"), 0, 0)
        pattern_grid.addWidget(QLabel("朝食"), 0, 1, Qt.AlignmentFlag.AlignCenter)
        pattern_grid.addWidget(QLabel("ディナー"), 0, 2, Qt.AlignmentFlag.AlignCenter)

        self.pattern_checks: list[tuple[QCheckBox, QCheckBox]] = []
        for i, day in enumerate(DAY_OF_WEEK_LABELS):
            lbl = QLabel(day)
            cb_b = QCheckBox()
            cb_d = QCheckBox()
            pattern_grid.addWidget(lbl, i + 1, 0)
            pattern_grid.addWidget(cb_b, i + 1, 1, Qt.AlignmentFlag.AlignCenter)
            pattern_grid.addWidget(cb_d, i + 1, 2, Qt.AlignmentFlag.AlignCenter)
            self.pattern_checks.append((cb_b, cb_d))

        pt_layout.addLayout(pattern_grid)

        note = QLabel("※ 固定パターンなしの場合はすべてチェックを外してください")
        note.setStyleSheet("color:#6b7280; font-size:11px;")
        pt_layout.addWidget(note)

        layout.addWidget(self.parttime_group)

        # ボタン
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QDialogButtonBox.StandardButton.Save).setText("保存")
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("キャンセル")
        buttons.accepted.connect(self._on_save)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

        self._on_type_changed()

    def _on_type_changed(self):
        is_part = self.emp_type_combo.currentData() == EmploymentType.PART_TIME
        self.parttime_group.setVisible(is_part)
        self.adjustSize()

    def _load_employee(self, emp: Employee):
        self.name_edit.setText(emp.name)
        idx = self.emp_type_combo.findData(emp.employment_type)
        self.emp_type_combo.setCurrentIndex(idx)
        idx_pp = self.primary_pos_combo.findData(emp.primary_position)
        self.primary_pos_combo.setCurrentIndex(idx_pp if idx_pp >= 0 else 0)
        idx_pt = self.primary_ts_combo.findData(emp.primary_timeslot)
        self.primary_ts_combo.setCurrentIndex(idx_pt if idx_pt >= 0 else 0)

        idx_h = self.hall_skill_combo.findData(emp.hall_skill)
        self.hall_skill_combo.setCurrentIndex(idx_h)
        idx_k = self.kitchen_skill_combo.findData(emp.kitchen_skill)
        self.kitchen_skill_combo.setCurrentIndex(idx_k)

        for i, (cb_b, cb_d) in enumerate(self.pattern_checks):
            p = emp.get_pattern(i)
            cb_b.setChecked(p.breakfast if p else False)
            cb_d.setChecked(p.dinner if p else False)

    def _on_save(self):
        name = self.name_edit.text().strip()
        if not name:
            QMessageBox.warning(self, "入力エラー", "氏名を入力してください")
            return

        emp_type = self.emp_type_combo.currentData()
        primary_position = self.primary_pos_combo.currentData()
        primary_timeslot = self.primary_ts_combo.currentData()
        hall_skill = self.hall_skill_combo.currentData()
        kitchen_skill = self.kitchen_skill_combo.currentData()

        patterns = []
        if emp_type == EmploymentType.PART_TIME:
            for i, (cb_b, cb_d) in enumerate(self.pattern_checks):
                if cb_b.isChecked() or cb_d.isChecked():
                    patterns.append(FixedPattern(i, cb_b.isChecked(), cb_d.isChecked()))

        existing_id = self.employee.id if self.employee else None
        existing_unavail = self.employee.fixed_unavailable_dates if self.employee else []

        self.employee = Employee(
            id=existing_id,
            name=name,
            employment_type=emp_type,
            hall_skill=hall_skill,
            kitchen_skill=kitchen_skill,
            primary_position=primary_position,
            primary_timeslot=primary_timeslot,
            fixed_patterns=patterns,
            fixed_unavailable_dates=existing_unavail,
        )
        self.accept()
