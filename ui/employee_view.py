"""従業員管理画面"""
from __future__ import annotations
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QPushButton, QTableWidget,
    QTableWidgetItem, QHeaderView, QDialog, QFormLayout, QLineEdit,
    QComboBox, QGroupBox, QCheckBox, QLabel, QMessageBox,
    QCalendarWidget, QDialogButtonBox, QSizePolicy, QFrame,
    QScrollArea, QGridLayout, QListWidget, QDateEdit
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

        # item 6: 検索ボックス
        self._search_edit = QLineEdit()
        self._search_edit.setPlaceholderText("🔍 氏名で検索...")
        self._search_edit.setFixedWidth(180)
        self._search_edit.setFixedHeight(32)
        self._search_edit.setToolTip("従業員名で絞り込みます")
        self._search_edit.textChanged.connect(self._on_search_changed)
        header.addWidget(self._search_edit)
        header.addStretch()
        self._btn_show_archive = QPushButton("アーカイブを表示")
        self._btn_show_archive.setFixedHeight(36)
        self._btn_show_archive.setCheckable(True)
        self._btn_show_archive.toggled.connect(self._on_toggle_archive)
        header.addWidget(self._btn_show_archive)
        self._btn_new = QPushButton("＋ 新規登録")
        self._btn_new.setFixedHeight(36)
        self._btn_new.clicked.connect(self._on_new)
        header.addWidget(self._btn_new)
        self._apply_btn_style()
        layout.addLayout(header)

        self._show_archive = False
        self._search_text  = ""

        # テーブル
        self.table = QTableWidget()
        self.table.setColumnCount(7)
        self.table.setHorizontalHeaderLabels(["ID", "氏名", "所属ポジション", "勤務時間帯", "習熟度", "雇用形態", "操作"])
        # item 8: ツールチップ
        self.table.horizontalHeaderItem(2).setToolTip("ホール / キッチン / （兼務可）= 主所属外ポジションにも対応可")
        self.table.horizontalHeaderItem(3).setToolTip("朝食専任 / ディナー専任 / どちらでも")
        self.table.horizontalHeaderItem(4).setToolTip("H=ホール習熟度、K=キッチン習熟度\nL:リーダー / V:ベテラン / 一般 / 初心者")
        self.table.horizontalHeaderItem(5).setToolTip("正社員はシフト制約が異なります（希望入力不要）")
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
        self._btn_show_archive.setStyleSheet(
            f"QPushButton {{ background:{c['surface']}; border:1px solid {c['border2']}; "
            f"border-radius:6px; padding:0 16px; color:{c['text']}; }} "
            f"QPushButton:checked {{ background:{c['surface2']}; color:{c['text2']}; }} "
            f"QPushButton:hover {{ background:{c['surface2']}; }}"
        )
        self._btn_new.setStyleSheet(
            f"QPushButton {{ background:{c['primary']}; color:white; border-radius:6px; "
            f"padding:0 16px; font-weight:bold; }} "
            f"QPushButton:hover {{ background:{c['primary_hover']}; }}"
        )

    def apply_theme(self):
        self._apply_btn_style()
        self.refresh()

    def _on_toggle_archive(self, checked: bool):
        self._show_archive = checked
        self._btn_show_archive.setText(
            "アクティブを表示" if checked else "アーカイブを表示"
        )
        self.refresh()

    def _on_search_changed(self, text: str):
        """item 6: 検索テキスト変更時に再描画"""
        self._search_text = text.strip().lower()
        self.refresh()

    def refresh(self):
        all_employees = repo.get_all_employees(active_only=not self._show_archive)
        # item 6: 検索フィルター
        if self._search_text:
            self.employees = [e for e in all_employees
                              if self._search_text in e.name.lower()]
        else:
            self.employees = all_employees
        self.table.setRowCount(len(self.employees))
        for row, emp in enumerate(self.employees):
            pp_label = emp.primary_position.label() if emp.primary_position else "未設定"
            pp_text  = f"{pp_label}（兼務可）" if emp.can_work_both_positions else pp_label
            pt_text = emp.primary_timeslot.short_label() + "専任" if emp.primary_timeslot else "どちらでも"
            skill_text = f"H:{emp.hall_skill.label()} / K:{emp.kitchen_skill.label()}"
            name_text = emp.name if emp.is_active else f"{emp.name}（アーカイブ）"
            self.table.setItem(row, 0, QTableWidgetItem(str(emp.id)))
            self.table.setItem(row, 1, QTableWidgetItem(name_text))
            self.table.setItem(row, 2, QTableWidgetItem(pp_text))
            self.table.setItem(row, 3, QTableWidgetItem(pt_text))
            self.table.setItem(row, 4, QTableWidgetItem(skill_text))
            self.table.setItem(row, 5, QTableWidgetItem(emp.employment_type.label()))
            if not emp.is_active:
                from PyQt6.QtGui import QColor, QBrush
                for col in range(6):
                    item = self.table.item(row, col)
                    if item:
                        item.setForeground(QBrush(QColor("#9ca3af")))

            # 操作ボタン
            btn_widget = QWidget()
            btn_layout = QHBoxLayout(btn_widget)
            btn_layout.setContentsMargins(4, 2, 4, 2)
            btn_layout.setSpacing(6)

            c = theme.c
            if emp.is_active:
                btn_edit = QPushButton("編集")
                btn_edit.setFixedSize(56, 28)
                btn_edit.setStyleSheet(
                    f"QPushButton {{ background:{c['surface']}; border:1px solid {c['border2']}; "
                    f"border-radius:4px; color:{c['text']}; }} "
                    f"QPushButton:hover {{ background:{c['surface2']}; }}"
                )
                btn_edit.setToolTip("従業員情報を編集します")
                btn_edit.clicked.connect(lambda _, e=emp: self._on_edit(e))
                btn_layout.addWidget(btn_edit)

                btn_del = QPushButton("削除")
                btn_del.setFixedSize(56, 28)
                btn_del.setStyleSheet(
                    f"QPushButton {{ background:{c['danger_bg']}; border:1px solid {c['danger_border']}; "
                    f"border-radius:4px; color:{c['danger_text']}; }} "
                    f"QPushButton:hover {{ background:{c['danger_bg']}; }}"
                )
                btn_del.setToolTip("論理削除します（過去のシフトデータは保持）")
                btn_del.clicked.connect(lambda _, e=emp: self._on_delete(e))
                btn_layout.addWidget(btn_del)
            else:
                btn_restore = QPushButton("復元")
                btn_restore.setFixedSize(56, 28)
                btn_restore.setStyleSheet(
                    f"QPushButton {{ background:{c['primary']}; border:none; "
                    f"border-radius:4px; color:white; }} "
                    f"QPushButton:hover {{ background:{c['primary_hover']}; }}"
                )
                btn_restore.setToolTip("アクティブな従業員として復元します")
                btn_restore.clicked.connect(lambda _, e=emp: self._on_restore(e))
                btn_layout.addWidget(btn_restore)

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

    def _on_restore(self, emp: Employee):
        reply = QMessageBox.question(
            self, "復元確認",
            f"「{emp.name}さん」を復元しますか？\n（アクティブな従業員として再登録されます）",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
        )
        if reply == QMessageBox.StandardButton.Yes:
            repo.restore_employee(emp.id)
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
        self.primary_pos_combo.addItem("ホール", PrimaryPosition.HALL)
        self.primary_pos_combo.addItem("キッチン", PrimaryPosition.KITCHEN)
        form.addRow("所属ポジション *", self.primary_pos_combo)

        self.can_both_check = QCheckBox("兼務可（主所属外のポジションにも入れる）")
        form.addRow("", self.can_both_check)

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
        for label, val in [("リーダー", SkillLevel.LEADER), ("ベテラン", SkillLevel.VETERAN),
                            ("メンバー", SkillLevel.GENERAL), ("ビギナー", SkillLevel.BEGINNER)]:
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

        # 固定不可日
        unavail_group = QGroupBox("固定不可日（出勤不可の日を登録）")
        unavail_vl = QVBoxLayout(unavail_group)
        unavail_vl.setSpacing(6)

        add_row = QHBoxLayout()
        self._unavail_date_edit = QDateEdit()
        self._unavail_date_edit.setCalendarPopup(True)
        self._unavail_date_edit.setDate(QDate.currentDate())
        self._unavail_date_edit.setDisplayFormat("yyyy/MM/dd")
        btn_add_unavail = QPushButton("追加")
        btn_add_unavail.setFixedWidth(56)
        btn_add_unavail.clicked.connect(self._add_unavail_date)
        add_row.addWidget(QLabel("日付:"))
        add_row.addWidget(self._unavail_date_edit)
        add_row.addWidget(btn_add_unavail)
        add_row.addStretch()
        unavail_vl.addLayout(add_row)

        self._unavail_list = QListWidget()
        self._unavail_list.setMaximumHeight(90)
        self._unavail_list.setSelectionMode(QListWidget.SelectionMode.MultiSelection)
        unavail_vl.addWidget(self._unavail_list)

        btn_remove_row = QHBoxLayout()
        btn_remove_row.addStretch()
        btn_remove_unavail = QPushButton("選択した日を削除")
        btn_remove_unavail.setFixedHeight(28)
        btn_remove_unavail.clicked.connect(self._remove_unavail_dates)
        btn_remove_row.addWidget(btn_remove_unavail)
        unavail_vl.addLayout(btn_remove_row)

        layout.addWidget(unavail_group)

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

    def _add_unavail_date(self):
        ds = self._unavail_date_edit.date().toString("yyyy-MM-dd")
        # 重複チェック
        existing = [self._unavail_list.item(i).text() for i in range(self._unavail_list.count())]
        if ds not in existing:
            self._unavail_list.addItem(ds)
            self._unavail_list.sortItems()

    def _remove_unavail_dates(self):
        for item in self._unavail_list.selectedItems():
            self._unavail_list.takeItem(self._unavail_list.row(item))

    def _load_employee(self, emp: Employee):
        self.name_edit.setText(emp.name)
        idx = self.emp_type_combo.findData(emp.employment_type)
        self.emp_type_combo.setCurrentIndex(idx)
        idx_pp = self.primary_pos_combo.findData(emp.primary_position)
        self.primary_pos_combo.setCurrentIndex(idx_pp if idx_pp >= 0 else 0)
        self.can_both_check.setChecked(emp.can_work_both_positions)
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

        self._unavail_list.clear()
        for ds in sorted(emp.fixed_unavailable_dates):
            self._unavail_list.addItem(ds)

    def _on_save(self):
        name = self.name_edit.text().strip()
        if not name:
            QMessageBox.warning(self, "入力エラー", "氏名を入力してください")
            return

        emp_type             = self.emp_type_combo.currentData()
        primary_position     = self.primary_pos_combo.currentData()
        can_work_both        = self.can_both_check.isChecked()
        primary_timeslot     = self.primary_ts_combo.currentData()
        hall_skill = self.hall_skill_combo.currentData()
        kitchen_skill = self.kitchen_skill_combo.currentData()

        patterns = []
        if emp_type == EmploymentType.PART_TIME:
            for i, (cb_b, cb_d) in enumerate(self.pattern_checks):
                if cb_b.isChecked() or cb_d.isChecked():
                    patterns.append(FixedPattern(i, cb_b.isChecked(), cb_d.isChecked()))

        existing_id = self.employee.id if self.employee else None
        unavail_dates = [
            self._unavail_list.item(i).text()
            for i in range(self._unavail_list.count())
        ]

        self.employee = Employee(
            id=existing_id,
            name=name,
            employment_type=emp_type,
            hall_skill=hall_skill,
            kitchen_skill=kitchen_skill,
            primary_position=primary_position,
            can_work_both_positions=can_work_both,
            primary_timeslot=primary_timeslot,
            fixed_patterns=patterns,
            fixed_unavailable_dates=unavail_dates,
        )
        self.accept()
