"""客数管理画面（カレンダー形式で予約客数を入力）"""
from __future__ import annotations
from datetime import date, timedelta

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QComboBox,
    QTabWidget, QTableWidget, QTableWidgetItem, QHeaderView,
    QPushButton, QDialog, QSpinBox, QAbstractItemView, QFrame
)
from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QFont, QColor, QBrush

from db import repositories as repo
from utils.constants import TimeSlot, DAY_OF_WEEK_LABELS
from utils.theme import theme


class CustomerCountView(QWidget):
    period_changed = pyqtSignal(int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._period = None
        self._counts: dict[str, dict[str, int]] = {}
        self._build_ui()
        self._load_periods()

    # ── UI構築 ─────────────────────────────────────────────────────────────

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(10)

        # ヘッダー
        header = QHBoxLayout()
        title = QLabel("客数管理")
        title.setFont(QFont("", 16, QFont.Weight.Bold))
        header.addWidget(title)
        header.addStretch()
        self.period_combo = QComboBox()
        self.period_combo.setMinimumWidth(240)
        self.period_combo.currentIndexChanged.connect(self._on_period_changed)
        header.addWidget(QLabel("期間:"))
        header.addWidget(self.period_combo)
        layout.addLayout(header)

        # 説明
        info = QLabel(
            "各日の予約客数を入力します。セルをクリックして客数を入力してください。\n"
            "🟢 入力済み（閾値未満）　🔴 増員対象（閾値以上）　設定画面で閾値を変更できます。"
        )
        info.setWordWrap(True)
        info.setStyleSheet("color:#6b7280; font-size:11px;")
        layout.addWidget(info)

        # タブ（朝食 / ディナー）
        self.tab_widget = QTabWidget()
        self._table_b = self._make_calendar_table(TimeSlot.BREAKFAST)
        self._table_d = self._make_calendar_table(TimeSlot.DINNER)
        self.tab_widget.addTab(self._table_b, "🌅 朝食")
        self.tab_widget.addTab(self._table_d, "🌆 ディナー")
        layout.addWidget(self.tab_widget)

        # 凡例
        legend = QHBoxLayout()
        for color, text in [
            ("#DCFCE7", "入力済み（閾値未満）"),
            ("#FEE2E2", "増員対象（閾値以上）"),
            ("#FFFBEB", "土曜"),
            ("#FFF5F5", "日曜"),
        ]:
            dot = QLabel("■")
            dot.setStyleSheet(f"color:{color}; font-size:18px;")
            legend.addWidget(dot)
            lbl = QLabel(text)
            lbl.setStyleSheet("font-size:11px;")
            legend.addWidget(lbl)
        legend.addStretch()
        self._threshold_label = QLabel("")
        self._threshold_label.setStyleSheet("font-size:11px; color:#6b7280;")
        legend.addWidget(self._threshold_label)
        layout.addLayout(legend)

    def _make_calendar_table(self, slot: TimeSlot) -> QTableWidget:
        table = QTableWidget()
        table.setColumnCount(7)
        table.setHorizontalHeaderLabels(["月", "火", "水", "木", "金", "土", "日"])
        table.verticalHeader().setVisible(False)
        table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        table.horizontalHeader().setDefaultAlignment(Qt.AlignmentFlag.AlignCenter)
        table.cellClicked.connect(
            lambda r, c, s=slot: self._on_cell_clicked(r, c, s))
        return table

    # ── 期間管理 ──────────────────────────────────────────────────────────

    def _load_periods(self):
        from utils.period_fmt import fmt as _fmt
        periods = repo.get_all_periods()
        self.period_combo.blockSignals(True)
        self.period_combo.clear()
        self.period_combo.addItem("（期間を選択）", None)
        for p in periods:
            self.period_combo.addItem(_fmt(p.start_date, p.end_date), p)
        self.period_combo.blockSignals(False)

    def set_period_by_id(self, period_id: int):
        for i in range(self.period_combo.count()):
            p = self.period_combo.itemData(i)
            if p and p.id == period_id:
                self.period_combo.blockSignals(True)
                self.period_combo.setCurrentIndex(i)
                self.period_combo.blockSignals(False)
                self._period = p
                self._load_counts()
                self._render_calendars()
                return

    def _on_period_changed(self, idx):
        self._period = self.period_combo.currentData()
        if not self._period:
            self._table_b.setRowCount(0)
            self._table_d.setRowCount(0)
            self._threshold_label.setText("")
            return
        self._load_counts()
        self._render_calendars()
        self.period_changed.emit(self._period.id)

    def _load_counts(self):
        if not self._period:
            self._counts = {}
            return
        self._counts = repo.get_reservation_counts(self._period.id)

    def _get_threshold(self, slot_key: str) -> int:
        key = "reserv_threshold_breakfast" if slot_key == "breakfast" else "reserv_threshold_dinner"
        default = "100" if slot_key == "breakfast" else "25"
        try:
            return int(repo.get_app_setting(key, default))
        except Exception:
            return 999

    # ── カレンダー描画 ────────────────────────────────────────────────────

    def _render_calendars(self):
        if not self._period:
            return
        b_thresh = self._get_threshold("breakfast")
        d_thresh = self._get_threshold("dinner")
        self._threshold_label.setText(
            f"増員閾値 ─ 朝食: {b_thresh}名以上  ディナー: {d_thresh}名以上"
        )
        self._render_calendar(self._table_b, "breakfast", b_thresh)
        self._render_calendar(self._table_d, "dinner",    d_thresh)

    def _render_calendar(self, table: QTableWidget, slot_key: str, threshold: int):
        if not self._period:
            return

        dates = self._period.date_range()
        if not dates:
            table.setRowCount(0)
            return

        first_date = dates[0]
        first_dow  = first_date.weekday()          # 0=月
        grid_start = first_date - timedelta(days=first_dow)

        last_date = dates[-1]
        last_dow  = last_date.weekday()
        grid_end  = last_date + timedelta(days=6 - last_dow)

        n_weeks = ((grid_end - grid_start).days + 1) // 7
        table.setRowCount(n_weeks)

        period_date_set = {d.isoformat() for d in dates}
        today_str = date.today().isoformat()
        c = theme.c

        cur = grid_start
        for week_idx in range(n_weeks):
            table.setRowHeight(week_idx, 68)
            for dow in range(7):
                ds = cur.isoformat()
                if ds in period_date_set:
                    count = self._counts.get(ds, {}).get(slot_key, 0)
                    count_text = f"{count}名" if count > 0 else "─"
                    text = f"{cur.day}日\n{count_text}"

                    item = QTableWidgetItem(text)
                    item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                    item.setFont(QFont("", 11))
                    item.setData(Qt.ItemDataRole.UserRole, ds)

                    # 背景色
                    if count > 0 and count >= threshold:
                        bg = "#FEE2E2"
                    elif count > 0:
                        bg = "#DCFCE7"
                    elif dow == 5:
                        bg = "#FFFBEB"
                    elif dow == 6:
                        bg = "#FFF5F5"
                    else:
                        bg = c["bg"]
                    item.setBackground(QBrush(QColor(bg)))

                    # 今日強調
                    if ds == today_str:
                        item.setForeground(QBrush(QColor(c["primary"])))
                        font = QFont("", 11, QFont.Weight.Bold)
                        item.setFont(font)

                else:
                    # 期間外（グレー）
                    item = QTableWidgetItem(str(cur.day))
                    item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                    item.setBackground(QBrush(QColor(c["surface2"])))
                    item.setForeground(QBrush(QColor(c["text3"])))
                    item.setFlags(Qt.ItemFlag.ItemIsEnabled)

                table.setItem(week_idx, dow, item)
                cur += timedelta(days=1)

    # ── セルクリック ──────────────────────────────────────────────────────

    def _on_cell_clicked(self, row: int, col: int, slot: TimeSlot):
        if not self._period:
            return
        table = self._table_b if slot == TimeSlot.BREAKFAST else self._table_d
        item = table.item(row, col)
        if not item:
            return
        ds = item.data(Qt.ItemDataRole.UserRole)
        if not ds:
            return  # 期間外セル

        slot_key  = "breakfast" if slot == TimeSlot.BREAKFAST else "dinner"
        threshold = self._get_threshold(slot_key)
        current   = self._counts.get(ds, {}).get(slot_key, 0)

        dlg = _CountInputDialog(ds, slot, current, threshold, parent=self)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            count = dlg.count
            rc = self._counts.get(ds, {"breakfast": 0, "dinner": 0}).copy()
            rc[slot_key] = count
            self._counts[ds] = rc
            repo.save_reservation_count(self._period.id, ds, rc["breakfast"], rc["dinner"])
            self._render_calendar(table, slot_key, threshold)

    # ── 公開API ──────────────────────────────────────────────────────────

    def refresh(self, period_id: int = None):
        self._load_periods()
        if period_id:
            self.set_period_by_id(period_id)
        elif self._period:
            self._load_counts()
            self._render_calendars()

    def apply_theme(self):
        self._render_calendars()


# ── 客数入力ダイアログ ─────────────────────────────────────────────────────

class _CountInputDialog(QDialog):
    def __init__(self, date_str: str, slot: TimeSlot,
                 current_count: int, threshold: int, parent=None):
        super().__init__(parent)
        d = date.fromisoformat(date_str)
        dow = DAY_OF_WEEK_LABELS[d.weekday()]
        slot_label = "朝食" if slot == TimeSlot.BREAKFAST else "ディナー"
        self.setWindowTitle(f"{d.month}/{d.day}({dow})  {slot_label} 予約客数")
        self.setFixedWidth(320)
        self._build_ui(d, dow, slot_label, current_count, threshold)

    def _build_ui(self, d: date, dow: str, slot_label: str,
                  current_count: int, threshold: int):
        layout = QVBoxLayout(self)
        layout.setSpacing(12)

        date_lbl = QLabel(f"📅  {d.year}年{d.month}月{d.day}日（{dow}）  {slot_label}")
        date_lbl.setFont(QFont("", 11))
        layout.addWidget(date_lbl)

        sep = QFrame()
        sep.setFrameShape(QFrame.Shape.HLine)
        layout.addWidget(sep)

        self._spin = QSpinBox()
        self._spin.setRange(0, 9999)
        self._spin.setValue(current_count)
        self._spin.setFixedHeight(44)
        self._spin.setSuffix("  名")
        self._spin.setFont(QFont("", 14))
        self._spin.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self._spin)

        c = theme.c
        thresh_lbl = QLabel(f"増員閾値: {threshold}名以上で増員")
        thresh_lbl.setStyleSheet(f"color:{c['text2']}; font-size:11px;")
        layout.addWidget(thresh_lbl)

        hint = QLabel("0 で保存するとデータを削除します。")
        hint.setStyleSheet("color:#9ca3af; font-size:11px;")
        layout.addWidget(hint)

        btn_row = QHBoxLayout()
        btn_cancel = QPushButton("キャンセル")
        btn_cancel.clicked.connect(self.reject)
        btn_ok = QPushButton("保存")
        btn_ok.setDefault(True)
        btn_ok.setFixedHeight(34)
        btn_ok.clicked.connect(self.accept)
        btn_ok.setStyleSheet(
            f"QPushButton {{ background:{c['primary']}; color:white; "
            f"border-radius:5px; padding:0 20px; font-weight:bold; }}"
            f" QPushButton:hover {{ background:{c['primary_hover']}; }}"
        )
        btn_row.addStretch()
        btn_row.addWidget(btn_cancel)
        btn_row.addWidget(btn_ok)
        layout.addLayout(btn_row)

    @property
    def count(self) -> int:
        return self._spin.value()
