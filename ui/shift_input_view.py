"""希望シフト入力画面（シフトパターン選択版）"""
from __future__ import annotations
from datetime import date
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QPushButton, QLabel,
    QComboBox, QDateEdit, QTableWidget, QHeaderView,
    QCheckBox, QMessageBox, QGroupBox, QLineEdit, QSizePolicy,
    QFrame
)
from PyQt6.QtCore import Qt, QDate, pyqtSignal
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


class _ShiftComboBox(QComboBox):
    """マウスホイールで選択変更しない / 数字キーでパターン直接選択"""

    # 0〜9 キーが押されたときにコンボインデックスを通知
    number_pressed = pyqtSignal(int)

    def wheelEvent(self, event):
        event.ignore()  # ホバー中のスクロールを無視

    def keyPressEvent(self, event):
        key = event.key()
        # テンキー（KeypadModifier）は除外し、上段の 0〜9 のみ処理
        if (Qt.Key.Key_0 <= key <= Qt.Key.Key_9
                and not (event.modifiers() & Qt.KeyboardModifier.KeypadModifier)):
            self.number_pressed.emit(key - Qt.Key.Key_0)
            event.accept()
            return
        super().keyPressEvent(event)


def _make_pattern_combo() -> _ShiftComboBox:
    """シフトパターン選択用 QComboBox を生成"""
    combo = _ShiftComboBox()
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
    period_changed    = pyqtSignal(int)   # 期間変更を MainWindow に通知 (item 1)
    navigate_requested = pyqtSignal(int)  # 空状態ナビ要求 (item 4)

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
        self._saved_snapshot: dict = {}   # 保存済み状態のスナップショット
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
        self._btn_set.setToolTip("入力した日付範囲で新しいシフト期間を作成します")
        self._btn_set.clicked.connect(self._on_set_period)
        period_layout.addWidget(self._btn_set)

        self._btn_del_period = QPushButton("期間削除")
        self._btn_del_period.setFixedHeight(32)
        self._btn_del_period.setEnabled(False)
        self._btn_del_period.setToolTip("選択中の期間と関連するすべてのデータを削除します")
        self._btn_del_period.clicked.connect(self._on_delete_period)
        period_layout.addWidget(self._btn_del_period)

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
        self._btn_prev.setToolTip("前の従業員に切り替えます（← キー）")
        self._btn_next.setToolTip("次の従業員に切り替えます（→ キー）")
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

        # 過去の希望パターン行
        history_row = QHBoxLayout()
        history_row.setSpacing(6)
        hist_lbl = QLabel("よく使うパターン:")
        hist_lbl.setStyleSheet("color:#6b7280; font-size:11px;")
        history_row.addWidget(hist_lbl)
        # パターンボタンを格納するコンテナ（_refresh_history で動的に更新）
        self._history_container = QWidget()
        self._history_inner = QHBoxLayout(self._history_container)
        self._history_inner.setContentsMargins(0, 0, 0, 0)
        self._history_inner.setSpacing(6)
        history_row.addWidget(self._history_container)
        history_row.addStretch()

        self._btn_forms_import = QPushButton("Google Forms CSV をインポート")
        self._btn_forms_import.setFixedHeight(28)
        self._btn_forms_import.setEnabled(False)
        self._btn_forms_import.setToolTip(
            "Google Forms の回答 CSV を読み込んで全スタッフの希望を一括入力します\n"
            "「Google Forms 設定方法」タブでフォームのテンプレートを確認できます"
        )
        self._btn_forms_import.clicked.connect(self._on_forms_import)
        history_row.addWidget(self._btn_forms_import)
        layout.addLayout(history_row)

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
        # item 8: ツールチップ
        self.table.horizontalHeaderItem(1).setToolTip(
            "その日の出勤パターンを選択します。\n"
            "数字キー(0〜9)でも選択できます。\n"
            "「カスタム」を選ぶと時刻を直接入力できます。"
        )
        self.table.horizontalHeaderItem(2).setToolTip("メモを入力できます（任意）")
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
        self.btn_save.setToolTip("現在の従業員のシフト希望を保存します（Ctrl+S）")
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
        # Ctrl+S: 保存 (item 9)
        QShortcut(QKeySequence("Ctrl+S"), self).activated.connect(self._on_save)
        # 0〜9キー:
        #   ・コンボにフォーカスがある場合 → _ShiftComboBox.keyPressEvent が直接処理
        #   ・日付セル等にフォーカスがある場合 → テーブルへのフィルタで処理
        self.table.installEventFilter(self)

    def eventFilter(self, obj, event):
        """テーブルにフォーカスがある状態での数字キーを処理"""
        from PyQt6.QtCore import QEvent
        if obj is self.table and event.type() == QEvent.Type.KeyPress:
            key = event.key()
            if (Qt.Key.Key_0 <= key <= Qt.Key.Key_9
                    and not (event.modifiers() & Qt.KeyboardModifier.KeypadModifier)):
                idx = key - Qt.Key.Key_0
                if idx < len(_COMBO_ITEMS):
                    self._select_pattern_for_row(self.table.currentRow(), idx)
                    return True
        return False

    def _select_pattern_for_row(self, row: int, combo_index: int):
        """指定行のシフトパターンをコンボインデックスで設定"""
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

    def _on_combo_number_key(self, combo_index: int, date_str: str, row: int):
        """コンボが number_pressed シグナルを emit したときの処理"""
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
        self._btn_forms_import.setStyleSheet(
            f"QPushButton {{ border:1px solid {c['primary']}; border-radius:4px; "
            f"padding:0 10px; color:{c['primary']}; background:{c['surface']}; }}"
            f" QPushButton:hover {{ background:{c['surface2']}; }}"
            f" QPushButton:disabled {{ color:{c['text2']}; border-color:{c['border2']}; }}"
        )
        self._btn_del_period.setStyleSheet(
            f"QPushButton {{ background:{c['danger_bg']}; border:1px solid {c['danger_border']}; "
            f"border-radius:5px; padding:0 12px; color:{c['danger_text']}; }}"
            f" QPushButton:hover {{ background:{c['danger_bg']}; }}"
            f" QPushButton:disabled {{ opacity:0.4; }}"
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
        from utils.period_fmt import fmt as _fmt
        periods = repo.get_all_periods()
        self.period_combo.blockSignals(True)
        self.period_combo.clear()
        self.period_combo.addItem("（新規期間を入力）", None)
        for p in periods:
            self.period_combo.addItem(_fmt(p.start_date, p.end_date), p)
        self.period_combo.blockSignals(False)


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
                    self._load_employees()
                return

    def _on_period_changed(self, idx):
        p = self.period_combo.currentData()
        self._btn_del_period.setEnabled(p is not None)
        if p:
            self._period = p
            self._load_employees()
            self.period_changed.emit(p.id)  # item 1

    def _refresh_history(self):
        """過去の希望パターン（頻出順）ボタンを更新する"""
        layout = self._history_inner
        # 既存ボタンをクリア
        while layout.count():
            item = layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        if not self._employees or not self._period:
            return
        emp = self._employees[self._current_idx]
        history = repo.get_employee_pattern_history(emp.id)

        if not history:
            lbl = QLabel("（過去のデータなし）")
            lbl.setStyleSheet("color:#9ca3af; font-size:11px;")
            layout.addWidget(lbl)
            return

        c = theme.c
        for pid, cnt in history:
            p = PATTERN_MAP.get(pid)
            if not p:
                continue
            btn = QPushButton(f"{p.label}  ×{cnt}")
            btn.setFixedHeight(26)
            if p.covers_breakfast() and p.covers_dinner():
                bg = c["cell_double"]
            elif p.covers_breakfast():
                bg = c["cell_breakfast"]
            elif p.covers_dinner():
                bg = c["cell_dinner"]
            else:
                bg = c["cell_none"]
            btn.setStyleSheet(
                f"QPushButton {{ background:{bg}; border:1px solid {c['border2']}; "
                f"border-radius:3px; padding:0 8px; font-size:11px; color:{c['text']}; }}"
                f" QPushButton:hover {{ border-color:{c['primary']}; }}"
            )
            btn.setToolTip(f"すべての日程を「{p.label}」で埋めます（上書き）")
            btn.clicked.connect(lambda checked=False, _pid=pid: self._apply_pattern_to_all(_pid))
            layout.addWidget(btn)

    def _apply_pattern_to_all(self, pattern_id: str):
        """すべての有効な日程に指定パターンを適用する"""
        if not self._period or not self._employees:
            return
        emp = self._employees[self._current_idx]
        for row in range(self.table.rowCount()):
            item = self.table.item(row, 0)
            if not item:
                continue
            ds = item.data(Qt.ItemDataRole.UserRole)
            cell = self._pattern_cells.get(ds)
            if cell and cell.combo.isEnabled():
                cell.set_pattern(pattern_id)
                self._apply_row_color(row, ds, False)

    def _on_delete_period(self):
        p = self.period_combo.currentData()
        if not p:
            return
        reply = QMessageBox.question(
            self, "期間削除の確認",
            f"期間「{p.start_date} 〜 {p.end_date}」を削除しますか？\n"
            "関連するシフト希望・アサインデータもすべて削除されます。",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
        repo.delete_period(p.id)
        self._period = None
        self._employees = []
        self._requests = {}
        self._pattern_cells = {}
        self.table.setRowCount(0)
        self._load_periods()
        self._btn_del_period.setEnabled(False)
        self.progress_label.setText("")

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
                self.period_combo.blockSignals(True)
                self.period_combo.setCurrentIndex(i)
                self.period_combo.blockSignals(False)
                break
        self._load_employees()
        self.period_changed.emit(period.id)  # item 1

    # ── 従業員 ────────────────────────────────────────────────────────────

    def _load_employees(self):
        if not self._period:
            self._btn_forms_import.setEnabled(False)
            return
        self._employees = repo.get_all_employees()
        self._btn_forms_import.setEnabled(bool(self._employees))
        if not self._employees:
            # item 4: 空状態ガイダンス
            self.progress_label.setText(
                "⚠️ 従業員が登録されていません。まず「従業員管理」画面で登録してください。"
            )
            self.progress_label.setStyleSheet("color:#b45309; font-weight:bold;")
            self.table.setRowCount(0)
            return
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
        filled_ids = set(
            r.employee_id for r in self._requests.values() if r.has_shift
        )
        total  = len(self._employees)
        filled = len(filled_ids)
        # item 3: 詳細サマリー
        not_filled = [e.name for e in self._employees if e.id not in filled_ids]
        c = theme.c
        if filled == total:
            self.progress_label.setText(f"✅ 入力済: {filled} / {total} 名（全員完了）")
            self.progress_label.setStyleSheet("color:#16a34a; font-weight:bold;")
        else:
            remaining = "、".join(not_filled[:3])
            suffix = f" 他{len(not_filled)-3}名" if len(not_filled) > 3 else ""
            self.progress_label.setText(
                f"📝 入力済: {filled} / {total} 名　未入力: {remaining}{suffix}"
            )
            self.progress_label.setStyleSheet(f"color:{c['text2']};")

    def _current_snapshot(self) -> dict:
        """現在の入力状態をスナップショットとして返す"""
        snap = {}
        for ds, cell in self._pattern_cells.items():
            snap[ds] = (cell.get_pattern_id(), cell.custom_edit.text())
        return snap

    def _has_unsaved_changes(self) -> bool:
        return self._current_snapshot() != self._saved_snapshot

    def _confirm_leave(self) -> bool:
        """未保存の変更がある場合に確認ダイアログを表示。離脱してよければ True を返す"""
        if not self._has_unsaved_changes():
            return True
        reply = QMessageBox.question(
            self, "未保存の変更",
            "保存されていない変更があります。\nこのまま切り替えますか？（変更は失われます）",
            QMessageBox.StandardButton.Discard | QMessageBox.StandardButton.Cancel,
        )
        return reply == QMessageBox.StandardButton.Discard

    def _on_employee_changed(self, idx):
        if idx == self._current_idx:
            return
        if not self._confirm_leave():
            # キャンセル: コンボを元に戻す
            self.emp_combo.blockSignals(True)
            self.emp_combo.setCurrentIndex(self._current_idx)
            self.emp_combo.blockSignals(False)
            return
        self._current_idx = idx
        self._render_table()

    def _on_prev_employee(self):
        if self._current_idx > 0:
            if not self._confirm_leave():
                return
            self._current_idx -= 1
            self.emp_combo.blockSignals(True)
            self.emp_combo.setCurrentIndex(self._current_idx)
            self.emp_combo.blockSignals(False)
            self._render_table()

    def _on_next_employee(self):
        if self._current_idx < len(self._employees) - 1:
            if not self._confirm_leave():
                return
            self._current_idx += 1
            self.emp_combo.blockSignals(True)
            self.emp_combo.setCurrentIndex(self._current_idx)
            self.emp_combo.blockSignals(False)
            self._render_table()

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

            # コンボの数字キーシグナルをビューのハンドラに接続
            cell.combo.number_pressed.connect(
                lambda idx, ds=date_str, r=row: self._on_combo_number_key(idx, ds, r)
            )

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

        # テーブル描画後、現在の状態を「保存済み」スナップショットとして記録
        self._saved_snapshot = self._current_snapshot()
        self._refresh_history()

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
        self._saved_snapshot = self._current_snapshot()
        self._update_progress()
        from utils.toast import show_toast  # item 5
        show_toast(f"「{emp.name}さん」の希望シフトを保存しました")

    # ── Google Forms CSV インポート ────────────────────────────────────────

    def _on_forms_import(self):
        if not self._period or not self._employees:
            return
        from ui.forms_import_dialog import FormsImportDialog
        dlg = FormsImportDialog(self._period, self._employees, parent=self)
        if dlg.exec():
            self._load_employees()
