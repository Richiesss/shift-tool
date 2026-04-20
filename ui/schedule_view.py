"""シフト表示・編集画面"""
from __future__ import annotations
from datetime import date
from collections import defaultdict
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QPushButton, QLabel,
    QComboBox, QTableWidget, QTableWidgetItem, QHeaderView,
    QDialog, QAbstractItemView, QTabWidget, QMessageBox,
    QScrollBar, QFrame
)
from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QFont, QColor, QBrush, QPainter, QPen
from db import repositories as repo
from models.employee import Employee
from models.schedule import ShiftAssignment, ShiftRequest
from utils.constants import (
    TimeSlot, Position, PrimaryPosition, SkillLevel,
    DAY_OF_WEEK_LABELS
)
from utils.theme import theme

SKILL_BADGE = {
    SkillLevel.LEADER:  "[L]",
    SkillLevel.VETERAN: "[V]",
    SkillLevel.GENERAL: "",
    SkillLevel.BEGINNER: "",
}

# _assignments value: (position_str, is_reinforcement_bool)
AssignVal = tuple[str, bool]


class ScheduleView(QWidget):
    period_changed     = pyqtSignal(int)  # item 1
    navigate_requested = pyqtSignal(int)  # item 4

    def __init__(self, parent=None):
        super().__init__(parent)
        self._period = None
        self._employees: list[Employee] = []
        # {(emp_id, date, slot_value): (position_value, is_reinforcement)}
        self._assignments: dict[tuple[int, str, str], AssignVal] = {}
        self._requests: dict[tuple[int, str], tuple[bool, bool]] = {}
        self._raw_requests: dict[tuple[int, str], ShiftRequest] = {}
        self._build_ui()
        self._load_periods()

    # ── UI構築 ─────────────────────────────────────────────────────────────

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(10)

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

        self._btn_confirm = QPushButton("✅ 確定する")
        self._btn_confirm.setFixedHeight(32)
        self._btn_confirm.setEnabled(False)
        self._btn_confirm.setToolTip("シフト表を確定します。確定後は編集がロックされます。")
        self._btn_confirm.clicked.connect(self._on_toggle_confirm)
        header.addWidget(self._btn_confirm)

        self._btn_output = QPushButton("出力 →")
        self._btn_output.setFixedHeight(32)
        self._btn_output.setToolTip("シフト表をCSV・PDF形式で出力します")
        self._btn_output.clicked.connect(self._on_output)
        header.addWidget(self._btn_output)
        layout.addLayout(header)

        # 凡例
        legend = QHBoxLayout()
        self._status_legend_labels: list[tuple[QLabel, str]] = []
        for text, color_key, tip in [
            ("✅制約クリア", "status_ok",   "人数・リーダー数がともに最低基準を超えています"),
            ("⚠️最低人数ちょうど", "status_warn", "人数またはリーダー数がちょうど最低基準です"),
            ("❌制約違反", "status_err",  "人数またはリーダー数が最低基準を下回っています"),
        ]:
            lbl = QLabel(text)
            lbl.setToolTip(tip)
            self._status_legend_labels.append((lbl, color_key))
            legend.addWidget(lbl)
        legend.addStretch()
        legend.addWidget(QLabel("習熟度: [L]リーダー [V]ベテラン　"))
        reinf_lbl = QLabel("■ 応援要員（希望外追加）")
        reinf_lbl.setToolTip("希望シフトを出していない従業員を手動で追加したアサインです")
        reinf_lbl.setStyleSheet("background:#fed7aa; border-radius:3px; padding:2px 6px; font-size:11px;")
        legend.addWidget(reinf_lbl)
        layout.addLayout(legend)

        # タブ
        self._show_other: dict[TimeSlot, bool] = {
            TimeSlot.BREAKFAST: False,
            TimeSlot.DINNER:    False,
        }
        self.tab_widget = QTabWidget()

        (tab_b, self.emp_table_b, self.sum_table_b,
         self._btn_other_b, self._warn_label_b) = self._make_tab_widget(TimeSlot.BREAKFAST)
        (tab_d, self.emp_table_d, self.sum_table_d,
         self._btn_other_d, self._warn_label_d) = self._make_tab_widget(TimeSlot.DINNER)

        self.tab_widget.addTab(tab_b, "🌅 朝食")
        self.tab_widget.addTab(tab_d, "🌆 ディナー")

        # 月間勤務集計タブ
        self._monthly_table = QTableWidget()
        self._monthly_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self._monthly_table.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        self._monthly_table.verticalHeader().setVisible(False)
        self._monthly_table.setAlternatingRowColors(True)
        self._monthly_table.setShowGrid(True)
        monthly_tab = QWidget()
        monthly_layout = QVBoxLayout(monthly_tab)
        monthly_layout.setContentsMargins(0, 6, 0, 0)
        monthly_layout.addWidget(self._monthly_table)
        self.tab_widget.addTab(monthly_tab, "📊 月間集計")

        layout.addWidget(self.tab_widget)

        self.status_label = QLabel("")
        layout.addWidget(self.status_label)

        self._apply_styles()

    def _make_tab_widget(self, slot: TimeSlot):
        """タブの中身を生成。(container, emp_table, sum_table, toggle_btn, warn_label) を返す"""
        container = QWidget()
        vbox = QVBoxLayout(container)
        vbox.setContentsMargins(0, 6, 0, 0)
        vbox.setSpacing(2)

        # 他スロット専任メンバー表示トグル
        other_label = "ディナー専任" if slot == TimeSlot.BREAKFAST else "朝食専任"
        btn = QPushButton(f"　{other_label}メンバーも表示　")
        btn.setCheckable(True)
        btn.setFixedHeight(28)
        btn.setToolTip(f"{other_label}の従業員をこのタブにも表示します（参考表示のみ）")
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

        # 従業員テーブル（スクロール可）
        emp_table = QTableWidget()
        emp_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        emp_table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        emp_table.verticalHeader().setVisible(False)
        emp_table.setShowGrid(True)
        emp_table.setAlternatingRowColors(False)
        # 垂直スクロールバーを常に表示して幅を一定に保つ
        emp_table.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOn)
        emp_table.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        emp_table.cellClicked.connect(
            lambda r, c, s=slot, t=emp_table: self._on_cell_clicked(r, c, t, s))
        emp_table.horizontalHeader().sectionClicked.connect(
            lambda col, s=slot, t=emp_table: self._on_date_header_clicked(col, s, t))
        emp_table.horizontalHeader().sectionResized.connect(
            lambda idx, old, new, s=slot: self._sync_col_width(idx, new, s))
        vbox.addWidget(emp_table, stretch=1)

        # 集計テーブル（常に下部に固定・スクロール非表示）
        sum_table = QTableWidget()
        sum_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        sum_table.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        sum_table.verticalHeader().setVisible(False)
        sum_table.horizontalHeader().setVisible(False)
        sum_table.setShowGrid(True)
        sum_table.setAlternatingRowColors(False)
        sum_table.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        sum_table.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        sum_table.setFixedHeight(60)  # 2行 × 28px + 余白
        vbox.addWidget(sum_table)

        # 水平スクロール同期
        emp_table.horizontalScrollBar().valueChanged.connect(
            sum_table.horizontalScrollBar().setValue)

        # 区切り線（集計テーブル上部）
        sep = QFrame()
        sep.setFrameShape(QFrame.Shape.HLine)
        vbox.insertWidget(vbox.count() - 1, sep)  # sum_tableの直前に挿入

        return container, emp_table, sum_table, btn, warn_label

    def _sync_col_width(self, col_idx: int, new_width: int, slot: TimeSlot):
        """従業員テーブルの列幅変更を集計テーブルに反映"""
        sum_t = self.sum_table_b if slot == TimeSlot.BREAKFAST else self.sum_table_d
        if col_idx < sum_t.columnCount():
            sum_t.setColumnWidth(col_idx, new_width)

    def _on_toggle_other(self, slot: TimeSlot, checked: bool):
        self._show_other[slot] = checked
        emp_t = self.emp_table_b if slot == TimeSlot.BREAKFAST else self.emp_table_d
        sum_t = self.sum_table_b if slot == TimeSlot.BREAKFAST else self.sum_table_d
        warn  = self._warn_label_b if slot == TimeSlot.BREAKFAST else self._warn_label_d
        self._render_slot_table(emp_t, sum_t, slot, warn)

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
        self._update_confirm_button()
        self._render_table()

    # ── データ読み込み ─────────────────────────────────────────────────────

    def _load_periods(self):
        from utils.period_fmt import fmt as _fmt
        periods = repo.get_all_periods()
        self.period_combo.blockSignals(True)
        self.period_combo.clear()
        self.period_combo.addItem("（期間を選択）", None)
        for p in periods:
            self.period_combo.addItem(_fmt(p.start_date, p.end_date), p)
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

    def set_period_by_id(self, period_id: int):
        """グローバル期間セレクターから呼ばれる同期メソッド（item 1）"""
        for i in range(self.period_combo.count()):
            p = self.period_combo.itemData(i)
            if p and p.id == period_id:
                if self.period_combo.currentIndex() != i:
                    self.period_combo.blockSignals(True)
                    self.period_combo.setCurrentIndex(i)
                    self.period_combo.blockSignals(False)
                    self._period = p
                    self._load_data()
                    self._render_table()
                    self._update_confirm_button()
                return

    def _on_period_changed(self, idx):
        self._period = self.period_combo.currentData()
        if not self._period:
            for t in (self.emp_table_b, self.emp_table_d,
                      self.sum_table_b, self.sum_table_d):
                t.setRowCount(0)
                t.setColumnCount(0)
            self._btn_confirm.setEnabled(False)
            return
        self._load_data()
        self._render_table()
        self._update_confirm_button()
        self.period_changed.emit(self._period.id)  # item 1

    def _load_data(self):
        self._employees = repo.get_all_employees()
        assignments = repo.get_assignments(self._period.id)
        requests = repo.get_shift_requests(self._period.id)
        self._assignments = {
            (a.employee_id, a.date, a.time_slot.value): (a.position.value, a.is_reinforcement)
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

    # ── 描画 ──────────────────────────────────────────────────────────────

    def _render_table(self):
        if not self._period or not self._employees:
            return
        self._render_slot_table(
            self.emp_table_b, self.sum_table_b, TimeSlot.BREAKFAST, self._warn_label_b)
        self._render_slot_table(
            self.emp_table_d, self.sum_table_d, TimeSlot.DINNER,    self._warn_label_d)
        self._render_monthly_tab()

    def _render_monthly_tab(self):
        if not self._period or not self._employees:
            self._monthly_table.setRowCount(0)
            return

        headers = ["氏名", "朝食(H)", "朝食(K)", "ディナー(H)", "ディナー(K)", "朝食計", "ディナー計", "合計", "応援"]
        self._monthly_table.setColumnCount(len(headers))
        self._monthly_table.setHorizontalHeaderLabels(headers)
        self._monthly_table.horizontalHeader().setSectionResizeMode(
            0, QHeaderView.ResizeMode.ResizeToContents)
        for ci in range(1, len(headers)):
            self._monthly_table.setColumnWidth(ci, 72)

        # emp_id -> {bh, bk, dh, dk, reinforcement}
        counts: dict[int, dict] = {
            e.id: {"bh": 0, "bk": 0, "dh": 0, "dk": 0, "reinf": 0}
            for e in self._employees
        }
        for (emp_id, ds, slot_v), (pos_v, is_reinf) in self._assignments.items():
            if emp_id not in counts:
                continue
            key = ("b" if slot_v == "breakfast" else "d") + ("h" if pos_v == "hall" else "k")
            counts[emp_id][key] += 1
            if is_reinf:
                counts[emp_id]["reinf"] += 1

        self._monthly_table.setRowCount(len(self._employees))
        c = theme.c
        for row, emp in enumerate(self._employees):
            d = counts[emp.id]
            bh, bk = d["bh"], d["bk"]
            dh, dk = d["dh"], d["dk"]
            b_total = bh + bk
            d_total = dh + dk
            total   = b_total + d_total
            reinf   = d["reinf"]

            vals = [emp.name, bh, bk, dh, dk, b_total, d_total, total, reinf]
            for ci, val in enumerate(vals):
                item = QTableWidgetItem(str(val))
                if ci > 0:
                    item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                if ci == 7 and total > 0:
                    item.setBackground(QBrush(QColor(c["cell_assigned"])))
                if ci == 8 and reinf > 0:
                    item.setBackground(QBrush(QColor(c["cell_reinforcement"])))
                item.setFlags(Qt.ItemFlag.ItemIsEnabled)
                self._monthly_table.setItem(row, ci, item)
            self._monthly_table.setRowHeight(row, 28)

    def _render_slot_table(self, emp_table: QTableWidget, sum_table: QTableWidget,
                           slot: TimeSlot, warn_label: QLabel):
        from datetime import date as _today_date
        today_str = _today_date.today().isoformat()

        dates = self._period.date_range()

        col_headers = ["氏名"]
        col_date_strs: list[str | None] = [None]
        for d in dates:
            dow = DAY_OF_WEEK_LABELS[d.weekday()]
            ds  = d.isoformat()
            # item 7: 今日ハイライト用マーカー
            prefix = "★" if ds == today_str else ""
            col_headers.append(f"{prefix}{d.month}/{d.day}\n({dow})")
            col_date_strs.append(ds)
        col_headers.append("計")
        col_date_strs.append(None)

        # 従業員フィルタリング
        primary_emps = [e for e in self._employees
                        if e.primary_timeslot is None or e.primary_timeslot == slot]
        other_emps   = [e for e in self._employees
                        if e.primary_timeslot is not None and e.primary_timeslot != slot]
        other_ids = {e.id for e in other_emps}
        show_other = self._show_other.get(slot, False)
        display_emps = primary_emps + (other_emps if show_other else [])

        # ポジション別グループ（ホール / キッチン）
        # 兼務可・未設定はホールグループに含める
        kit_emps  = [e for e in display_emps
                     if e.primary_position is not None and e.primary_position.value == "kitchen"]
        hall_emps = [e for e in display_emps if e not in kit_emps]

        emp_rows: list[tuple] = []
        for group_label, group_emps in [
            ("ホール", hall_emps),
            ("キッチン", kit_emps),
        ]:
            if not group_emps:
                continue
            emp_rows.append(("divider", group_label))
            for emp in group_emps:
                emp_rows.append(("employee", emp))

        # 集計
        count_map:  dict[tuple[str, str], int] = defaultdict(int)
        leader_map: dict[tuple[str, str], int] = defaultdict(int)
        for (emp_id, ds, slot_v), (pos_v, _) in self._assignments.items():
            if slot_v != slot.value:
                continue
            count_map[(ds, pos_v)] += 1
            emp = next((e for e in self._employees if e.id == emp_id), None)
            if emp and emp.is_leader(pos_v):
                leader_map[(ds, pos_v)] += 1

        n_cols = len(col_headers)

        # ── 従業員テーブル ────────────────────────────────────────────────
        emp_table.setRowCount(len(emp_rows))
        emp_table.setColumnCount(n_cols)
        emp_table.setHorizontalHeaderLabels(col_headers)
        emp_table.setColumnWidth(0, 95)
        for c_i in range(1, n_cols - 1):
            emp_table.setColumnWidth(c_i, 50)
        emp_table.setColumnWidth(n_cols - 1, 36)
        emp_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Fixed)

        # item 7: 今日の列ヘッダーをハイライト
        c = theme.c
        for c_i, ds in enumerate(col_date_strs):
            if ds == today_str:
                emp_table.horizontalHeaderItem(c_i).setBackground(
                    QBrush(QColor(c["primary"]))
                )
                emp_table.horizontalHeaderItem(c_i).setForeground(
                    QBrush(QColor("#ffffff"))
                )

        for row_idx, (row_type, row_data) in enumerate(emp_rows):
            if row_type == "employee":
                self._fill_emp_row(emp_table, row_idx, row_data, slot, col_date_strs, other_ids)
                emp_table.setRowHeight(row_idx, 28)
            else:
                self._fill_divider_row(emp_table, row_idx, row_data, n_cols)
                emp_table.setRowHeight(row_idx, 18)

        # ── 集計テーブル ──────────────────────────────────────────────────
        n_pos = len(list(Position))
        sum_table.setRowCount(n_pos)
        sum_table.setColumnCount(n_cols)
        sum_table.setColumnWidth(0, 95)
        for c_i in range(1, n_cols - 1):
            sum_table.setColumnWidth(c_i, 50)
        sum_table.setColumnWidth(n_cols - 1, 36)

        for pi, pos in enumerate(Position):
            self._fill_summary_row(sum_table, pi, pos, slot, col_date_strs, count_map, leader_map)
            sum_table.setRowHeight(pi, 28)

        # 警告更新
        self._update_constraint_warnings(warn_label, slot, dates, count_map, leader_map)

    def _fill_divider_row(self, table: QTableWidget, row: int, label: str, col_count: int):
        c = theme.c
        bg = QBrush(QColor(c["surface2"]))
        for col in range(col_count):
            item = QTableWidgetItem(label if col == 0 else "")
            item.setBackground(bg)
            item.setFont(QFont("", 8, QFont.Weight.Bold))
            item.setForeground(QBrush(QColor(c["text3"])))
            item.setFlags(Qt.ItemFlag.ItemIsEnabled)
            table.setItem(row, col, item)

    def _fill_emp_row(self, table: QTableWidget, row: int, emp: Employee,
                      slot: TimeSlot, col_date_strs: list, other_ids: set):
        skill_b = SKILL_BADGE.get(emp.hall_skill, "")
        skill_k = SKILL_BADGE.get(emp.kitchen_skill, "")
        pp_base = f"[{emp.primary_position.label()[:1]}]" if emp.primary_position else ""
        pp = f"{pp_base}[兼]" if emp.can_work_both_positions else pp_base
        is_other = emp.id in other_ids
        other_slot = TimeSlot.DINNER if slot == TimeSlot.BREAKFAST else TimeSlot.BREAKFAST
        suffix = f" ↔{other_slot.short_label()}" if is_other else ""
        name_item = QTableWidgetItem(f"{emp.name}{pp}{suffix}\nH:{skill_b} K:{skill_k}")
        name_item.setFont(QFont("", 9))
        name_item.setFlags(Qt.ItemFlag.ItemIsEnabled)

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
            result = self._assignments.get((emp.id, ds, slot_v))
            pos_v = result[0] if result else None
            is_reinf = result[1] if result else False
            req = self._requests.get((emp.id, ds))
            can_work = (req[0] if slot == TimeSlot.BREAKFAST else req[1]) if req else False

            if pos_v:
                skill = emp.hall_skill if pos_v == "hall" else emp.kitchen_skill
                badge = SKILL_BADGE.get(skill, "")
                pos_label = "H" if pos_v == "hall" else "K"
                prefix = "+" if is_reinf else ""
                item = QTableWidgetItem(f"{prefix}{pos_label}{badge}")
                bg_color = c["cell_reinforcement"] if is_reinf else c["cell_assigned"]
                item.setBackground(QBrush(QColor(bg_color)))
                item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                reinf_note = "（応援要員）" if is_reinf else ""
                item.setToolTip(f"クリックで削除　{pos_label}={('ホール' if pos_v=='hall' else 'キッチン')}{reinf_note}")
                item.setData(Qt.ItemDataRole.UserRole, ("assigned", emp.id, ds, slot_v))
                total += 1
            elif can_work:
                item = QTableWidgetItem("△")
                item.setBackground(QBrush(QColor(c["cell_available"])))
                item.setForeground(QBrush(QColor(c["cell_avail_text"])))
                item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                item.setToolTip("クリックでアサイン（希望あり）")
                item.setData(Qt.ItemDataRole.UserRole, ("available", emp.id, ds, slot_v))
            else:
                item = QTableWidgetItem("")
                item.setBackground(QBrush(QColor(c["cell_unavail"])))
                item.setToolTip("クリックで応援要員として追加（希望なし）")
                item.setData(Qt.ItemDataRole.UserRole, ("unavailable", emp.id, ds, slot_v))
            table.setItem(row, col_idx, item)

        total_item = QTableWidgetItem(str(total))
        total_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
        total_item.setFont(QFont("", 9, QFont.Weight.Bold))
        total_item.setFlags(Qt.ItemFlag.ItemIsEnabled)
        table.setItem(row, len(col_date_strs) - 1, total_item)

    def _fill_summary_row(self, table: QTableWidget, row: int, pos: Position,
                          slot: TimeSlot, col_date_strs: list,
                          count_map: dict, leader_map: dict):
        label_item = QTableWidgetItem(pos.label())
        label_item.setFont(QFont("", 8, QFont.Weight.Bold))
        label_item.setBackground(QBrush(QColor(theme.c["surface2"])))
        label_item.setFlags(Qt.ItemFlag.ItemIsEnabled)
        table.setItem(row, 0, label_item)

        shift_constraints = repo.get_shift_constraints()
        constraint = shift_constraints.get((slot, pos), {})
        min_req    = constraint.get("min", 0)
        min_leader = constraint.get("min_leader", 0)

        for col_idx, ds in enumerate(col_date_strs):
            if ds is None:
                continue
            cnt = count_map[(ds, pos.value)]
            ld  = leader_map[(ds, pos.value)]
            item = QTableWidgetItem(f"{cnt}名\n★{ld}")
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
        shift_constraints = repo.get_shift_constraints()
        violations = []
        for d in dates:
            ds = d.isoformat()
            d_label = f"{d.month}/{d.day}({DAY_OF_WEEK_LABELS[d.weekday()]})"
            for pos in Position:
                cnt = count_map[(ds, pos.value)]
                ld  = leader_map[(ds, pos.value)]
                constraint = shift_constraints.get((slot, pos), {})
                min_req    = constraint.get("min", 0)
                min_leader = constraint.get("min_leader", 0)
                if cnt < min_req:
                    violations.append(f"❌ {d_label} {pos.label()}: {cnt}/{min_req}名不足")
                elif ld < min_leader:
                    violations.append(f"⚠️ {d_label} {pos.label()}: ★{ld}/{min_leader}名不足")
        if violations:
            warn_label.setText("　".join(violations))
            warn_label.setVisible(True)
        else:
            warn_label.setVisible(False)

    # ── イベント ──────────────────────────────────────────────────────────

    def _on_date_header_clicked(self, col: int, slot: TimeSlot, table: QTableWidget):
        if col == 0 or col >= table.columnCount() - 1:
            return
        dates = self._period.date_range()
        date_idx = col - 1
        if date_idx < 0 or date_idx >= len(dates):
            return
        ds = dates[date_idx].isoformat()
        dlg = DayTimetableDialog(
            ds, self._employees, self._assignments, self._raw_requests, parent=self)
        dlg.exec()

    def _on_cell_clicked(self, row: int, col: int, table: QTableWidget, slot: TimeSlot):
        if self._period and self._period.status == "confirmed":
            return  # 確定済みは編集不可
        item = table.item(row, col)
        if not item:
            return
        data = item.data(Qt.ItemDataRole.UserRole)
        if not data or not isinstance(data, tuple):
            return

        state, emp_id, ds, slot_v = data

        if state == "assigned":
            emp = next((e for e in self._employees if e.id == emp_id), None)
            if not emp:
                return
            _, is_reinf = self._assignments.get((emp_id, ds, slot_v), ("", False))
            reinf_note = "（応援要員）" if is_reinf else ""
            reply = QMessageBox.question(
                self, "シフト削除",
                f"{emp.name}さん{reinf_note} の {ds} {slot.short_label()} のアサインを削除しますか？",
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
                self._period.id, is_reinforcement=False, parent=self
            )
            if dlg.exec() == QDialog.DialogCode.Accepted and dlg.selected_position:
                assignment = ShiftAssignment(emp_id, ds, slot, dlg.selected_position,
                                             is_reinforcement=False)
                repo.add_assignment(self._period.id, assignment)
                self._assignments[(emp_id, ds, slot_v)] = (dlg.selected_position.value, False)
                self._render_table()

        elif state == "unavailable":
            # 応援要員として追加
            emp = next((e for e in self._employees if e.id == emp_id), None)
            if not emp:
                return
            reply = QMessageBox.question(
                self, "応援要員追加",
                f"「{emp.name}さん」はこの時間帯の希望を出していませんが、\n"
                f"応援要員として {ds} {slot.short_label()} に追加しますか？",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
            )
            if reply != QMessageBox.StandardButton.Yes:
                return
            dlg = PositionSelectDialog(
                emp, ds, slot, self._employees, self._assignments,
                self._period.id, is_reinforcement=True, parent=self
            )
            if dlg.exec() == QDialog.DialogCode.Accepted and dlg.selected_position:
                assignment = ShiftAssignment(emp_id, ds, slot, dlg.selected_position,
                                             is_reinforcement=True)
                repo.add_assignment(self._period.id, assignment)
                self._assignments[(emp_id, ds, slot_v)] = (dlg.selected_position.value, True)
                self._render_table()

    def _update_confirm_button(self):
        if not self._period:
            self._btn_confirm.setEnabled(False)
            return
        self._btn_confirm.setEnabled(True)
        is_confirmed = (self._period.status == "confirmed")
        c = theme.c
        if is_confirmed:
            self._btn_confirm.setText("🔒 確定済み（解除する）")
            self._btn_confirm.setStyleSheet(
                f"QPushButton {{ background:{c['surface2']}; border:1px solid {c['border2']}; "
                f"border-radius:5px; padding:0 14px; color:{c['text2']}; }}"
                f" QPushButton:hover {{ background:{c['danger_bg']}; color:{c['danger_text']}; }}"
            )
        else:
            self._btn_confirm.setText("✅ 確定する")
            self._btn_confirm.setStyleSheet(
                f"QPushButton {{ background:#16a34a; color:white; border-radius:5px; padding:0 14px; }}"
                f" QPushButton:hover {{ background:#15803d; }}"
            )

    def _on_toggle_confirm(self):
        if not self._period:
            return
        is_confirmed = (self._period.status == "confirmed")
        if is_confirmed:
            reply = QMessageBox.question(
                self, "確定解除",
                "このシフト表の確定を解除しますか？\n解除後は再び編集可能になります。",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
            )
            if reply != QMessageBox.StandardButton.Yes:
                return
            self._period.status = "draft"
        else:
            reply = QMessageBox.question(
                self, "シフト確定",
                "このシフト表を確定しますか？\n確定後は編集がロックされます。",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
            )
            if reply != QMessageBox.StandardButton.Yes:
                return
            self._period.status = "confirmed"
        repo.save_period(self._period)
        self._update_confirm_button()
        self._render_table()

    def _on_output(self):
        if not self._period:
            return
        from ui.output_view import OutputDialog
        dlg = OutputDialog(self._period, self._employees, self._assignments, parent=self)
        dlg.exec()


