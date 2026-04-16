"""シフト表示・編集画面"""
from __future__ import annotations
from datetime import date
from collections import defaultdict
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QPushButton, QLabel,
    QComboBox, QTableWidget, QTableWidgetItem, QHeaderView,
    QGroupBox, QDialog, QListWidget, QListWidgetItem,
    QDialogButtonBox, QScrollArea, QSizePolicy, QFrame,
    QMessageBox, QAbstractItemView, QTabWidget, QScrollBar
)
from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QFont, QColor, QBrush, QPainter, QPen, QFontMetrics
from db import repositories as repo
from models.employee import Employee
from models.schedule import ShiftAssignment, ShiftRequest
from utils.constants import (
    TimeSlot, Position, PrimaryPosition, SkillLevel, DAY_OF_WEEK_LABELS,
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
COLOR_OK = QColor("#d1fae5")
COLOR_WARN = QColor("#fef3c7")
COLOR_ERROR = QColor("#fee2e2")


class ScheduleView(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._period = None
        self._employees: list[Employee] = []
        self._assignments: dict[tuple[int, str, str], str] = {}
        self._requests: dict[tuple[int, str], tuple[bool, bool]] = {}
        self._raw_requests: dict[tuple[int, str], ShiftRequest] = {}
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
        for text, color_key in [
            ("✅制約クリア", "status_ok"),
            ("⚠️最低人数ちょうど", "status_warn"),
            ("❌制約違反", "status_err"),
        ]:
            lbl = QLabel(text)
            self._status_legend_labels.append((lbl, color_key))
            legend.addWidget(lbl)
        legend.addStretch()
        legend.addWidget(QLabel("習熟度: ★★リーダー ★ベテラン ▼新人"))
        layout.addLayout(legend)

        # タブ（朝食 / ディナー）
        self._show_other: dict[TimeSlot, bool] = {
            TimeSlot.BREAKFAST: False,
            TimeSlot.DINNER: False,
        }
        self.tab_widget = QTabWidget()
        (tab_b_w, self.table_b,
         self._btn_other_b, self._warn_label_b) = self._make_tab_widget(TimeSlot.BREAKFAST)
        (tab_d_w, self.table_d,
         self._btn_other_d, self._warn_label_d) = self._make_tab_widget(TimeSlot.DINNER)
        self.tab_widget.addTab(tab_b_w, "🌅 朝食")
        self.tab_widget.addTab(tab_d_w, "🌆 ディナー")
        layout.addWidget(self.tab_widget)

        self.status_label = QLabel("")
        layout.addWidget(self.status_label)

        self._apply_styles()

    def _make_tab_widget(self, slot: TimeSlot):
        """タブの中身 (QWidget, QTableWidget, toggle_btn, warn_label) を生成"""
        container = QWidget()
        vbox = QVBoxLayout(container)
        vbox.setContentsMargins(0, 6, 0, 0)
        vbox.setSpacing(4)

        # 他スロット専任メンバー表示トグル
        other_label = "ディナー専任" if slot == TimeSlot.BREAKFAST else "朝食専任"
        btn = QPushButton(f"　{other_label}メンバーも表示　")
        btn.setCheckable(True)
        btn.setFixedHeight(28)
        btn.toggled.connect(lambda checked, s=slot: self._on_toggle_other(s, checked))
        row_btn = QHBoxLayout()
        row_btn.addWidget(btn)
        row_btn.addStretch()
        vbox.addLayout(row_btn)

        # 制約違反警告ラベル
        warn_label = QLabel("")
        warn_label.setWordWrap(True)
        warn_label.setVisible(False)
        warn_label.setStyleSheet(
            "background:#fef2f2; border:1px solid #fca5a5; border-radius:4px; "
            "padding:4px 8px; font-size:11px;"
        )
        vbox.addWidget(warn_label)

        table = QTableWidget()
        table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        table.verticalHeader().setVisible(False)
        table.setShowGrid(True)
        table.setAlternatingRowColors(False)
        table.cellClicked.connect(
            lambda r, c, s=slot, t=table: self._on_cell_clicked(r, c, t, s))
        # 日付ヘッダークリック → タイムライン表示
        table.horizontalHeader().sectionClicked.connect(
            lambda col, s=slot, t=table: self._on_date_header_clicked(col, s, t))
        vbox.addWidget(table)

        return container, table, btn, warn_label

    def _on_toggle_other(self, slot: TimeSlot, checked: bool):
        self._show_other[slot] = checked
        table = self.table_b if slot == TimeSlot.BREAKFAST else self.table_d
        warn = self._warn_label_b if slot == TimeSlot.BREAKFAST else self._warn_label_d
        self._render_slot_table(table, slot, warn)

    def _apply_styles(self):
        c = theme.c
        self._btn_output.setStyleSheet(
            f"QPushButton {{ background:{c['purple']}; color:white; border-radius:5px; padding:0 14px; }}"
            f" QPushButton:hover {{ background:{c['purple_hover']}; }}"
        )
        self.status_label.setStyleSheet(f"color:{c['text2']}; font-size:11px;")
        for lbl, color_key in self._status_legend_labels:
            lbl.setStyleSheet(
                f"background:{c[color_key]}; border-radius:3px; padding:2px 8px; font-size:11px;"
            )
        for btn in (self._btn_other_b, self._btn_other_d):
            btn.setStyleSheet(
                f"QPushButton {{ background:{c['cell_other_slot']}; border:1px solid {c['border2']}; "
                f"border-radius:4px; font-size:11px; color:{c['text']}; }}"
                f" QPushButton:checked {{ background:{c['status_warn']}; border-color:{c['border2']}; }}"
            )

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
        self._raw_requests = {
            (r.employee_id, r.date): r
            for r in requests
        }

    def _render_table(self):
        if not self._period or not self._employees:
            return
        self._render_slot_table(self.table_b, TimeSlot.BREAKFAST, self._warn_label_b)
        self._render_slot_table(self.table_d, TimeSlot.DINNER, self._warn_label_d)

    def _render_slot_table(self, table: QTableWidget, slot: TimeSlot, warn_label: QLabel):
        dates = self._period.date_range()

        col_headers = ["氏名"]
        col_date_strs: list[str | None] = [None]
        for d in dates:
            dow = DAY_OF_WEEK_LABELS[d.weekday()]
            col_headers.append(f"{d.month}/{d.day}\n({dow})")
            col_date_strs.append(d.isoformat())
        col_headers.append("計")
        col_date_strs.append(None)

        # 従業員フィルタリング
        primary_emps = [e for e in self._employees
                        if e.primary_timeslot is None or e.primary_timeslot == slot]
        other_emps = [e for e in self._employees
                      if e.primary_timeslot is not None and e.primary_timeslot != slot]
        other_ids = {e.id for e in other_emps}
        show_other = self._show_other.get(slot, False)
        display_emps = primary_emps + (other_emps if show_other else [])

        # ポジション別グループ分け
        hall_emps = [e for e in display_emps
                     if e.primary_position is not None and e.primary_position.value == "hall"]
        any_emps  = [e for e in display_emps if e.primary_position is None]
        kit_emps  = [e for e in display_emps
                     if e.primary_position is not None and e.primary_position.value == "kitchen"]

        rows_data: list[tuple] = []
        for group_label, group_emps in [
            ("ホール専任", hall_emps),
            ("どちらでも", any_emps),
            ("キッチン専任", kit_emps),
        ]:
            if not group_emps:
                continue
            rows_data.append(("divider", group_label))
            for emp in group_emps:
                rows_data.append(("employee", emp))
        for pos in Position:
            rows_data.append(("summary", pos))

        table.setRowCount(len(rows_data))
        table.setColumnCount(len(col_headers))
        table.setHorizontalHeaderLabels(col_headers)
        table.setColumnWidth(0, 95)
        for c_idx in range(1, len(col_headers) - 1):
            table.setColumnWidth(c_idx, 50)
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
                self._fill_emp_slot_row(
                    table, row_idx, row_data, slot, col_date_strs, other_ids)
                table.setRowHeight(row_idx, 28)
            elif row_type == "divider":
                self._fill_divider_row(table, row_idx, row_data, len(col_headers))
                table.setRowHeight(row_idx, 18)
            else:
                self._fill_summary_slot_row(
                    table, row_idx, row_data, slot, col_date_strs, count_map, leader_map)
                table.setRowHeight(row_idx, 28)

        # 制約違反の警告更新
        self._update_constraint_warnings(warn_label, slot, dates, count_map, leader_map)

    def _fill_divider_row(self, table: QTableWidget, row: int, label: str, col_count: int):
        """ポジショングループの区切り行"""
        c = theme.c
        bg = QBrush(QColor(c["surface2"]))
        for col in range(col_count):
            item = QTableWidgetItem(label if col == 0 else "")
            item.setBackground(bg)
            item.setFont(QFont("", 8, QFont.Weight.Bold))
            item.setForeground(QBrush(QColor(c["text3"])))
            item.setFlags(Qt.ItemFlag.ItemIsEnabled)  # 選択・編集不可
            table.setItem(row, col, item)

    def _fill_emp_slot_row(self, table: QTableWidget, row: int, emp: Employee,
                           slot: TimeSlot, col_date_strs: list, other_ids: set = None):
        skill_b = SKILL_BADGE.get(emp.hall_skill, "")
        skill_k = SKILL_BADGE.get(emp.kitchen_skill, "")
        pp = f"[{emp.primary_position.label()[:1]}]" if emp.primary_position else ""
        is_other = other_ids and emp.id in other_ids
        other_slot = TimeSlot.DINNER if slot == TimeSlot.BREAKFAST else TimeSlot.BREAKFAST
        suffix = f" ↔{other_slot.short_label()}" if is_other else ""
        name_item = QTableWidgetItem(f"{emp.name}{pp}{suffix}\nH:{skill_b} K:{skill_k}")
        name_item.setFont(QFont("", 9))
        name_item.setFlags(Qt.ItemFlag.ItemIsEnabled)

        # ポジション専任で背景色を区別
        c = theme.c
        if is_other:
            name_item.setBackground(QBrush(QColor(c["cell_other_slot"])))
        elif emp.primary_position and emp.primary_position.value == "hall":
            name_item.setBackground(QBrush(QColor(c["cell_breakfast"])))
        elif emp.primary_position and emp.primary_position.value == "kitchen":
            name_item.setBackground(QBrush(QColor(c["cell_double"])))
        table.setItem(row, 0, name_item)

        slot_v = slot.value
        total = 0
        for col_idx, ds in enumerate(col_date_strs):
            if ds is None:
                continue
            pos_v = self._assignments.get((emp.id, ds, slot_v))
            req = self._requests.get((emp.id, ds))
            can_work = (req[0] if slot == TimeSlot.BREAKFAST else req[1]) if req else False

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
        total_item.setFlags(Qt.ItemFlag.ItemIsEnabled)
        table.setItem(row, len(col_date_strs) - 1, total_item)

    def _fill_summary_slot_row(self, table: QTableWidget, row: int, pos: Position,
                               slot: TimeSlot, col_date_strs: list,
                               count_map: dict, leader_map: dict):
        label_item = QTableWidgetItem(pos.label())
        label_item.setFont(QFont("", 8, QFont.Weight.Bold))
        label_item.setBackground(QBrush(QColor(theme.c["surface2"])))
        label_item.setFlags(Qt.ItemFlag.ItemIsEnabled)
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
            item.setFlags(Qt.ItemFlag.ItemIsEnabled)
            c = theme.c
            if cnt < min_req or ld < min_leader:
                item.setBackground(QBrush(QColor(c["status_err"])))
            elif cnt == min_req or ld == min_leader:
                item.setBackground(QBrush(QColor(c["status_warn"])))
            else:
                item.setBackground(QBrush(QColor(c["status_ok"])))
            table.setItem(row, col_idx, item)

        table.setItem(row, len(col_date_strs) - 1, QTableWidgetItem(""))

    def _update_constraint_warnings(self, warn_label: QLabel, slot: TimeSlot,
                                    dates: list, count_map: dict, leader_map: dict):
        """制約違反を集計してタブの警告ラベルに表示"""
        violations = []
        for d in dates:
            ds = d.isoformat()
            d_label = f"{d.month}/{d.day}({DAY_OF_WEEK_LABELS[d.weekday()]})"
            for pos in Position:
                cnt = count_map[(ds, pos.value)]
                ld = leader_map[(ds, pos.value)]
                constraint = SHIFT_CONSTRAINTS.get((slot, pos), {})
                min_req = constraint.get("min", 0)
                min_leader = constraint.get("min_leader", 0)
                if cnt < min_req:
                    violations.append(
                        f"❌ {d_label} {pos.label()}: {cnt}/{min_req}名不足"
                    )
                elif ld < min_leader:
                    violations.append(
                        f"⚠️ {d_label} {pos.label()}: リーダー{ld}/{min_leader}名不足"
                    )

        if violations:
            warn_label.setText("　".join(violations))
            warn_label.setVisible(True)
        else:
            warn_label.setVisible(False)

    def _on_date_header_clicked(self, col: int, slot: TimeSlot, table: QTableWidget):
        """日付列ヘッダークリック → その日のタイムライン表示"""
        if col == 0 or col >= table.columnCount() - 1:
            return  # 氏名列・計列は無視
        header_text = table.horizontalHeaderItem(col)
        if not header_text:
            return
        dates = self._period.date_range()
        date_idx = col - 1  # col=1 が dates[0]
        if date_idx < 0 or date_idx >= len(dates):
            return
        ds = dates[date_idx].isoformat()
        dlg = DayTimetableDialog(
            ds, self._employees, self._assignments,
            self._raw_requests, parent=self
        )
        dlg.exec()

    def _on_cell_clicked(self, row: int, col: int, table: QTableWidget, slot: TimeSlot):
        item = table.item(row, col)
        if not item:
            return
        data = item.data(Qt.ItemDataRole.UserRole)
        if not data or not isinstance(data, tuple) or data[0] not in ("assigned", "available"):
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
            dlg = PositionSelectDialog(
                emp, ds, slot, self._employees, self._assignments,
                self._period.id, parent=self
            )
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


# ── タイムラインダイアログ ─────────────────────────────────────────────

class DayTimetableDialog(QDialog):
    """1日分の希望シフトをタイムライン形式で表示するダイアログ"""

    def __init__(self, date_str: str, employees: list[Employee],
                 assignments: dict, raw_requests: dict, parent=None):
        super().__init__(parent)
        d = date.fromisoformat(date_str)
        dow = DAY_OF_WEEK_LABELS[d.weekday()]
        self.setWindowTitle(f"{d.month}月{d.day}日({dow}) シフト希望タイムライン")
        self.setMinimumWidth(750)
        self.setMinimumHeight(400)

        layout = QVBoxLayout(self)
        layout.setSpacing(8)

        # 説明ラベル
        info = QLabel(
            "■ 朝食帯(6-11時)  ■ ディナー帯(17-23時)  　"
            "濃色=アサイン済 / 薄色=希望のみ / 赤背景=不足時間帯"
        )
        info.setStyleSheet("font-size:11px; color:#6b7280;")
        layout.addWidget(info)

        # タイムラインウィジェット（スクロール可）
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)

        tl = _TimetableWidget(date_str, employees, assignments, raw_requests)
        scroll.setWidget(tl)
        layout.addWidget(scroll)

        # 凡例
        legend_row = QHBoxLayout()
        for color, text in [
            ("#3b82f6", "朝食アサイン"), ("#93c5fd", "朝食希望"),
            ("#ec4899", "ディナーアサイン"), ("#f9a8d4", "ディナー希望"),
            ("#22c55e", "ダブルアサイン"), ("#86efac", "ダブル希望"),
        ]:
            dot = QLabel("●")
            dot.setStyleSheet(f"color:{color}; font-size:16px;")
            legend_row.addWidget(dot)
            legend_row.addWidget(QLabel(text))
        legend_row.addStretch()
        layout.addLayout(legend_row)

        btn = QPushButton("閉じる")
        btn.clicked.connect(self.accept)
        layout.addWidget(btn)


class _TimetableWidget(QWidget):
    """1日分のシフトをタイムラインとして描画するカスタムウィジェット"""

    TIME_START = 5.0   # 5:00
    TIME_END   = 24.0  # 24:00
    MARGIN_LEFT = 110
    MARGIN_TOP  = 28
    MARGIN_RIGHT = 12
    ROW_H = 22
    COVERAGE_H = 48

    def __init__(self, date_str: str, employees: list[Employee],
                 assignments: dict, raw_requests: dict, parent=None):
        super().__init__(parent)
        self._date_str = date_str
        self._assignments = assignments
        self._rows = self._build_rows(employees, assignments, raw_requests, date_str)
        n = max(1, len(self._rows))
        total_h = self.MARGIN_TOP + n * self.ROW_H + self.COVERAGE_H + 16
        self.setMinimumHeight(total_h)
        self.setMinimumWidth(700)

    @staticmethod
    def _parse_hour(t: str) -> float:
        try:
            h, m = map(int, t.split(":"))
            return h + m / 60.0
        except Exception:
            return 0.0

    @classmethod
    def _build_rows(cls, employees, assignments, raw_requests, ds):
        from utils.shift_patterns import PATTERN_MAP
        rows = []
        for emp in employees:
            req = raw_requests.get((emp.id, ds))
            if not req:
                continue
            if not req.breakfast and not req.dinner:
                continue

            start_h, end_h = None, None
            force_both = False

            if req.pattern_id and req.pattern_id != "custom":
                p = PATTERN_MAP.get(req.pattern_id)
                if p:
                    if p.force_both:
                        force_both = True
                        start_h = 6.0
                        end_h = 23.0
                    else:
                        start_h = p.start_hour()
                        end_h = p.end_hour()
            elif req.pattern_id == "custom":
                if req.custom_start:
                    start_h = cls._parse_hour(req.custom_start)
                if req.custom_end:
                    end_h = cls._parse_hour(req.custom_end)

            if start_h is None:
                start_h = 6.0 if req.breakfast else 17.0
            if end_h is None:
                end_h = 11.0 if not req.dinner else 23.0

            assigned_b = (emp.id, ds, "breakfast") in {
                (eid, d2, s) for (eid, d2, s) in assignments if d2 == ds
            }
            assigned_d = (emp.id, ds, "dinner") in {
                (eid, d2, s) for (eid, d2, s) in assignments if d2 == ds
            }

            rows.append({
                "emp": emp,
                "start_h": start_h,
                "end_h": end_h,
                "breakfast": req.breakfast,
                "dinner": req.dinner,
                "force_both": force_both,
                "assigned_b": assigned_b,
                "assigned_d": assigned_d,
            })
        return rows

    def _x(self, hour: float, width: int) -> int:
        span = self.TIME_END - self.TIME_START
        usable = width - self.MARGIN_LEFT - self.MARGIN_RIGHT
        return self.MARGIN_LEFT + int((hour - self.TIME_START) / span * usable)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        W = self.width()
        c = theme.c

        bg = QColor(c["bg"])
        painter.fillRect(self.rect(), bg)

        # 時間帯ゾーン背景
        b_start_x = self._x(6,  W)
        b_end_x   = self._x(11, W)
        d_start_x = self._x(17, W)
        d_end_x   = self._x(23, W)
        zone_h = self.MARGIN_TOP + len(self._rows) * self.ROW_H + self.COVERAGE_H

        painter.fillRect(b_start_x, 0, b_end_x - b_start_x, zone_h, QColor("#eff6ff"))
        painter.fillRect(d_start_x, 0, d_end_x - d_start_x, zone_h, QColor("#fdf2f8"))

        # 時間軸
        painter.setPen(QPen(QColor(c["border"]), 1))
        font_sm = QFont("", 8)
        painter.setFont(font_sm)
        for h in range(int(self.TIME_START), int(self.TIME_END) + 1):
            x = self._x(h, W)
            painter.drawLine(x, self.MARGIN_TOP - 6, x, self.MARGIN_TOP + len(self._rows) * self.ROW_H + self.COVERAGE_H)
            painter.setPen(QColor(c["text2"]))
            painter.drawText(x - 10, self.MARGIN_TOP - 8, f"{h}:00")
            painter.setPen(QPen(QColor(c["border"]), 1))

        # 各従業員バー
        painter.setFont(QFont("", 9))
        for i, row in enumerate(self._rows):
            y = self.MARGIN_TOP + i * self.ROW_H
            cy = y + self.ROW_H // 2

            # 名前
            painter.setPen(QColor(c["text"]))
            painter.drawText(4, cy + 5, row["emp"].name)

            # バーの色決定
            is_b = row["breakfast"]
            is_d = row["dinner"]
            is_both = row["force_both"] or (is_b and is_d)
            assigned = row["assigned_b"] or row["assigned_d"]

            if is_both:
                bar_color = QColor("#22c55e") if assigned else QColor("#86efac")
            elif is_b:
                bar_color = QColor("#3b82f6") if row["assigned_b"] else QColor("#93c5fd")
            else:
                bar_color = QColor("#ec4899") if row["assigned_d"] else QColor("#f9a8d4")

            x1 = self._x(row["start_h"], W)
            x2 = self._x(row["end_h"], W)
            bar_h = self.ROW_H - 4
            painter.fillRect(x1, y + 2, max(2, x2 - x1), bar_h, bar_color)

            # バー上に時刻テキスト
            start_str = f"{int(row['start_h'])}:{int((row['start_h'] % 1) * 60):02d}"
            end_str   = f"{int(row['end_h'])}:{int((row['end_h'] % 1) * 60):02d}"
            painter.setPen(QColor("#ffffff") if assigned else QColor(c["text"]))
            painter.setFont(QFont("", 8))
            if x2 - x1 > 50:
                painter.drawText(x1 + 3, cy + 4, f"{start_str}〜{end_str}")
            painter.setFont(QFont("", 9))

        # カバレッジバー（下段）
        cov_y = self.MARGIN_TOP + len(self._rows) * self.ROW_H + 4
        painter.setPen(QColor(c["text2"]))
        painter.setFont(QFont("", 8))
        painter.drawText(4, cov_y + 14, "人数")

        # 1時間ごとの在籍人数を計算
        slot_hours = list(range(int(self.TIME_START), int(self.TIME_END)))
        max_count = max(
            sum(
                1 for row in self._rows
                if row["start_h"] <= h < row["end_h"]
            )
            for h in slot_hours
        ) if self._rows else 1

        # 朝食・ディナーの最小必要人数（ホール+キッチン合計）
        b_min = sum(SHIFT_CONSTRAINTS.get((TimeSlot.BREAKFAST, pos), {}).get("min", 0) for pos in Position)
        d_min = sum(SHIFT_CONSTRAINTS.get((TimeSlot.DINNER,    pos), {}).get("min", 0) for pos in Position)

        bar_area_h = self.COVERAGE_H - 20
        for h in slot_hours:
            count = sum(
                1 for row in self._rows
                if row["start_h"] <= h < row["end_h"]
            )
            x1 = self._x(h, W)
            x2 = self._x(h + 1, W)
            bar_px = int(count / max(max_count, 1) * bar_area_h) if count > 0 else 0

            # 不足判定
            is_b_zone = 6 <= h < 11
            is_d_zone = 17 <= h < 23
            required = b_min if is_b_zone else (d_min if is_d_zone else 0)
            shortage = required > 0 and count < required

            color = QColor("#ef4444") if shortage else QColor("#60a5fa")
            painter.fillRect(x1 + 1, cov_y + bar_area_h - bar_px + 4,
                             max(1, x2 - x1 - 2), bar_px, color)

            # 人数テキスト
            if count > 0:
                painter.setPen(QColor(c["text"]))
                painter.drawText(x1 + 2, cov_y + bar_area_h + 18, str(count))

        painter.end()


# ── ポジション選択ダイアログ ─────────────────────────────────────────────

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

        for pos in Position:
            key = (slot, pos)
            constraint = SHIFT_CONSTRAINTS.get(key, {})
            current = sum(
                1 for (eid, d2, s2), p in assignments.items()
                if d2 == ds and s2 == slot.value and p == pos.value
            )
            leaders = sum(
                1 for (eid, d2, s2), p in assignments.items()
                if d2 == ds and s2 == slot.value and p == pos.value
                and any(x.id == eid and x.is_leader(pos.value) for x in all_employees)
            )

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
