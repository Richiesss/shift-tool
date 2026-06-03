"""シフト自動生成画面"""
from __future__ import annotations
import threading
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QPushButton, QLabel,
    QComboBox, QGroupBox, QTextEdit, QProgressBar, QMessageBox,
    QFrame, QSizePolicy, QScrollArea
)
from PyQt6.QtCore import Qt, QThread, pyqtSignal, QObject
from PyQt6.QtGui import QFont, QColor
from db import repositories as repo
from optimizer.solver import solve, SolveResult, SolverConfig, PRIORITY_SCALE
from utils.theme import theme


class SolverWorker(QObject):
    finished = pyqtSignal(object)  # SolveResult

    def __init__(self, period, employees, requests, config: SolverConfig):
        super().__init__()
        self.period = period
        self.employees = employees
        self.requests = requests
        self.config = config

    def run(self):
        result = solve(self.period, self.employees, self.requests, self.config)
        self.finished.emit(result)


class GenerateView(QWidget):
    schedule_generated  = pyqtSignal(int)  # period_id
    period_changed      = pyqtSignal(int)  # item 1
    navigate_requested  = pyqtSignal(int)  # item 4

    def __init__(self, parent=None):
        super().__init__(parent)
        self._period = None
        self._thread = None
        self._worker = None
        self._build_ui()
        self._load_periods()

    def _build_ui(self):
        # スクロールエリアでラップ（ウィンドウが小さくても潰れない）
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        outer.addWidget(scroll)

        inner = QWidget()
        scroll.setWidget(inner)
        layout = QVBoxLayout(inner)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(16)
        layout.setAlignment(Qt.AlignmentFlag.AlignTop)

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

        # 最適化設定
        config_group = QGroupBox("最適化設定（優先度）")
        config_vl = QVBoxLayout(config_group)
        config_vl.setSpacing(8)

        # 優先度設定行: (表示名, attr名, 説明文)
        _CONFIG_ROWS = [
            ("人件費最小化",        "cost_scale",
             "コストが低いスタッフを優先してアサインします。人件費削減を重視する場合は「高」に設定してください。"),
            ("アルバイト希望充当",   "pt_pref_scale",
             "アルバイトの希望シフトをできるだけ尊重します。スタッフの満足度を重視する場合は「高」に設定してください。"),
            # 正社員両掛け持ち回避はハード制約で禁止済みのためUI設定を削除
            ("人員バランス均等化",   "balance_scale",
             "特定のスタッフに勤務が偏らないよう、全員の出勤数を均等化します。公平なシフト分配を重視する場合は「高」に設定してください。"),
            ("深夜勤務分散",         "late_night_scale",
             "ディナーの遅い時間帯（深夜枠）の担当が一部のスタッフに集中しないよう分散させます。"),
        ]

        self._config_combos: dict[str, QComboBox] = {}
        for i, (display_name, attr, description) in enumerate(_CONFIG_ROWS):
            if i > 0:
                sep = QFrame()
                sep.setFrameShape(QFrame.Shape.HLine)
                sep.setObjectName("config_sep")
                config_vl.addWidget(sep)

            # QWidget コンテナ必須: 裸の QHBoxLayout をネストすると
            # Qt がジオメトリを正しく計算できず、ウィジェットが重なる
            container = QWidget()
            cl = QVBoxLayout(container)
            cl.setContentsMargins(0, 4, 0, 4)
            cl.setSpacing(3)

            # 項目名 + コンボを QWidget に収める（ここも QWidget 必須）
            header_widget = QWidget()
            hl = QHBoxLayout(header_widget)
            hl.setContentsMargins(0, 0, 0, 0)
            hl.setSpacing(8)

            name_lbl = QLabel(display_name)
            name_lbl.setFont(QFont("", -1, QFont.Weight.Bold))

            combo = QComboBox()
            combo.addItem("低",  "低")
            combo.addItem("中",  "中")
            combo.addItem("高",  "高")
            combo.setCurrentIndex(1)   # デフォルト「中」
            combo.setFixedWidth(80)
            combo.setMinimumHeight(28)
            self._config_combos[attr] = combo

            hl.addWidget(name_lbl, 1)
            hl.addWidget(combo)

            desc_lbl = QLabel(description)
            desc_lbl.setWordWrap(True)
            desc_lbl.setObjectName("desc_label")

            cl.addWidget(header_widget)
            cl.addWidget(desc_lbl)
            config_vl.addWidget(container)

        # リセットボタン
        reset_row = QHBoxLayout()
        reset_row.addStretch()
        self._btn_reset = QPushButton("デフォルトに戻す")
        self._btn_reset.setFixedHeight(28)
        self._btn_reset.clicked.connect(self._reset_config)
        reset_row.addWidget(self._btn_reset)
        config_vl.addLayout(reset_row)
        layout.addWidget(config_group)

        # 生成ボタン
        btn_row = QHBoxLayout()
        btn_row.addStretch()
        self.btn_generate = QPushButton("　シフトを自動生成する　")
        self.btn_generate.setFixedHeight(44)
        self.btn_generate.setEnabled(False)
        self.btn_generate.setFont(QFont("", 12, QFont.Weight.Bold))
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
        self.btn_to_edit.clicked.connect(self._on_go_to_edit)
        rl.addWidget(self.btn_to_edit, alignment=Qt.AlignmentFlag.AlignRight)
        layout.addWidget(result_group)

        self._apply_btn_styles()

    def _apply_btn_styles(self):
        c = theme.c
        # 説明文ラベルをサブテキスト色で表示
        for w in self.findChildren(QLabel, "desc_label"):
            w.setStyleSheet(f"color: {c['text2']}; font-size: 9pt;")
        for w in self.findChildren(QFrame, "config_sep"):
            w.setStyleSheet(f"color: {c['border']};")
        self.btn_generate.setStyleSheet(
            f"QPushButton {{ background:{c['primary']}; color:white; border-radius:8px; padding:0 24px; }}"
            f" QPushButton:hover {{ background:{c['primary_hover']}; }}"
            f" QPushButton:disabled {{ background:{c['primary_dis']}; }}"
        )
        self.btn_to_edit.setStyleSheet(
            f"QPushButton {{ background:{c['success']}; color:white; border-radius:6px; padding:0 20px; font-weight:bold; }}"
            f" QPushButton:hover {{ background:{c['success_hover']}; }}"
            f" QPushButton:disabled {{ background:{c['success_dis']}; }}"
        )
        self._btn_reset.setStyleSheet(
            f"QPushButton {{ background:{c['surface']}; border:1px solid {c['border2']}; "
            f"border-radius:4px; color:{c['text']}; padding:0 12px; }}"
            f" QPushButton:hover {{ background:{c['surface2']}; }}"
        )

    def apply_theme(self):
        self._apply_btn_styles()

    def _load_periods(self):
        from utils.period_fmt import fmt as _fmt
        periods = repo.get_all_periods()
        self.period_combo.blockSignals(True)
        self.period_combo.clear()
        self.period_combo.addItem("（期間を選択）", None)
        for p in periods:
            self.period_combo.addItem(_fmt(p.start_date, p.end_date), p)
        self.period_combo.blockSignals(False)

    def refresh(self):
        self._load_periods()

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
                    self._update_check()
                return

    def _on_period_changed(self, idx):
        self._period = self.period_combo.currentData()
        if not self._period:
            self.check_label.setText("期間を選択してください")
            self.btn_generate.setEnabled(False)
            return
        self.period_changed.emit(self._period.id)  # item 1
        self._update_check()

    def _update_check(self):
        if not self._period:
            return
        employees = repo.get_all_employees()
        requests  = repo.get_shift_requests(self._period.id)

        # item 4: 空状態ガイダンス
        if not employees:
            self.check_label.setText(
                "⚠️ 従業員が登録されていません。\n"
                "「従業員管理」画面で従業員を登録してからシフトを生成してください。"
            )
            self.btn_generate.setEnabled(False)
            return

        filled_ids = set(r.employee_id for r in requests if r.breakfast or r.dinner)
        total      = len(employees)
        not_filled = [e for e in employees if e.id not in filled_ids]

        lines = []
        lines.append(f"✅ 従業員登録数: {total}名")
        if not_filled:
            lines.append(f"⚠️  希望未入力: {len(not_filled)}名")
            for e in not_filled[:5]:
                lines.append(f"   ・{e.name}")
            if len(not_filled) > 5:
                lines.append(f"   … 他{len(not_filled)-5}名")
        else:
            lines.append(f"✅ 希望シフト入力済: {total}/{total}名（全員完了）")

        self.check_label.setText("\n".join(lines))
        self.btn_generate.setEnabled(True)

    def _on_generate(self):
        if not self._period:
            return
        # 前回のスレッドがまだ動いている場合は新規起動しない（二重起動防止）
        if self._thread is not None and self._thread.isRunning():
            return

        employees = repo.get_all_employees()
        requests = repo.get_shift_requests(self._period.id)

        self.btn_generate.setEnabled(False)
        self.btn_to_edit.setEnabled(False)
        self.progress_bar.setVisible(True)
        self.result_text.clear()
        self.result_text.setPlainText("最適化中です。しばらくお待ちください…")

        self._thread = QThread()
        self._worker = SolverWorker(self._period, employees, requests, self._build_config())
        self._worker.moveToThread(self._thread)
        self._thread.started.connect(self._worker.run)
        self._worker.finished.connect(self._on_finished)
        self._worker.finished.connect(self._thread.quit)
        self._thread.start()

    def _on_finished(self, result: SolveResult):
        # ウィジェットが既に破棄されている場合は何もしない
        if not self.isVisible():
            return
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

    def _reset_config(self):
        for combo in self._config_combos.values():
            combo.setCurrentIndex(1)  # 「中」

    def _build_config(self) -> SolverConfig:
        scales = {attr: PRIORITY_SCALE[combo.currentData()]
                  for attr, combo in self._config_combos.items()}
        return SolverConfig(
            cost_scale=scales["cost_scale"],
            pt_pref_scale=scales["pt_pref_scale"],
            balance_scale=scales["balance_scale"],
            late_night_scale=scales["late_night_scale"],
        )

    def _on_go_to_edit(self):
        if self._period:
            self.schedule_generated.emit(self._period.id)
