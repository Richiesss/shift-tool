"""シフト表示・編集画面"""
from __future__ import annotations
from datetime import date
from collections import defaultdict
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QPushButton, QLabel,
    QComboBox, QTableWidget, QTableWidgetItem, QHeaderView,
    QGroupBox, QDialog, QListWidget, QListWidgetItem,
    QDialogButtonBox, QScrollArea, QSizePolicy, QFrame,
    QMessageBox, QAbstractItemView
)
from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QFont, QColor, QBrush
from db import repositories as repo
from models.employee import Employee
from models.schedule import ShiftAssignment
from utils.constants import (
    TimeSlot, Position, SkillLevel, DAY_OF_WEEK_LABELS,
    SHIFT_CONSTRAINTS
)
from utils.theme import theme

# 習熟度バッジ
SKILL_BADGE = {
    SkillLevel.LEADER: "★★",
    SkillLevel.VETERAN: "★",
    SkillLevel.GENERAL: "",
    SkillLevel.BEGINNER: "▼",
}

# セル色
COLOR_OK = QColor("#d1fae5")      # 緑：制約クリア
COLOR_WARN = QColor("#fef3c7")    # 黄：警告（最低人数ちょうど）
COLOR_ERROR = QColor("#fee2e2")   # 赤：制約違反
COLOR_HEADER_DAY = QColor("#eff6ff")
COLOR_WEEKEND_SAT = QColor("#dbeafe")
COLOR_WEEKEND_SUN = QColor("#fce7f3")


