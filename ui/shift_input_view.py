"""希望シフト入力画面（シフトパターン選択版）"""
from __future__ import annotations
from datetime import date
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QPushButton, QLabel,
    QComboBox, QDateEdit, QTableWidget, QHeaderView,
    QCheckBox, QMessageBox, QGroupBox, QLineEdit, QSizePolicy,
    QFrame
)
from PyQt6.QtCore import Qt, QDate
from PyQt6.QtGui import QFont, QColor, QBrush, QKeySequence, QShortcut
from db import repositories as repo
from models.schedule import SchedulePeriod, ShiftRequest
from utils.constants import DAY_OF_WEEK_LABELS
from utils.shift_patterns import ALL_PATTERNS, PATTERN_MAP, default_pattern_from_fixed
from utils.theme import theme


# ドロップダウンに表示するパターン（カスタム含む）
_COMBO_ITEMS: list[tuple[str, str | None]] = [
    ("（休み）", None),
]
for _p in ALL_PATTERNS:
    _COMBO_ITEMS.append((_p.label, _p.id))


def _make_pattern_combo() -> QComboBox:
    """シフトパターン選択用 QComboBox を生成"""
    combo = QComboBox()
    combo.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
    for label, pid in _COMBO_ITEMS:
        combo.addItem(label, pid)
    return combo


class _PatternCellWidget(QWidget):
    """1行分のパターン選択ウィジェット（コンボ＋カスタム時刻入力）"""

    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(2, 1, 2, 1)
        layout.setSpacing(4)

        self.combo = _make_pattern_combo()
        self.combo.currentIndexChanged.connect(self._on_combo_changed)
        layout.addWidget(self.combo, stretch=1)

        self.custom_edit = QLineEdit()
        self.custom_edit.setPlaceholderText("例: 10:00〜15:00")
        self.custom_edit.setFixedWidth(130)
        self.custom_edit.setVisible(False)
        layout.addWidget(self.custom_edit)

    def _on_combo_changed(self):
        pid = self.combo.currentData()
        self.custom_edit.setVisible(pid == "custom")

    def set_pattern(self, pattern_id: str | None, custom_start: str | None = None,
                    custom_end: str | None = None):
        for i in range(self.combo.count()):
            if self.combo.itemData(i) == pattern_id:
                self.combo.setCurrentIndex(i)
                break
        if pattern_id == "custom":
            parts = []
            if custom_start:
                parts.append(custom_start)
            if custom_end:
                parts.append(custom_end)
            self.custom_edit.setText("〜".join(parts))
            self.custom_edit.setVisible(True)

    def get_pattern_id(self) -> str | None:
        return self.combo.currentData()

    def get_custom_times(self) -> tuple[str | None, str | None]:
        """カスタム時刻の (start, end) を返す。"HH:MM〜HH:MM" 形式を解析"""
        text = self.custom_edit.text().strip()
        if not text:
            return None, None
        # 区切り文字を統一
        text = text.replace("〜", "~").replace("～", "~").replace("-", "~").replace("―", "~")
        parts = [p.strip() for p in text.split("~") if p.strip()]
        start = parts[0] if len(parts) >= 1 else None
        end = parts[1] if len(parts) >= 2 else None
        return start, end

    def set_enabled(self, enabled: bool):
        self.combo.setEnabled(enabled)
        self.custom_edit.setEnabled(enabled)


