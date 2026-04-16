"""シフト表示・編集画面"""
from __future__ import annotations
from datetime import date
from collections import defaultdict
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QPushButton, QLabel,
    QComboBox, QTableWidget, QTableWidgetItem, QHeaderView,
    QGroupBox, QDialog, QListWidget, QListWidgetItem,
    QDialogButtonBox, QScrollArea, QSizePolicy, QFrame,
    QMessageBox, QAbstractItemView, QTabWidget
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

        # タブ（朝食 / ディナー）
        self.tab_widget = QTabWidget()
        self.table_b = self._make_table()  # 朝食
        self.table_d = self._make_table()  # ディナー
        self.tab_widget.addTab(self.table_b, "🌅 朝食")
        self.tab_widget.addTab(self.table_d, "🌆 ディナー")
        self.table_b.cellClicked.connect(
            lambda r, c: self._on_cell_clicked(r, c, self.table_b, TimeSlot.BREAKFAST))
        self.table_d.cellClicked.connect(
            lambda r, c: self._on_cell_clicked(r, c, self.table_d, TimeSlot.DINNER))
        layout.addWidget(self.tab_widget)

        self.status_label = QLabel("")
        layout.addWidget(self.status_label)

        self._apply_styles()

    def _make_table(self) -> QTableWidget:
        t = QTableWidget()
        t.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        t.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        t.verticalHeader().setVisible(False)
        t.setShowGrid(True)
        t.setAlternatingRowColors(False)
        return t

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
            for t in (self.table_b, self.table_d):
                t.setRowCount(0)
                t.setColumnCount(0)
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
        self._render_slot_table(self.table_b, TimeSlot.BREAKFAST)
        self._render_slot_table(self.table_d, TimeSlot.DINNER)

    def _render_slot_table(self, table: QTableWidget, slot: TimeSlot):
        dates = self._period.date_range()

        # 列: 氏名 + 日付ごと + 合計
        col_headers = ["氏名"]
        col_date_strs: list[str | None] = [None]
        for d in dates:
            dow = DAY_OF_WEEK_LABELS[d.weekday()]
            col_headers.append(f"{d.month}/{d.day}\n({dow})")
            col_date_strs.append(d.isoformat())
        col_headers.append("計")
        col_date_strs.append(None)

        # 行: 全従業員 + 集計行（ポジション別）
        rows_data: list[tuple] = [("employee", emp) for emp in self._employees]
        for pos in Position:
            rows_data.append(("summary", pos))

        table.setRowCount(len(rows_data))
        table.setColumnCount(len(col_headers))
        table.setHorizontalHeaderLabels(col_headers)
        table.setColumnWidth(0, 95)
        for c in range(1, len(col_headers) - 1):
            table.setColumnWidth(c, 50)
        table.setColumnWidth(len(col_headers) - 1, 36)
        table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Fixed)

        # 集計
        count_map: dict[tuple[str, str], int] = defaultdict(int)
        leader_map: dict[tuple[str, str], int] = defaultdict(int)
        for (emp_id, ds, slot_v), pos_v in self._assignments.items():
            if slot_v != slot.value:
                continue
            count_map[(ds, pos_v)] += 1
            emp = next((e for e in self._employees if e.id == emp_id), None)
            if emp and emp.is_leader(pos_v):
                leader_map[(ds, pos_v)] += 1

        for row_idx, (row_type, row_data) in enumerate(rows_data):
            if row_type == "employee":
                self._fill_emp_slot_row(table, row_idx, row_data, slot, col_date_strs)
            else:
                self._fill_summary_slot_row(table, row_idx, row_data, slot, col_date_strs, count_map, leader_map)
            table.setRowHeight(row_idx, 28)

    def _fill_emp_slot_row(self, table: QTableWidget, row: int, emp: Employee,
                           slot: TimeSlot, col_date_strs: list):
        skill_b = SKILL_BADGE.get(emp.hall_skill, "")
        skill_k = SKILL_BADGE.get(emp.kitchen_skill, "")
        pp = f"[{emp.primary_position.label()[:1]}]" if emp.primary_position else ""
        name_item = QTableWidgetItem(f"{emp.name}{pp}\nH:{skill_b} K:{skill_k}")
        name_item.setFont(QFont("", 9))
        table.setItem(row, 0, name_item)

        slot_v = slot.value
        total = 0
        for col_idx, ds in enumerate(col_date_strs):
            if ds is None:
                continue
            pos_v = self._assignments.get((emp.id, ds, slot_v))
            req = self._requests.get((emp.id, ds))
            can_work = (req[0] if slot == TimeSlot.BREAKFAST else req[1]) if req else False

            c = theme.c
            if pos_v:
                skill = emp.hall_skill if pos_v == "hall" else emp.kitchen_skill
                badge = SKILL_BADGE.get(skill, "")
                pos_label = "H" if pos_v == "hall" else "K"
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
            table.setItem(row, col_idx, item)

        total_item = QTableWidgetItem(str(total))
        total_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
        total_item.setFont(QFont("", 9, QFont.Weight.Bold))
        table.setItem(row, len(col_date_strs) - 1, total_item)

    def _fill_summary_slot_row(self, table: QTableWidget, row: int, pos: Position,
                               slot: TimeSlot, col_date_strs: list,
                               count_map: dict, leader_map: dict):
        label_item = QTableWidgetItem(pos.label())
        label_item.setFont(QFont("", 8, QFont.Weight.Bold))
        label_item.setBackground(QBrush(QColor(theme.c["surface2"])))
        table.setItem(row, 0, label_item)

        constraint = SHIFT_CONSTRAINTS.get((slot, pos), {})
        min_req = constraint.get("min", 0)
        min_leader = constraint.get("min_leader", 0)

        for col_idx, ds in enumerate(col_date_strs):
            if ds is None:
                continue
            cnt = count_map[(ds, pos.value)]
            ld = leader_map[(ds, pos.value)]
            text = f"{cnt}名\n★{ld}"
            item = QTableWidgetItem(text)
            item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            item.setFont(QFont("", 8))
            c = theme.c
            if cnt < min_req or ld < min_leader:
                item.setBackground(QBrush(QColor(c["status_err"])))
            elif cnt == min_req or ld == min_leader:
                item.setBackground(QBrush(QColor(c["status_warn"])))
            else:
                item.setBackground(QBrush(QColor(c["status_ok"])))
            table.setItem(row, col_idx, item)

        table.setItem(row, len(col_date_strs) - 1, QTableWidgetItem(""))

    def _on_cell_clicked(self, row: int, col: int, table: QTableWidget, slot: TimeSlot):
        item = table.item(row, col)
        if not item:
            return
        data = item.data(Qt.ItemDataRole.UserRole)
        if not data:
            return

        state, emp_id, ds, slot_v = data

        if state == "assigned":
            emp = next((e for e in self._employees if e.id == emp_id), None)
            if not emp:
                return
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
            emp = next((e for e in self._employees if e.id == emp_id), None)
            if not emp:
                return
            dlg = PositionSelectDialog(emp, ds, slot, self._employees, self._assignments, self._period.id, parent=self)
            if dlg.exec() == QDialog.DialogCode.Accepted and dlg.selected_position:
                from models.schedule import ShiftAssignment
                assignment = ShiftAssignment(emp_id, ds, slot, dlg.selected_position)
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
            leaders = 0
            for eid, d2, s2 in assignments:
                if d2 == ds and s2 == slot.value and assignments.get((eid, d2, s2)) == pos.value:
                    e = next((x for x in all_employees if x.id == eid), None)
                    if e and e.is_leader(pos.value):
                        leaders += 1

            emp_skill = emp.skill_for(pos.value)
            badge = SKILL_BADGE.get(emp_skill, "")
            btn = QPushButton(
                f"{pos.label()}  [{current}/{constraint.get('max',3)}名]  "
                f"★リーダー:{leaders}/{constraint.get('min_leader',1)}名  "
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