class ScheduleView(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._period = None
        self._employees: list[Employee] = []
        self._assignments: dict[tuple[int, str, str], str] = {}  # (emp_id, date, slot) -> position
        self._requests: dict[tuple[int, str], tuple[bool, bool]] = {}
        self._build_ui()
        self._load_periods()

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(10)

        # ヘッダー
        header = QHBoxLayout()
        title = QLabel("シフト表 確認・編集")
        title.setFont(QFont("", 16, QFont.Weight.Bold))
        header.addWidget(title)
        header.addStretch()

        self.period_combo = QComboBox()
        self.period_combo.setMinimumWidth(240)
        self.period_combo.currentIndexChanged.connect(self._on_period_changed)
        header.addWidget(QLabel("期間:"))
        header.addWidget(self.period_combo)

        self._btn_output = QPushButton("出力 →")
        self._btn_output.setFixedHeight(32)
        self._btn_output.clicked.connect(self._on_output)
        header.addWidget(self._btn_output)
        layout.addLayout(header)

        # 凡例
        legend = QHBoxLayout()
        self._status_legend_labels: list[tuple[QLabel, str]] = []
        for text, color_key in [("✅制約クリア", "status_ok"), ("⚠️最低人数ちょうど", "status_warn"), ("❌制約違反", "status_err")]:
            lbl = QLabel(text)
            self._status_legend_labels.append((lbl, color_key))
            legend.addWidget(lbl)
        legend.addStretch()
        legend.addWidget(QLabel("習熟度: ★★リーダー ★ベテラン ▼新人"))
        layout.addLayout(legend)

        # テーブル（スクロール可）
        self.table = QTableWidget()
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.cellClicked.connect(self._on_cell_clicked)
        self.table.verticalHeader().setVisible(False)
        self.table.setShowGrid(True)
        self.table.setAlternatingRowColors(False)
        layout.addWidget(self.table)

        # 凡例（人員数）
        self.status_label = QLabel("")
        layout.addWidget(self.status_label)

        self._apply_styles()

    def _apply_styles(self):
        c = theme.c
        self._btn_output.setStyleSheet(
            f"QPushButton {{ background:{c['purple']}; color:white; border-radius:5px; padding:0 14px; }}"
            f" QPushButton:hover {{ background:{c['purple_hover']}; }}"
        )
        self.status_label.setStyleSheet(f"color:{c['text2']}; font-size:11px;")
        for lbl, color_key in self._status_legend_labels:
            lbl.setStyleSheet(f"background:{c[color_key]}; border-radius:3px; padding:2px 8px; font-size:11px;")

    def apply_theme(self):
        self._apply_styles()
        self._render_table()

    def _load_periods(self):
        periods = repo.get_all_periods()
        self.period_combo.blockSignals(True)
        self.period_combo.clear()
        self.period_combo.addItem("（期間を選択）", None)
        for p in periods:
            self.period_combo.addItem(f"{p.start_date} 〜 {p.end_date}", p)
        self.period_combo.blockSignals(False)

    def refresh(self, period_id: int = None):
        self._load_periods()
        if period_id:
            for i in range(self.period_combo.count()):
                p = self.period_combo.itemData(i)
                if p and p.id == period_id:
                    self.period_combo.setCurrentIndex(i)
                    return
        self._render_table()

    def _on_period_changed(self, idx):
        self._period = self.period_combo.currentData()
        if not self._period:
            self.table.setRowCount(0)
            self.table.setColumnCount(0)
            return
        self._load_data()
        self._render_table()

    def _load_data(self):
        self._employees = repo.get_all_employees()
        assignments = repo.get_assignments(self._period.id)
        requests = repo.get_shift_requests(self._period.id)
        self._assignments = {
            (a.employee_id, a.date, a.time_slot.value): a.position.value
            for a in assignments
        }
        self._requests = {
            (r.employee_id, r.date): (r.breakfast, r.dinner)
            for r in requests
        }

    def _render_table(self):
        if not self._period or not self._employees:
            return

        dates = self._period.date_range()
        slots = list(TimeSlot)

        # 列: 氏名列 + (日付×スロット) 列 + 合計列
        col_headers = ["氏名"]
        col_meta = [None]  # (date_str, slot)
        for d in dates:
            for slot in slots:
                dow = DAY_OF_WEEK_LABELS[d.weekday()]
                col_headers.append(f"{d.month}/{d.day}\n({dow})\n{slot.short_label()}")
                col_meta.append((d.isoformat(), slot.value))
        col_headers.append("合計")
        col_meta.append(None)

        # 行: ホールグループ + キッチングループ + 集計行
        hall_emps = [e for e in self._employees if True]  # 全員表示
        all_emps = self._employees

        # 行構成: ホールヘッダー + 全従業員 + 人員数行×2スロット + キャチン区切り
        # シンプルに: 全従業員行 + 日付ごとの集計行
        rows_data = []  # (row_type, data)
        for emp in all_emps:
            rows_data.append(("employee", emp))
        # 集計行: 朝食・ディナー別
        for slot in slots:
            for pos in Position:
                rows_data.append(("summary", (slot, pos)))

        self.table.setRowCount(len(rows_data))
        self.table.setColumnCount(len(col_headers))
        self.table.setHorizontalHeaderLabels(col_headers)

        # 列幅
        self.table.setColumnWidth(0, 100)
        for c in range(1, len(col_headers) - 1):
            self.table.setColumnWidth(c, 58)
        self.table.setColumnWidth(len(col_headers) - 1, 50)

        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Fixed)

        # 集計計算
        count_map: dict[tuple[str, str, str], int] = defaultdict(int)
        skilled_map: dict[tuple[str, str, str], int] = defaultdict(int)
        for (emp_id, ds, slot_v), pos_v in self._assignments.items():
            count_map[(ds, slot_v, pos_v)] += 1
            emp = next((e for e in self._employees if e.id == emp_id), None)
            if emp and emp.is_skilled(pos_v):
                skilled_map[(ds, slot_v, pos_v)] += 1

        for row_idx, (row_type, row_data) in enumerate(rows_data):
            if row_type == "employee":
                emp = row_data
                self._fill_employee_row(row_idx, emp, dates, slots, col_meta)
            else:
                slot, pos = row_data
                self._fill_summary_row(row_idx, slot, pos, dates, col_meta, count_map, skilled_map)

        # 行の高さ
        for r in range(len(rows_data)):
            self.table.setRowHeight(r, 28)

    def _fill_employee_row(self, row, emp: Employee, dates, slots, col_meta):
        # 氏名セル
        skill_b = SKILL_BADGE.get(emp.hall_skill, "")
        skill_k = SKILL_BADGE.get(emp.kitchen_skill, "")
        name_text = f"{emp.name}\nH:{skill_b} K:{skill_k}"
        name_item = QTableWidgetItem(name_text)
        name_item.setFont(QFont("", 9))
        self.table.setItem(row, 0, name_item)

        total = 0
        for col_idx, meta in enumerate(col_meta):
            if meta is None:
                continue
            ds, slot_v = meta
            pos_v = self._assignments.get((emp.id, ds, slot_v))
            req = self._requests.get((emp.id, ds))
            can_work = (req[0] if slot_v == TimeSlot.BREAKFAST.value else req[1]) if req else False

            c = theme.c
            if pos_v:
                pos_label = "H" if pos_v == "hall" else "K"
                skill = emp.hall_skill if pos_v == "hall" else emp.kitchen_skill
                badge = SKILL_BADGE.get(skill, "")
                item = QTableWidgetItem(f"{pos_label}{badge}")
                item.setBackground(QBrush(QColor(c["cell_assigned"])))
                item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                item.setData(Qt.ItemDataRole.UserRole, ("assigned", emp.id, ds, slot_v))
                total += 1
            elif can_work:
                item = QTableWidgetItem("△")
                item.setBackground(QBrush(QColor(c["cell_available"])))
                item.setForeground(QBrush(QColor(c["cell_avail_text"])))
                item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                item.setData(Qt.ItemDataRole.UserRole, ("available", emp.id, ds, slot_v))
            else:
                item = QTableWidgetItem("")
                item.setBackground(QBrush(QColor(c["cell_unavail"])))
                item.setData(Qt.ItemDataRole.UserRole, ("unavailable", emp.id, ds, slot_v))

            self.table.setItem(row, col_idx, item)

        # 合計
        total_item = QTableWidgetItem(str(total))
        total_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
        total_item.setFont(QFont("", 9, QFont.Weight.Bold))
        self.table.setItem(row, len(col_meta) - 1, total_item)

    def _fill_summary_row(self, row, slot: TimeSlot, pos: Position, dates, col_meta, count_map, skilled_map):
        label = f"{slot.short_label()}\n{pos.label()}"
        label_item = QTableWidgetItem(label)
        label_item.setFont(QFont("", 8, QFont.Weight.Bold))
        label_item.setBackground(QBrush(QColor(theme.c["surface2"])))
        self.table.setItem(row, 0, label_item)

        key = (slot, pos)
        constraint = SHIFT_CONSTRAINTS.get(key, {})

        for col_idx, meta in enumerate(col_meta):
            if meta is None:
                continue
            ds, slot_v = meta
            if slot_v != slot.value:
                item = QTableWidgetItem("")
                item.setBackground(QBrush(QColor(theme.c["surface2"])))
                self.table.setItem(row, col_idx, item)
                continue

            cnt = count_map[(ds, slot.value, pos.value)]
            sk = skilled_map[(ds, slot.value, pos.value)]
            min_req = constraint.get("min", 0)
            max_req = constraint.get("max", 99)
            min_sk = constraint.get("min_skilled", 0)

            text = f"{cnt}名\n熟{sk}"
            item = QTableWidgetItem(text)
            item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            item.setFont(QFont("", 8))

            c = theme.c
            if cnt < min_req or sk < min_sk:
                item.setBackground(QBrush(QColor(c["status_err"])))
            elif cnt == min_req:
                item.setBackground(QBrush(QColor(c["status_warn"])))
            else:
                item.setBackground(QBrush(QColor(c["status_ok"])))

            self.table.setItem(row, col_idx, item)

        # 合計欄は空白
        self.table.setItem(row, len(col_meta) - 1, QTableWidgetItem(""))

    def _on_cell_clicked(self, row, col):
        item = self.table.item(row, col)
        if not item:
            return
        data = item.data(Qt.ItemDataRole.UserRole)
        if not data:
            return

        state, emp_id, ds, slot_v = data

        if state == "assigned":
            # クリックで削除確認
            emp = next((e for e in self._employees if e.id == emp_id), None)
            if not emp:
                return
            slot = TimeSlot(slot_v)
            pos_v = self._assignments.get((emp_id, ds, slot_v))
            reply = QMessageBox.question(
                self, "シフト削除",
                f"{emp.name} の {ds} {slot.short_label()} のアサインを削除しますか？",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
            )
            if reply == QMessageBox.StandardButton.Yes:
                repo.remove_assignment(self._period.id, emp_id, ds, slot)
                del self._assignments[(emp_id, ds, slot_v)]
                self._render_table()

        elif state == "available":
            # クリックでポジション選択 → 追加
            emp = next((e for e in self._employees if e.id == emp_id), None)
            if not emp:
                return
            dlg = PositionSelectDialog(emp, ds, TimeSlot(slot_v), self._employees, self._assignments, self._period.id, parent=self)
            if dlg.exec() == QDialog.DialogCode.Accepted and dlg.selected_position:
                from models.schedule import ShiftAssignment
                assignment = ShiftAssignment(emp_id, ds, TimeSlot(slot_v), dlg.selected_position)
                repo.add_assignment(self._period.id, assignment)
                self._assignments[(emp_id, ds, slot_v)] = dlg.selected_position.value
                self._render_table()

    def _on_output(self):
        if not self._period:
            return
        from ui.output_view import OutputDialog
        dlg = OutputDialog(self._period, self._employees, self._assignments, parent=self)
        dlg.exec()