# ── タイムラインダイアログ ─────────────────────────────────────────────

class DayTimetableDialog(QDialog):
    def __init__(self, date_str: str, employees: list[Employee],
                 assignments: dict, raw_requests: dict, parent=None):
        super().__init__(parent)
        d = date.fromisoformat(date_str)
        dow = DAY_OF_WEEK_LABELS[d.weekday()]
        self.setWindowTitle(f"{d.month}月{d.day}日({dow}) シフト希望タイムライン")
        self.setMinimumWidth(750)
        self.setMinimumHeight(400)

        from PyQt6.QtWidgets import QScrollArea
        layout = QVBoxLayout(self)
        layout.setSpacing(8)

        info = QLabel(
            "■ 朝食帯(6-11時)  ■ ディナー帯(17-23時)　"
            "濃色=アサイン済 / 薄色=希望のみ / 赤バー=不足時間帯"
        )
        info.setStyleSheet("font-size:11px; color:#6b7280;")
        layout.addWidget(info)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        tl = _TimetableWidget(date_str, employees, assignments, raw_requests)
        scroll.setWidget(tl)
        layout.addWidget(scroll)

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
    TIME_START = 5.0
    TIME_END   = 24.0
    MARGIN_LEFT  = 110
    MARGIN_TOP   = 28
    MARGIN_RIGHT = 12
    ROW_H       = 22
    COVERAGE_H  = 52

    def __init__(self, date_str, employees, assignments, raw_requests, parent=None):
        super().__init__(parent)
        self._date_str = date_str
        self._assignments = assignments
        self._rows = self._build_rows(employees, assignments, raw_requests, date_str)
        # paintEvent から DB アクセスしないようコンストラクタでキャッシュ
        _sc = repo.get_shift_constraints()
        self._b_min = sum(_sc.get((TimeSlot.BREAKFAST, pos), {}).get("min", 0) for pos in Position)
        self._d_min = sum(_sc.get((TimeSlot.DINNER,    pos), {}).get("min", 0) for pos in Position)
        n = max(1, len(self._rows))
        self.setMinimumHeight(self.MARGIN_TOP + n * self.ROW_H + self.COVERAGE_H + 20)
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
        assigned_keys = {(eid, d2, s) for (eid, d2, s) in assignments if d2 == ds}
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
                        start_h, end_h = 6.0, 23.0
                    else:
                        start_h = p.start_hour()
                        end_h   = p.end_hour()
            elif req.pattern_id == "custom":
                start_h = cls._parse_hour(req.custom_start) if req.custom_start else 6.0
                end_h   = cls._parse_hour(req.custom_end)   if req.custom_end   else 23.0
            if start_h is None:
                start_h = 6.0 if req.breakfast else 17.0
            if end_h is None:
                end_h = 11.0 if not req.dinner else 23.0
            rows.append({
                "emp": emp,
                "start_h": start_h, "end_h": end_h,
                "breakfast": req.breakfast, "dinner": req.dinner,
                "force_both": force_both,
                "assigned_b": (emp.id, ds, "breakfast") in assigned_keys,
                "assigned_d": (emp.id, ds, "dinner")    in assigned_keys,
            })
        return rows

    def _x(self, hour: float, width: int) -> int:
        usable = width - self.MARGIN_LEFT - self.MARGIN_RIGHT
        return self.MARGIN_LEFT + int((hour - self.TIME_START) / (self.TIME_END - self.TIME_START) * usable)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        W = self.width()
        c = theme.c
        painter.fillRect(self.rect(), QColor(c["bg"]))

        # ゾーン背景
        zone_h = self.MARGIN_TOP + len(self._rows) * self.ROW_H + self.COVERAGE_H
        painter.fillRect(self._x(6,  W), 0, self._x(11, W) - self._x(6,  W), zone_h, QColor("#eff6ff"))
        painter.fillRect(self._x(17, W), 0, self._x(23, W) - self._x(17, W), zone_h, QColor("#fdf2f8"))

        # 時間グリッド
        painter.setFont(QFont("", 8))
        for h in range(int(self.TIME_START), int(self.TIME_END) + 1):
            x = self._x(h, W)
            painter.setPen(QPen(QColor(c["border"]), 1))
            painter.drawLine(x, self.MARGIN_TOP - 6, x, zone_h)
            painter.setPen(QColor(c["text2"]))
            painter.drawText(x - 10, self.MARGIN_TOP - 8, f"{h}:00")

        # 従業員バー
        painter.setFont(QFont("", 9))
        for i, row in enumerate(self._rows):
            y  = self.MARGIN_TOP + i * self.ROW_H
            cy = y + self.ROW_H // 2
            painter.setPen(QColor(c["text"]))
            painter.drawText(4, cy + 5, row["emp"].name)

            is_both = row["force_both"] or (row["breakfast"] and row["dinner"])
            assigned = row["assigned_b"] or row["assigned_d"]
            if is_both:
                bar_color = QColor("#22c55e") if assigned else QColor("#86efac")
            elif row["breakfast"]:
                bar_color = QColor("#3b82f6") if row["assigned_b"] else QColor("#93c5fd")
            else:
                bar_color = QColor("#ec4899") if row["assigned_d"] else QColor("#f9a8d4")

            x1 = self._x(row["start_h"], W)
            x2 = self._x(row["end_h"],   W)
            painter.fillRect(x1, y + 2, max(2, x2 - x1), self.ROW_H - 4, bar_color)
            if x2 - x1 > 50:
                sh = int(row["start_h"]); sm = int((row["start_h"] % 1) * 60)
                eh = int(row["end_h"]);   em = int((row["end_h"]   % 1) * 60)
                painter.setPen(QColor("#ffffff") if assigned else QColor(c["text"]))
                painter.setFont(QFont("", 8))
                painter.drawText(x1 + 3, cy + 4, f"{sh}:{sm:02d}〜{eh}:{em:02d}")
                painter.setFont(QFont("", 9))

        # カバレッジバー
        cov_y = self.MARGIN_TOP + len(self._rows) * self.ROW_H + 4
        painter.setPen(QColor(c["text2"]))
        painter.setFont(QFont("", 8))
        painter.drawText(4, cov_y + 14, "人数")

        slot_hours = list(range(int(self.TIME_START), int(self.TIME_END)))
        all_counts = [
            sum(1 for r in self._rows if r["start_h"] <= h < r["end_h"])
            for h in slot_hours
        ]
        max_count = max(all_counts) if all_counts else 1
        b_min = self._b_min
        d_min = self._d_min
        bar_h = self.COVERAGE_H - 22

        for h, count in zip(slot_hours, all_counts):
            x1 = self._x(h, W)
            x2 = self._x(h + 1, W)
            bar_px = int(count / max(max_count, 1) * bar_h) if count > 0 else 0
            is_b_zone = 6 <= h < 11
            is_d_zone = 17 <= h < 23
            required  = b_min if is_b_zone else (d_min if is_d_zone else 0)
            color = QColor("#ef4444") if (required > 0 and count < required) else QColor("#60a5fa")
            if bar_px > 0:
                painter.fillRect(x1 + 1, cov_y + bar_h - bar_px + 4,
                                 max(1, x2 - x1 - 2), bar_px, color)
            if count > 0:
                painter.setPen(QColor(c["text"]))
                painter.drawText(x1 + 2, cov_y + bar_h + 18, str(count))

        painter.end()


