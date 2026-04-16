"""出力ダイアログ"""
from __future__ import annotations
import os
from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QPushButton, QLabel,
    QGroupBox, QRadioButton, QButtonGroup, QFileDialog,
    QMessageBox, QLineEdit
)
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QFont
from models.employee import Employee
from models.schedule import SchedulePeriod
from utils.theme import theme


class OutputDialog(QDialog):
    def __init__(self, period: SchedulePeriod, employees: list[Employee],
                 assignments: dict, parent=None):
        super().__init__(parent)
        self.period = period
        self.employees = employees
        self.assignments = assignments
        self.setWindowTitle("シフト表の出力")
        self.setFixedWidth(480)
        self._build_ui()

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setSpacing(14)

        title = QLabel("出力設定")
        title.setFont(QFont("", 14, QFont.Weight.Bold))
        layout.addWidget(title)

        layout.addWidget(QLabel(f"対象期間: {self.period.start_date} 〜 {self.period.end_date}"))

        # 出力形式
        format_group = QGroupBox("出力形式")
        fl = QVBoxLayout(format_group)
        self.btn_group = QButtonGroup()
        self.rb_pdf = QRadioButton("PDF出力（印刷用）")
        self.rb_xlsx = QRadioButton("Excel出力（.xlsx）")
        self.rb_pdf.setChecked(True)
        self.btn_group.addButton(self.rb_pdf)
        self.btn_group.addButton(self.rb_xlsx)
        fl.addWidget(self.rb_pdf)
        fl.addWidget(self.rb_xlsx)
        layout.addWidget(format_group)

        # 保存先
        path_group = QGroupBox("保存先")
        pl = QHBoxLayout(path_group)
        self.path_edit = QLineEdit()
        # 自動ファイル名: シフト表_YYYY年M月D日-M月D日
        from datetime import date as _date
        sd = _date.fromisoformat(self.period.start_date)
        ed = _date.fromisoformat(self.period.end_date)
        default_name = (
            f"シフト表_{sd.year}年{sd.month}月{sd.day}日-{ed.month}月{ed.day}日.pdf"
        )
        default_dir = os.path.expanduser("~/Desktop")
        self.path_edit.setText(os.path.join(default_dir, default_name))
        btn_browse = QPushButton("参照...")
        btn_browse.setFixedWidth(64)
        btn_browse.clicked.connect(self._browse)
        pl.addWidget(self.path_edit)
        pl.addWidget(btn_browse)
        layout.addWidget(path_group)
        self.rb_pdf.toggled.connect(self._on_format_changed)
        self.rb_xlsx.toggled.connect(self._on_format_changed)

        # ボタン
        btn_row = QHBoxLayout()
        btn_cancel = QPushButton("キャンセル")
        btn_cancel.clicked.connect(self.reject)
        btn_export = QPushButton("出力する")
        btn_export.setFixedHeight(36)
        c = theme.c
        btn_export.setStyleSheet(
            f"QPushButton {{ background:{c['purple']}; color:white; border-radius:6px; padding:0 20px; font-weight:bold; }}"
            f" QPushButton:hover {{ background:{c['purple_hover']}; }}"
        )
        btn_export.clicked.connect(self._on_export)
        btn_row.addStretch()
        btn_row.addWidget(btn_cancel)
        btn_row.addWidget(btn_export)
        layout.addLayout(btn_row)

    def _on_format_changed(self):
        path = self.path_edit.text()
        if self.rb_pdf.isChecked():
            path = os.path.splitext(path)[0] + ".pdf"
        else:
            path = os.path.splitext(path)[0] + ".xlsx"
        self.path_edit.setText(path)

    def _browse(self):
        if self.rb_pdf.isChecked():
            filter_str = "PDF files (*.pdf)"
            ext = ".pdf"
        else:
            filter_str = "Excel files (*.xlsx)"
            ext = ".xlsx"

        path, _ = QFileDialog.getSaveFileName(
            self, "保存先を選択", self.path_edit.text(), filter_str
        )
        if path:
            if not path.endswith(ext):
                path += ext
            self.path_edit.setText(path)

    def _on_export(self):
        path = self.path_edit.text().strip()
        if not path:
            QMessageBox.warning(self, "エラー", "保存先を指定してください")
            return

        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)

        try:
            if self.rb_pdf.isChecked():
                from export.pdf_exporter import export_pdf
                export_pdf(path, self.period, self.employees, self.assignments)
            else:
                from export.excel_exporter import export_excel
                export_excel(path, self.period, self.employees, self.assignments)

            QMessageBox.information(
                self, "出力完了",
                f"出力しました:\n{path}"
            )
            self.accept()
        except Exception as e:
            QMessageBox.critical(self, "出力エラー", f"出力に失敗しました:\n{e}")
