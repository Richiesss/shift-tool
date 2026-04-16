"""ヘルプ・クレジット・バグ報告ダイアログ"""
from __future__ import annotations
from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QTabWidget,
    QLabel, QPushButton, QScrollArea, QWidget, QTextBrowser
)
from PyQt6.QtCore import Qt, QUrl
from PyQt6.QtGui import QFont, QDesktopServices
from utils.theme import theme


_HELP_HTML = """
<h2>SDU-Shift 使い方ガイド</h2>

<h3>📋 基本的な流れ</h3>
<ol>
  <li><b>従業員管理</b> — 従業員を登録します。<br>
    所属ポジション（ホール/キッチン専任）、勤務時間帯（朝食/ディナー専任）、
    習熟度（リーダー/ベテラン/メンバー/ビギナー）を設定してください。</li>
  <li><b>希望シフト入力</b> — 期間を設定し、従業員ごとに出勤希望を入力します。<br>
    シフトパターンを選択するか、カスタムで時刻を入力してください。</li>
  <li><b>シフト自動生成</b> — 希望を元に最適なシフトを自動生成します。<br>
    制約を満たせない場合はベストエフォートで生成し、不足箇所をメッセージで知らせます。</li>
  <li><b>シフト表示・編集</b> — 生成結果を確認・手動で調整します。<br>
    不足している枠には「応援要員」として任意のメンバーを追加できます。</li>
  <li><b>出力</b> — シフト表示・編集画面の「出力 →」ボタンから PDF または Excel でエクスポートできます。</li>
</ol>

<h3>⌨️ キーボードショートカット（希望シフト入力）</h3>
<table border="1" cellpadding="4" cellspacing="0">
  <tr><th>キー</th><th>動作</th></tr>
  <tr><td>← / →</td><td>前/次の従業員に切り替え</td></tr>
  <tr><td>0〜9</td><td>現在選択行のシフトパターンを番号で選択（0=休み）</td></tr>
</table>

<h3>🖱️ シフト表の操作</h3>
<ul>
  <li><b>日付ヘッダーをクリック</b> — その日の希望シフトをタイムライン表示します</li>
  <li><b>△（希望あり）セルをクリック</b> — ポジションを選択してアサインします</li>
  <li><b>空白（希望なし）セルをクリック</b> — 応援要員として追加できます（橙色で表示）</li>
  <li><b>アサイン済みセルをクリック</b> — アサインを削除します</li>
</ul>

<h3>💾 データの保存場所</h3>
<table border="1" cellpadding="4" cellspacing="0">
  <tr><th>環境</th><th>保存先</th></tr>
  <tr><td>macOS .app</td><td>~/Library/Application Support/SDU-Shift/shift_tool.db</td></tr>
  <tr><td>Windows EXE</td><td>%APPDATA%\\SDU-Shift\\shift_tool.db</td></tr>
  <tr><td>開発環境</td><td>~/.shift_tool/shift_tool.db</td></tr>
</table>

<h3>🔒 バックアップ</h3>
<p>「設定」画面のバックアップ機能で任意の場所に DB を保存できます。
アップデート前は必ずバックアップを取ることを推奨します。</p>
"""

_CREDITS_HTML = """
<h2>クレジット</h2>

<h3>開発</h3>
<p>Richiesss(島野 凌)</p>

<h3>使用ライブラリ</h3>
<table border="1" cellpadding="4" cellspacing="0">
  <tr><th>ライブラリ</th><th>用途</th><th>ライセンス</th></tr>
  <tr><td>PyQt6</td><td>GUI フレームワーク</td><td>GPL v3 / Commercial</td></tr>
  <tr><td>Google OR-Tools</td><td>CP-SAT ソルバー（最適化）</td><td>Apache 2.0</td></tr>
  <tr><td>SQLite</td><td>データベース</td><td>Public Domain</td></tr>
  <tr><td>openpyxl</td><td>Excel 出力</td><td>MIT</td></tr>
  <tr><td>reportlab</td><td>PDF 出力</td><td>BSD</td></tr>
  <tr><td>PyInstaller</td><td>実行ファイルのビルド</td><td>GPL v2</td></tr>
</table>
"""

_BUG_HTML = """
<h2>バグ報告・フィードバック</h2>

<p>バグの報告・機能リクエストは GitHub Issues(LINEでもいいよ)からお願いします。</p>

<h3>報告時に含めてほしい情報</h3>
<ul>
  <li>OS（Windows 10/11、macOS バージョン）</li>
  <li>アプリのバージョン（サイドバー下部に表示）</li>
  <li>再現手順（何をしたらどうなったか）</li>
  <li>エラーメッセージがあればそのテキスト</li>
</ul>

<p><a href="https://github.com/Richiesss/shift-tool/issues">
→ GitHub Issues を開く</a></p>

<h3>よくある問題</h3>
<ul>
  <li><b>Windows で起動しない</b> — SmartScreen の警告が出た場合は「詳細情報」→「実行」を選択してください</li>
  <li><b>macOS で開けない</b> — Finder で右クリック → 「開く」→「開く」で起動できます</li>
  <li><b>データが消えた</b> — 設定画面のバックアップから復元できます</li>
  <li><b>シフトが生成できない</b> — 希望シフト入力が完了しているか確認してください。
    制約に合わない場合はベストエフォートで生成されます</li>
</ul>
"""


class HelpDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("ヘルプ")
        self.setMinimumSize(620, 520)
        self._build_ui()

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setSpacing(8)

        tabs = QTabWidget()

        for title, html in [
            ("📖 使い方",    _HELP_HTML),
            ("👥 クレジット", _CREDITS_HTML),
            ("🐛 バグ報告",  _BUG_HTML),
        ]:
            browser = QTextBrowser()
            browser.setHtml(html)
            browser.setOpenLinks(False)
            browser.anchorClicked.connect(self._on_link_clicked)
            c = theme.c
            browser.setStyleSheet(
                f"background:{c['bg']}; color:{c['text']}; border:none; padding:8px;"
            )
            tabs.addTab(browser, title)

        layout.addWidget(tabs)

        btn = QPushButton("閉じる")
        btn.clicked.connect(self.accept)
        btn.setFixedHeight(32)
        layout.addWidget(btn)

    @staticmethod
    def _on_link_clicked(url: QUrl):
        QDesktopServices.openUrl(url)