class ShiftInputView(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._period: SchedulePeriod | None = None
        self._employees = []
        self._current_idx = 0
        self._requests: dict[tuple[int, str], ShiftRequest] = {}
        # date_str -> _PatternCellWidget
        self._pattern_cells: dict[str, _PatternCellWidget] = {}
        # date_str -> QLineEdit (備考)
        self._note_edits: dict[str, QLineEdit] = {}
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

        self._btn_set = QPushButton("期間確定")
        self._btn_set.setFixedHeight(32)
        self._btn_set.clicked.connect(self._on_set_period)
        period_layout.addWidget(self._btn_set)
        period_layout.addStretch()
        layout.addWidget(period_group)

        # 進捗
        self.progress_label = QLabel("")
        layout.addWidget(self.progress_label)

        # 従業員ナビ
        emp_nav = QHBoxLayout()
        self.emp_combo = QComboBox()
        self.emp_combo.setMinimumWidth(180)
        self.emp_combo.currentIndexChanged.connect(self._on_employee_changed)

        self._btn_prev = QPushButton("◀ 前の従業員")
        self._btn_next = QPushButton("次の従業員 ▶")
        self._btn_prev.clicked.connect(self._on_prev_employee)
        self._btn_next.clicked.connect(self._on_next_employee)
        for b in [self._btn_prev, self._btn_next]:
            b.setFixedHeight(32)

        emp_nav.addWidget(QLabel("従業員:"))
        emp_nav.addWidget(self.emp_combo)
        emp_nav.addStretch()
        emp_nav.addWidget(self._btn_prev)
        emp_nav.addWidget(self._btn_next)
        layout.addLayout(emp_nav)

        # パターン凡例
        legend = QHBoxLayout()
        legend.addWidget(QLabel("パターン例:"))
        self._legend_labels: list[tuple[QLabel, str]] = []
        for label, color_key in [
            ("朝食系", "cell_breakfast"), ("ディナー系", "cell_dinner"),
            ("通し/両対応", "cell_double"), ("カバーなし", "cell_none"),
        ]:
            lbl = QLabel(label)
            self._legend_labels.append((lbl, color_key))
            legend.addWidget(lbl)
        legend.addStretch()
        layout.addLayout(legend)

        # テーブル
        self.table = QTableWidget()
        self.table.setColumnCount(3)
        self.table.setHorizontalHeaderLabels(["日付", "シフトパターン", "備考"])
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.Fixed)
        self.table.setColumnWidth(2, 160)
        self.table.setEditTriggers(QTableWidget.EditTrigger.DoubleClicked
                                   | QTableWidget.EditTrigger.SelectedClicked)
        self.table.verticalHeader().setVisible(False)
        layout.addWidget(self.table)

        # 保存ボタン
        btn_row = QHBoxLayout()
        btn_row.addStretch()
        self.btn_save = QPushButton("この従業員の希望を保存")
        self.btn_save.setFixedHeight(36)
        self.btn_save.clicked.connect(self._on_save)
        btn_row.addWidget(self.btn_save)
        layout.addLayout(btn_row)

        self._apply_styles()
        self._setup_shortcuts()

    def _setup_shortcuts(self):
        """キーボードショートカット設定"""
        # 左右矢印キー: 従業員切り替え
        QShortcut(QKeySequence(Qt.Key.Key_Left),  self).activated.connect(self._on_prev_employee)
        QShortcut(QKeySequence(Qt.Key.Key_Right), self).activated.connect(self._on_next_employee)
        # 0〜9キー: QComboBox がフォーカスを持つときQShortcutが効かないため
        # アプリレベルのイベントフィルタで処理する
        from PyQt6.QtWidgets import QApplication
        QApplication.instance().installEventFilter(self)

    def eventFilter(self, obj, event):
        from PyQt6.QtCore import QEvent
        from PyQt6.QtWidgets import QApplication, QLineEdit
        if self.isVisible() and event.type() == QEvent.Type.KeyPress:
            focused = QApplication.focusWidget()
            # テキスト入力中（備考欄・カスタム時刻欄）は横取りしない
            if not isinstance(focused, QLineEdit):
                key = event.key()
                if Qt.Key.Key_0 <= key <= Qt.Key.Key_9:
                    idx = key - Qt.Key.Key_0
                    if idx < len(_COMBO_ITEMS):
                        self._select_pattern_by_key(idx)
                        return True
        return False

    def _select_pattern_by_key(self, combo_index: int):
        """現在選択中の行のシフトパターンをコンボインデックスで設定"""
        row = self.table.currentRow()
        if row < 0:
            return
        item = self.table.item(row, 0)
        if not item:
            return
        date_str = item.data(Qt.ItemDataRole.UserRole)
        cell = self._pattern_cells.get(date_str)
        if cell and cell.combo.isEnabled() and combo_index < cell.combo.count():
            cell.combo.setCurrentIndex(combo_index)
            self._apply_row_color(row, date_str, False)

    def _apply_styles(self):
        c = theme.c
        self._btn_set.setStyleSheet(
            f"QPushButton {{ background:{c['primary']}; color:white; border-radius:5px; padding:0 12px; }}"
            f" QPushButton:hover {{ background:{c['primary_hover']}; }}"
        )
        for b in [self._btn_prev, self._btn_next]:
            b.setStyleSheet(
                f"QPushButton {{ border:1px solid {c['border2']}; border-radius:5px; padding:0 12px; }}"
                f" QPushButton:hover {{ background:{c['surface2']}; }}"
            )
        self.btn_save.setStyleSheet(
            f"QPushButton {{ background:{c['success']}; color:white; border-radius:6px; padding:0 20px; font-weight:bold; }}"
            f" QPushButton:hover {{ background:{c['success_hover']}; }}"
        )
        self.progress_label.setStyleSheet(f"color:{c['text2']};")
        for lbl, color_key in self._legend_labels:
            lbl.setStyleSheet(
                f"background:{c[color_key]}; border-radius:3px; padding:1px 6px; font-size:11px;"
            )

    def apply_theme(self):
        self._apply_styles()
        self._render_table()

    # ── 期間 ──────────────────────────────────────────────────────────────

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
        for i in range(self.period_combo.count()):
            p = self.period_combo.itemData(i)
            if p and p.id == period.id:
                self.period_combo.setCurrentIndex(i)
                break
        self._load_employees()

    # ── 従業員 ────────────────────────────────────────────────────────────

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
        filled = set(
            r.employee_id for r in self._requests.values() if r.has_shift
        )
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

    # ── テーブル描画 ──────────────────────────────────────────────────────

    def _render_table(self):
        if not self._period or not self._employees:
            self.table.setRowCount(0)
            return

        emp = self._employees[self._current_idx]
        dates = self._period.date_range()
        self.table.setRowCount(len(dates))
        self._pattern_cells = {}
        self._note_edits = {}

        for row, d in enumerate(dates):
            date_str = d.isoformat()
            dow = d.weekday()
            dow_label = DAY_OF_WEEK_LABELS[dow]
            date_display = f"{d.month}/{d.day}({dow_label})"

            # ── 日付セル ──
            from PyQt6.QtWidgets import QTableWidgetItem
            date_item = QTableWidgetItem(date_display)
            date_item.setData(Qt.ItemDataRole.UserRole, date_str)
            date_item.setFlags(date_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
            c = theme.c
            if dow == 5:
                date_item.setForeground(QBrush(QColor(c["sat_text"])))
            elif dow == 6:
                date_item.setForeground(QBrush(QColor(c["sun_text"])))
            self.table.setItem(row, 0, date_item)

            # ── パターン選択セル ──
            is_unavail = date_str in emp.fixed_unavailable_dates
            req = self._requests.get((emp.id, date_str))

            cell = _PatternCellWidget()
            cell.set_enabled(not is_unavail)

            if is_unavail:
                # 固定不可日: 休みのまま
                cell.set_pattern(None)
                cell.combo.setToolTip("固定不可日")
            elif req is not None:
                # 保存済みデータを復元
                cell.set_pattern(req.pattern_id, req.custom_start, req.custom_end)
            elif emp.has_fixed_pattern():
                # 固定パターンからデフォルトを設定
                fp = emp.get_pattern(dow)
                if fp:
                    default_pid = default_pattern_from_fixed(fp.breakfast, fp.dinner)
                    cell.set_pattern(default_pid)

            self.table.setCellWidget(row, 1, cell)
            self._pattern_cells[date_str] = cell

            # ── 備考セル ──
            note_item = QTableWidgetItem(req.note if req else "")
            note_item.setFlags(note_item.flags() | Qt.ItemFlag.ItemIsEditable)
            self.table.setItem(row, 2, note_item)

            # 不可日はグレーアウト
            if is_unavail:
                date_item.setBackground(QBrush(QColor(theme.c["cell_none"])))
                note_item.setBackground(QBrush(QColor(theme.c["cell_none"])))

            # 行の背景色でパターン種別を視覚化
            self._apply_row_color(row, date_str, is_unavail)

            self.table.setRowHeight(row, 38)

    def _apply_row_color(self, row: int, date_str: str, is_unavail: bool):
        """選択パターンに応じて行背景を更新（変更時にも呼べるよう分離）"""
        if is_unavail:
            return
        cell = self._pattern_cells.get(date_str)
        if not cell:
            return
        pid = cell.get_pattern_id()
        if pid is None:
            return  # 休み: 色なし
        p = PATTERN_MAP.get(pid)
        if not p:
            return
        cb = p.covers_breakfast() if pid != "custom" else False
        cd = p.covers_dinner() if pid != "custom" else False

        c = theme.c
        if cb and cd:
            color = c["cell_double"]
        elif cb:
            color = c["cell_breakfast"]
        elif cd:
            color = c["cell_dinner"]
        else:
            color = c["cell_none"]

        from PyQt6.QtWidgets import QTableWidgetItem
        item = self.table.item(row, 0)
        if item:
            item.setBackground(QBrush(QColor(color)))

    # ── 保存 ──────────────────────────────────────────────────────────────

    def _on_save(self):
        if not self._period or not self._employees:
            return
        emp = self._employees[self._current_idx]
        dates = self._period.date_range()
        requests = []
        for row, d in enumerate(dates):
            date_str = d.isoformat()
            cell = self._pattern_cells.get(date_str)
            note_item = self.table.item(row, 2)
            note = note_item.text() if note_item else ""

            if cell:
                pid = cell.get_pattern_id()
                custom_start, custom_end = cell.get_custom_times() if pid == "custom" else (None, None)
                req = ShiftRequest(
                    employee_id=emp.id,
                    date=date_str,
                    pattern_id=pid,
                    custom_start=custom_start,
                    custom_end=custom_end,
                    note=note,
                )
                requests.append(req)
                self._requests[(emp.id, date_str)] = req

        repo.save_shift_requests(self._period.id, requests)
        self._update_progress()
        QMessageBox.information(self, "保存完了", f"「{emp.name}」の希望シフトを保存しました")