class PositionSelectDialog(QDialog):
    def __init__(self, emp: Employee, ds: str, slot: TimeSlot, all_employees, assignments, period_id, parent=None):
        super().__init__(parent)
        self.selected_position = None
        self.setWindowTitle(f"{emp.name} のポジション選択")
        self.setFixedWidth(320)

        layout = QVBoxLayout(self)

        d = date.fromisoformat(ds)
        dow = DAY_OF_WEEK_LABELS[d.weekday()]
        layout.addWidget(QLabel(f"日付: {d.month}/{d.day}({dow})  {slot.short_label()}"))
        layout.addWidget(QLabel(f"従業員: {emp.name}"))
        layout.addWidget(QLabel(""))

        # 各ポジションの現在の状況
        for pos in Position:
            key = (slot, pos)
            constraint = SHIFT_CONSTRAINTS.get(key, {})
            current = sum(1 for (eid, d2, s2), p in assignments.items() if d2 == ds and s2 == slot.value and p == pos.value)
            skilled = 0
            for eid, d2, s2 in assignments:
                if d2 == ds and s2 == slot.value and assignments.get((eid, d2, s2)) == pos.value:
                    e = next((x for x in all_employees if x.id == eid), None)
                    if e and e.is_skilled(pos.value):
                        skilled += 1

            emp_skill = emp.skill_for(pos.value)
            badge = SKILL_BADGE.get(emp_skill, "")
            btn = QPushButton(
                f"{pos.label()}  [{current}/{constraint.get('max',3)}名]  "
                f"熟練:{skilled}/{constraint.get('min_skilled',1)}名  "
                f"自分の習熟度:{emp_skill.label()}{badge}"
            )
            btn.setFixedHeight(40)
            c = theme.c
            if current >= constraint.get("max", 3):
                btn.setEnabled(False)
                btn.setToolTip("上限人数に達しています")
                btn.setStyleSheet(f"color:{c['text2']};")
            else:
                btn.setStyleSheet(
                    f"QPushButton {{ text-align:left; padding:0 12px; border:1px solid {c['border2']}; border-radius:5px; }}"
                    f" QPushButton:hover {{ background:{c['surface2']}; }}"
                )
                btn.clicked.connect(lambda _, p=pos: self._select(p))
            layout.addWidget(btn)

        cancel_btn = QPushButton("キャンセル")
        cancel_btn.clicked.connect(self.reject)
        layout.addWidget(cancel_btn)

    def _select(self, pos: Position):
        self.selected_position = pos
        self.accept()
