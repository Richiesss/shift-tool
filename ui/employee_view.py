"""従業員管理画面"""
from __future__ import annotations
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QPushButton, QTableWidget,
    QTableWidgetItem, QHeaderView, QDialog, QFormLayout, QLineEdit,
    QComboBox, QGroupBox, QCheckBox, QLabel, QMessageBox,
    QCalendarWidget, QDialogButtonBox, QSizePolicy, QFrame,
    QScrollArea
)
from PyQt6.QtGui import QFont, QColor
from db import repositories as repo
from models.employee import Employee
from utils.constants import EmploymentType, SkillLevel, PrimaryPosition, TimeSlot
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
            skill_text = (
                f"H朝:{emp.hall_skill_breakfast.label()} / H夜:{emp.hall_skill_dinner.label()} / "
                f"K朝:{emp.kitchen_skill_breakfast.label()} / K夜:{emp.kitchen_skill_dinner.label()}"
            )
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
        self.emp_type_combo.addItem("アルバイト/パート", EmploymentType.PART_TIME)
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
        self.primary_ts_combo.currentIndexChanged.connect(self._on_timeslot_changed)
        form.addRow("勤務時間帯", self.primary_ts_combo)
        layout.addWidget(basic_group)

        # 朝食タイムテーブル対応スキル
        self.timetable_group = timetable_group = QGroupBox("朝食タイムテーブル対応")
        tt_layout = QVBoxLayout(timetable_group)
        tt_layout.setSpacing(6)
        self.can_open_check = QCheckBox("開店準備対応可（5:45〜6:30）")
        self.can_open_check.setToolTip("朝食の開店準備シフトに対応可能なスタッフ\nシフト生成時に開店準備バンドの人数制約対象となります")
        self.can_cleanup_check = QCheckBox("朝食片付け対応可（10:00〜11:30）")
        self.can_cleanup_check.setToolTip("朝食後の片付け・レイアウト準備に対応可能なスタッフ\nシフト生成時に片付けバンドの人数制約対象となります")
        tt_layout.addWidget(self.can_open_check)
        tt_layout.addWidget(self.can_cleanup_check)
        layout.addWidget(timetable_group)

        # シフト希望省略設定
        avail_group = QGroupBox("シフト希望省略（常時出勤可）")
        avail_layout = QVBoxLayout(avail_group)
        avail_layout.setSpacing(6)
        avail_note = QLabel(
            "チェックしたスタッフは希望シフトの提出が不要です。\n"
            "対象の時間帯であれば毎日シフトに入れることができます。"
        )
        avail_note.setWordWrap(True)
        avail_note.setStyleSheet("font-size:11px; color:#6b7280;")
        avail_layout.addWidget(avail_note)
        self.always_avail_b_check = QCheckBox("朝食: 毎日出勤可（希望提出不要）")
        self.always_avail_d_check = QCheckBox("ディナー: 毎日出勤可（希望提出不要）")
        avail_layout.addWidget(self.always_avail_b_check)
        avail_layout.addWidget(self.always_avail_d_check)
        layout.addWidget(avail_group)

        # 習熟度（朝食/ディナー別）
        skill_group = QGroupBox("習熟度（ポジション・時間帯別）")
        skill_layout = QFormLayout(skill_group)
        skill_layout.setSpacing(10)

        self.hall_skill_breakfast_combo = QComboBox()
        self.hall_skill_dinner_combo = QComboBox()
        self.kitchen_skill_breakfast_combo = QComboBox()
        self.kitchen_skill_dinner_combo = QComboBox()
        for label, val in [("リーダー", SkillLevel.LEADER), ("ベテラン", SkillLevel.VETERAN),
                            ("メンバー", SkillLevel.GENERAL), ("ビギナー", SkillLevel.BEGINNER)]:
            self.hall_skill_breakfast_combo.addItem(label, val)
            self.hall_skill_dinner_combo.addItem(label, val)
            self.kitchen_skill_breakfast_combo.addItem(label, val)
            self.kitchen_skill_dinner_combo.addItem(label, val)
        for combo in (self.hall_skill_breakfast_combo, self.hall_skill_dinner_combo,
                      self.kitchen_skill_breakfast_combo, self.kitchen_skill_dinner_combo):
            combo.setCurrentIndex(3)
        skill_layout.addRow("ホール習熟度（朝食）", self.hall_skill_breakfast_combo)
        skill_layout.addRow("ホール習熟度（ディナー）", self.hall_skill_dinner_combo)
        skill_layout.addRow("キッチン習熟度（朝食）", self.kitchen_skill_breakfast_combo)
        skill_layout.addRow("キッチン習熟度（ディナー）", self.kitchen_skill_dinner_combo)
        layout.addWidget(skill_group)

        # ボタン
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QDialogButtonBox.StandardButton.Save).setText("保存")
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("キャンセル")
        buttons.accepted.connect(self._on_save)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

        self._on_type_changed()
        self._on_timeslot_changed()

    def _on_type_changed(self):
        self.adjustSize()

    def _on_timeslot_changed(self):
        # ディナー専任は朝食の開店準備・片付けを行わないため非表示にし、チェックも外す
        is_dinner_only = self.primary_ts_combo.currentData() == TimeSlot.DINNER
        self.timetable_group.setVisible(not is_dinner_only)
        if is_dinner_only:
            self.can_open_check.setChecked(False)
            self.can_cleanup_check.setChecked(False)
        self.adjustSize()

    def _load_employee(self, emp: Employee):
        self.name_edit.setText(emp.name)
        idx = self.emp_type_combo.findData(emp.employment_type)
        self.emp_type_combo.setCurrentIndex(idx)
        idx_pp = self.primary_pos_combo.findData(emp.primary_position)
        self.primary_pos_combo.setCurrentIndex(idx_pp if idx_pp >= 0 else 0)
        self.can_both_check.setChecked(emp.can_work_both_positions)
        self.can_open_check.setChecked(emp.can_open)
        self.can_cleanup_check.setChecked(emp.can_cleanup)
        self.always_avail_b_check.setChecked(emp.always_available_breakfast)
        self.always_avail_d_check.setChecked(emp.always_available_dinner)
        idx_pt = self.primary_ts_combo.findData(emp.primary_timeslot)
        self.primary_ts_combo.setCurrentIndex(idx_pt if idx_pt >= 0 else 0)

        idx_hb = self.hall_skill_breakfast_combo.findData(emp.hall_skill_breakfast)
        self.hall_skill_breakfast_combo.setCurrentIndex(idx_hb)
        idx_hd = self.hall_skill_dinner_combo.findData(emp.hall_skill_dinner)
        self.hall_skill_dinner_combo.setCurrentIndex(idx_hd)
        idx_kb = self.kitchen_skill_breakfast_combo.findData(emp.kitchen_skill_breakfast)
        self.kitchen_skill_breakfast_combo.setCurrentIndex(idx_kb)
        idx_kd = self.kitchen_skill_dinner_combo.findData(emp.kitchen_skill_dinner)
        self.kitchen_skill_dinner_combo.setCurrentIndex(idx_kd)

    def _on_save(self):
        name = self.name_edit.text().strip()
        if not name:
            QMessageBox.warning(self, "入力エラー", "氏名を入力してください")
            return

        emp_type             = self.emp_type_combo.currentData()
        primary_position     = self.primary_pos_combo.currentData()
        can_work_both        = self.can_both_check.isChecked()
        can_open             = self.can_open_check.isChecked()
        can_cleanup          = self.can_cleanup_check.isChecked()
        always_avail_b       = self.always_avail_b_check.isChecked()
        always_avail_d       = self.always_avail_d_check.isChecked()
        primary_timeslot     = self.primary_ts_combo.currentData()
        if primary_timeslot == TimeSlot.DINNER:
            can_open = False
            can_cleanup = False
        hall_skill_breakfast = self.hall_skill_breakfast_combo.currentData()
        hall_skill_dinner = self.hall_skill_dinner_combo.currentData()
        kitchen_skill_breakfast = self.kitchen_skill_breakfast_combo.currentData()
        kitchen_skill_dinner = self.kitchen_skill_dinner_combo.currentData()

        existing_id = self.employee.id if self.employee else None

        self.employee = Employee(
            id=existing_id,
            name=name,
            employment_type=emp_type,
            hall_skill_breakfast=hall_skill_breakfast,
            hall_skill_dinner=hall_skill_dinner,
            kitchen_skill_breakfast=kitchen_skill_breakfast,
            kitchen_skill_dinner=kitchen_skill_dinner,
            primary_position=primary_position,
            can_work_both_positions=can_work_both,
            can_open=can_open,
            can_cleanup=can_cleanup,
            always_available_breakfast=always_avail_b,
            always_available_dinner=always_avail_d,
            primary_timeslot=primary_timeslot,
        )
        self.accept()
