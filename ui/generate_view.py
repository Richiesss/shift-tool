"""シフト自動生成画面"""
from __future__ import annotations
import threading
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QPushButton, QLabel,
    QComboBox, QGroupBox, QTextEdit, QProgressBar, QMessageBox,
    QFrame, QSizePolicy
)
from PyQt6.QtCore import Qt, QThread, pyqtSignal, QObject
from PyQt6.QtGui import QFont, QColor
from db import repositories as repo
from optimizer.solver import solve, SolveResult


class SolverWorker(QObject):
    finished = pyqtSignal(object)  # SolveResult

    def __init__(self, period, employees, requests):
        super().__init__()
        self.period = period
        self.employees = employees
        self.requests = requests

    def run(self):
        result = solve(self.period, self.employees, self.requests)
        self.finished.emit(result)


class GenerateView(QWidget):
    schedule_generated = pyqtSignal(int)  # period_id

    def __init__(self, parent=None):
        super().__init__(parent)
        self._period = None
        self._thread = None
        self._worker = None
        self._build_ui()
        self._load_periods()

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(16)

        title = QLabel("シフト自動生成")
        title.setFont(QFont("", 16, QFont.Weight.Bold))
        layout.addWidget(title)

        # 期間選択
        period_group = QGroupBox("対象期間")
        pl = QHBoxLayout(period_group)
        self.period_combo = QComboBox()
        self.period_combo.setMinimumWidth(260)
        self.period_combo.currentIndexChanged.connect(self._on_period_changed)
        pl.addWidget(QLabel("期間:"))
        pl.addWidget(self.period_combo)
        pl.addStretch()
        layout.addWidget(period_group)

        # データ確認
        check_group = QGroupBox("入力データ確認")
        check_layout = QVBoxLayout(check_group)
        self.check_label = QLabel("期間を選択してください")
        self.check_label.setWordWrap(True)
        check_layout.addWidget(self.check_label)
        layout.addWidget(check_group)

        # 生成ボタン
        btn_row = QHBoxLayout()
        btn_row.addStretch()
        self.btn_generate = QPushButton("　シフトを自動生成する　")
        self.btn_generate.setFixedHeight(44)
        self.btn_generate.setEnabled(False)
        self.btn_generate.setFont(QFont("", 12, QFont.Weight.Bold))
        self.btn_generate.setStyleSheet("""
            QPushButton { background:#2563eb; color:white; border-radius:8px; padding:0 24px; }
            QPushButton:hover { background:#1d4ed8; }
            QPushButton:disabled { background:#93c5fd; }
        """)
        self.btn_generate.clicked.connect(self._on_generate)
        btn_row.addWidget(self.btn_generate)
        btn_row.addStretch()
        layout.addLayout(btn_row)

        # プログレス
        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 0)  # indeterminate
        self.progress_bar.setVisible(False)
        self.progress_bar.setFixedHeight(8)
        layout.addWidget(self.progress_bar)

        # 結果表示
        result_group = QGroupBox("生成結果")
        rl = QVBoxLayout(result_group)
        self.result_text = QTextEdit()
        self.result_text.setReadOnly(True)
        self.result_text.setMinimumHeight(200)
        self.result_text.setStyleSheet("font-family: monospace; font-size:13px;")
        rl.addWidget(self.result_text)

        self.btn_to_edit = QPushButton("シフト表の確認・編集へ →")
        self.btn_to_edit.setFixedHeight(36)
        self.btn_to_edit.setEnabled(False)
        self.btn_to_edit.setStyleSheet("""
            QPushButton { background:#16a34a; color:white; border-radius:6px; padding:0 20px; font-weight:bold; }
            QPushButton:hover { background:#15803d; }
            QPushButton:disabled { background:#86efac; }
        """)
        self.btn_to_edit.clicked.connect(self._on_go_to_edit)
        rl.addWidget(self.btn_to_edit, alignment=Qt.AlignmentFlag.AlignRight)
        layout.addWidget(result_group)

    def _load_periods(self):
        periods = repo.get_all_periods()
        self.period_combo.blockSignals(True)
        self.period_combo.clear()
        self.period_combo.addItem("（期間を選択）", None)
        for p in periods:
            self.period_combo.addItem(f"{p.start_date} 〜 {p.end_date}", p)
        self.period_combo.blockSignals(False)

    def refresh(self):
        self._load_periods()

    def _on_period_changed(self, idx):
        self._period = self.period_combo.currentData()
        if not self._period:
            self.check_label.setText("期間を選択してください")
            self.btn_generate.setEnabled(False)
            return
        self._update_check()

    def _update_check(self):
        if not self._period:
            return
        employees = repo.get_all_employees()
        requests = repo.get_shift_requests(self._period.id)

        filled_ids = set(r.employee_id for r in requests if r.breakfast or r.dinner)
        total = len(employees)
        not_filled = [e for e in employees if e.id not in filled_ids]

        lines = []
        if not_filled:
            lines.append(f"⚠️  未入力の従業員: {len(not_filled)}名")
            for e in not_filled[:5]:
                lines.append(f"   ・{e.name}")
            if len(not_filled) > 5:
                lines.append(f"   … 他{len(not_filled)-5}名")
        else:
            lines.append(f"✅ 希望シフト入力済: {total}/{total}名")

        lines.append(f"✅ 従業員登録数: {total}名")
        self.check_label.setText("\n".join(lines))
        self.btn_generate.setEnabled(True)

    def _on_generate(self):
        if not self._period:
            return

        employees = repo.get_all_employees()
        requests = repo.get_shift_requests(self._period.id)

        self.btn_generate.setEnabled(False)
        self.btn_to_edit.setEnabled(False)
        self.progress_bar.setVisible(True)
        self.result_text.clear()
        self.result_text.setPlainText("最適化中です。しばらくお待ちください…")

        self._thread = QThread()
        self._worker = SolverWorker(self._period, employees, requests)
        self._worker.moveToThread(self._thread)
        self._thread.started.connect(self._worker.run)
        self._worker.finished.connect(self._on_finished)
        self._worker.finished.connect(self._thread.quit)
        self._thread.start()

    def _on_finished(self, result: SolveResult):
        self.progress_bar.setVisible(False)
        self.btn_generate.setEnabled(True)

        lines = []
        if result.status in ("optimal", "feasible"):
            icon = "✅" if result.status == "optimal" else "⚡"
            lines.append(f"{icon} 生成{'完了（最適解）' if result.status == 'optimal' else '完了（準最適解）'}")
            lines.append(f"   求解時間: {result.solve_time_sec:.1f}秒")
            lines.append(f"   総アサイン数: {len(result.assignments)}件")
            if result.warnings:
                lines.append("")
                lines.append("【警告】")
                for w in result.warnings:
                    lines.append(f"  ⚠️  {w}")
            lines.append("")
            lines.append("→ 「シフト表の確認・編集へ」から内容を確認してください")

            # 保存
            repo.save_assignments(self._period.id, result.assignments)
            self.btn_to_edit.setEnabled(True)

        else:
            lines.append("❌ シフトを生成できませんでした")
            lines.append("")
            lines.append("【原因】")
            for e in result.errors:
                lines.append(f"  {e}")
            lines.append("")
            lines.append("対策: 希望シフト入力画面で当該日の入力を見直すか、")
            lines.append("      手動でシフト表画面からアサインしてください。")

        self.result_text.setPlainText("\n".join(lines))

    def _on_go_to_edit(self):
        if self._period:
            self.schedule_generated.emit(self._period.id)
