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
  <li><b>従業員管理</b> — 従業員を登録します。</li>
  <li><b>希望シフト入力</b> — 期間を設定し、出勤希望を従業員ごとに入力します。</li>
  <li><b>シフト自動生成</b> — 希望を元に最適なシフトを自動生成します。</li>
  <li><b>シフト表示・編集</b> — 生成結果を確認・手動調整します。</li>
  <li><b>出力</b> — PDF または Excel でエクスポートします。</li>
</ol>

<hr>

<h3>👤 従業員管理</h3>
<ul>
  <li><b>所属ポジション（必須）</b> — ホールまたはキッチンを選択してください。<br>
    「兼務可」にチェックすると、主所属外のポジションにも配置できます。</li>
  <li><b>勤務時間帯</b> — 朝食専任 / ディナー専任 / どちらでも を設定します。<br>
    正社員はどちらでも可（毎回希望入力不要）、アルバイトは希望に従います。</li>
  <li><b>習熟度</b> — ホール・キッチンそれぞれに設定します。<br>
    <b>リーダー</b> ≫ <b>ベテラン</b>（熟練者カウント対象）≫ <b>メンバー</b> ≫ <b>ビギナー</b></li>
  <li><b>固定シフト（アルバイトのみ）</b> — 毎週決まった曜日・時間帯に出るスタッフは固定パターンを設定すると希望入力を省略できます。</li>
  <li><b>固定不可日</b> — 特定日（イベント・試験等）に出られない日を登録します。</li>
  <li><b>アーカイブ</b> — 退職・長期休暇のスタッフは「削除」でアーカイブ化できます。過去のシフトデータはそのまま残ります。</li>
</ul>

<h3>📅 希望シフト入力</h3>
<ul>
  <li>期間（開始日〜終了日）を作成し、各スタッフの出勤希望を入力します。</li>
  <li><b>シフトパターン</b> — 朝食・ディナーそれぞれに時間帯パターンを選択します。</li>
  <li><b>ダブル勤務</b> — 朝食・ディナーの両方に出勤する場合は「ダブル」を選択します。</li>
  <li><b>カスタム時刻</b> — 定型パターンにない時刻は「カスタム」で直接入力できます。</li>
  <li><b>有給</b> — 備考欄に「有給」と記入するとシフト表上に「有給」と表示されます。</li>
  <li><b>Google Forms 連携</b> — Apps Script でフォームを作成し、CSV をインポートすることで一括入力が可能です（スクリプトは <code>scripts/ShiftFormGenerator.gs</code>）。</li>
</ul>
<table border="1" cellpadding="4" cellspacing="0">
  <tr><th>キー</th><th>動作（希望シフト入力画面）</th></tr>
  <tr><td>← / →</td><td>前/次の従業員に切り替え</td></tr>
  <tr><td>0〜9</td><td>現在選択行のパターンを番号で選択（0=休み）</td></tr>
</table>

<h3>⚙️ シフト自動生成</h3>
<ul>
  <li>「シフトを自動生成する」ボタンを押すと CP-SAT ソルバーが最適配置を計算します。</li>
  <li><b>優先度設定</b> — 人件費最小化・アルバイト希望充当・人員バランス均等化・深夜勤務分散（アルバイトのみ）を「低/中/高」で調整できます。</li>
  <li><b>自動生成の基本ルール</b>:
    <ul>
      <li>正社員の掛け持ち禁止（朝食とディナーの両方に出勤不可）</li>
      <li>朝食はアルバイト優先（アルバイトだけで充足する場合は正社員はアサインされません）</li>
      <li>ディナーは正社員優先（正社員だけで充足する場合はアルバイトはアサインされません）</li>
      <li>開店準備（open）や片付け（cleanup）の対応可能スタッフが要件を満たすよう自動配置</li>
    </ul>
  </li>
  <li>制約（最低人数・リーダー必要数）を満たせない日はベストエフォートで生成し、警告メッセージが表示されます。</li>
  <li>生成後は「シフト表の確認・編集へ →」ボタンで結果を確認できます。</li>
</ul>

<h3>🖱️ シフト表示・編集</h3>
<ul>
  <li><b>△（希望あり）セルをクリック</b> — ポジション（ホール/キッチン）を選択してアサインします。</li>
  <li><b>アサイン済みセルをクリック</b> — アサインを削除します。</li>
  <li><b>空白（希望なし）セルをクリック</b> — <b>応援要員</b>として追加できます（橙色表示）。<br>
    勤務開始・終了時刻を指定するダイアログが表示されます。</li>
  <li><b>集計行の色</b> — 緑＝制約クリア、黄＝最低人数ちょうど、赤＝制約違反。</li>
  <li><b>月間集計タブ</b> — 朝食/ディナー × ホール/キッチン の月間勤務回数が一覧で確認できます。</li>
  <li><b>確定する</b> — シフトを確定状態にします。確定後も解除して再編集できます。</li>