# ── ポジション選択ダイアログ ──────────────────────────────────────────────

class PositionSelectDialog(QDialog):
    def __init__(self, emp: Employee, ds: str, slot: TimeSlot,
                 all_employees, assignments: dict, period_id: int,
                 is_reinforcement: bool = False, parent=None):
        super().__init__(parent)
        self.selected_position = None
        suffix = "（応援要員）" if is_reinforcement else ""
        self.setWindowTitle(f"{emp.name}さんのポジション選択{suffix}")
        self.setFixedWidth(340)

        layout = QVBoxLayout(self)

        d = date.fromisoformat(ds)
        dow = DAY_OF_WEEK_LABELS[d.weekday()]
        layout.addWidget(QLabel(f"日付: {d.month}/{d.day}({dow})  {slot.short_label()}"))
        layout.addWidget(QLabel(f"従業員: {emp.name}さん"))
        if is_reinforcement:
            warn = QLabel("⚠️ この従業員は希望を出していません（応援要員として追加）")
            warn.setStyleSheet("color:#b45309; font-size:11px;")
            layout.addWidget(warn)
        layout.addWidget(QLabel(""))

        _shift_constraints = repo.get_shift_constraints()
        for pos in Position:
            constraint = _shift_constraints.get((slot, pos), {})
            current = sum(
                1 for (eid, d2, s2), (p, _) in assignments.items()
                if d2 == ds and s2 == slot.value and p == pos.value
            )
            leaders = sum(
                1 for (eid, d2, s2), (p, _) in assignments.items()
                if d2 == ds and s2 == slot.value and p == pos.value
                and any(x.id == eid and x.is_leader(pos.value) for x in all_employees)
            )
            emp_skill = emp.skill_for(pos.value)
            badge = SKILL_BADGE.get(emp_skill, "")
            btn = QPushButton(
                f"{pos.label()}  [{current}/{constraint.get('max',3)}名]  "
                f"リーダー:{leaders}/{constraint.get('min_leader',1)}名  "
                f"自分:{emp_skill.label()}{badge}"
            )
            btn.setFixedHeight(40)
            c = theme.c
            if not is_reinforcement and current >= constraint.get("max", 3):
                btn.setEnabled(False)
                btn.setToolTip("上限人数に達しています")
                btn.setStyleSheet(f"color:{c['text2']};")
            else:
                btn.setStyleSheet(
                    f"QPushButton {{ text-align:left; padding:0 12px; "
                    f"border:1px solid {c['border2']}; border-radius:5px; }}"
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