</ul>

<h3>📤 出力（PDF・Excel）</h3>
<ul>
  <li>シフト表示・編集画面の「出力 →」ボタンから PDF または Excel を選択します。</li>
  <li><b>PDF</b> — A4 横向き 1 枚。上段キッチン（行事メモ欄付き）・下段ホールの 2 段構成。フォントサイズは日数に応じて自動調整されます。</li>
  <li><b>Excel</b> — 従業員×日付のマトリクス形式。末尾に時間帯×ポジションの集計行あり。A4 横向き 1 ページに収まる設定で出力されます。</li>
</ul>

<h3>🔧 設定</h3>
<ul>
  <li><b>シフト人員設定</b> — 時間帯・ポジションごとの最低/最大人数・リーダー必要数を設定します。</li>
  <li><b>客数閾値・増員設定</b> — 予約客数の閾値と、超過時に自動で増員する人数を設定します。</li>
  <li><b>開店・片付け制約</b> — 朝食の開店準備（open）や片付け（cleanup）に必要な人数・リーダー数を設定します。</li>
  <li><b>社員時間設定</b> — 正社員が朝食・ディナーにアサインされた際の標準勤務時間を設定します。</li>
  <li><b>テーマ</b> — ライト / ダーク / システム設定に従う を切り替えられます。</li>
  <li><b>バックアップ</b> — DB ファイルを任意の場所に保存します。アップデート前に実行することを推奨します。</li>
  <li><b>インポート（復元）</b> — バックアップファイルから全データを復元します。現在のデータは上書きされます。</li>
</ul>

<h3>💾 データの保存場所</h3>
<table border="1" cellpadding="4" cellspacing="0">
  <tr><th>環境</th><th>保存先</th></tr>
  <tr><td>macOS .app</td><td>~/Library/Application Support/SDU-Shift/shift_tool.db</td></tr>
  <tr><td>Windows EXE</td><td>%APPDATA%\\SDU-Shift\\shift_tool.db</td></tr>
  <tr><td>開発環境</td><td>~/.shift_tool/shift_tool.db</td></tr>
</table>
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

<p>バグの報告・機能リクエストは GitHub Issues または LINE からお願いします。</p>

<p><a href="https://github.com/Richiesss/shift-tool/issues">→ GitHub Issues を開く</a></p>

<h3>報告時に含めてほしい情報</h3>
<ul>
  <li>OS（Windows 10/11、macOS バージョン）</li>
  <li>アプリのバージョン（サイドバー下部に表示）</li>
  <li>再現手順（何をしたらどうなったか、できるだけ具体的に）</li>
  <li>エラーメッセージがあればそのテキスト（スクリーンショットも歓迎）</li>
</ul>

<h3>よくある問題と対処法</h3>
<table border="1" cellpadding="6" cellspacing="0">
  <tr><th>症状</th><th>対処法</th></tr>
  <tr>
    <td>Windows で起動しない</td>
    <td>SmartScreen の警告が出た場合は「詳細情報」→「実行」を選択してください</td>
  </tr>
  <tr>
    <td>macOS で開けない</td>
    <td>Finder で右クリック →「開く」→「開く」で起動できます</td>
  </tr>
  <tr>
    <td>データが消えた</td>
    <td>設定画面の「インポート（復元）」からバックアップを復元できます</td>
  </tr>
  <tr>
    <td>シフトが生成できない（❌ エラー）</td>
    <td>希望シフト入力が完了しているか確認してください。<br>
        「シフト人員設定」の最低人数が希望者数を超えていると失敗します。設定を見直してください。</td>
  </tr>
  <tr>
    <td>PDF が 2 ページになる</td>
    <td>従業員数が多すぎる場合、フォントサイズが自動縮小されても収まらないことがあります。<br>
        期間を短くするか、従業員グループを分けて出力してください。</td>
  </tr>
  <tr>
    <td>Excel が 1 枚に収まらない</td>
    <td>印刷プレビューで「シートを 1 ページに収める」設定を確認してください。<br>
        ファイルはA4横向き・1枚設定で出力されますが、Excelの設定によっては変わることがあります。</td>
  </tr>
  <tr>
    <td>Google Forms の CSV が取り込めない</td>
    <td>スプレッドシートから「ファイル → ダウンロード → CSV」でエクスポートしたファイルを使用してください。<br>
        フォームの回答シートから直接ダウンロードしたファイルは形式が異なる場合があります。</td>
  </tr>
</table>
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
