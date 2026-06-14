"""PDF出力モジュール（SDU_shift テンプレート再現方式）

export/templates/SDU_shift_template.xlsx と同じ紙面レイアウトを reportlab で再現する。
Excel出力（テンプレート流し込み）を印刷したときと同じ見た目になるよう、
テンプレートの実測値（列幅・行高・フォントサイズ・罫線）を縮尺して描画する。

紙面構成（1日 = 3列: 開始時刻 / 区切り("-")・備考 / 終了時刻、日付ブロックは期間日数分）:
  日付・曜日 / メモ1(3行) / メモ2 / 朝食見込・夜予約
  キッチン社員(2枠) / キッチンA・P(18枠)
  2ブロック目ヘッダー（日付・曜日・朝食見込・夜予約）
  ホール社員(3枠) / ホールA・P(16枠)
  フッター（日付・曜日）
枠が足りないグループは行を追加する。印刷設定は A4横・1ページフィットを再現。
"""
from __future__ import annotations
from datetime import date
import os

from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib import colors
from reportlab.platypus import Table, TableStyle, Paragraph
from reportlab.pdfgen import canvas
from reportlab.lib.styles import ParagraphStyle
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.lib.enums import TA_CENTER

from models.employee import Employee
from models.schedule import SchedulePeriod
from utils.constants import TimeSlot, EmploymentType, PrimaryPosition
from utils.holidays import holiday_set

DAY_JP = ["月", "火", "水", "木", "金", "土", "日"]

# ── テンプレートのレイアウト実測値 ───────────────────────────────────────
N_BLOCKS     = 16     # テンプレートの最大日付ブロック数（実際は期間日数分だけ描画）
COLS_PER_DAY = 3      # 1日 = 3列（開始 / 区切り・備考 / 終了）
NAME_W_CH    = 17.63  # 氏名列幅（Excel文字単位）
DAY_W_CH     = 5.75   # 日付1列幅（Excel文字単位）
CH_PT        = 5.25   # Excel列幅1文字 ≒ 5.25pt

H_DATE  = 30.0        # 日付・曜日・従業員行の行高(pt)
H_MEMO1 = 15.0        # メモ1（×3行）
H_MEMO2 = 45.0        # メモ2
H_FORE  = 39.75       # 朝食見込・夜予約

F_TITLE = 24.0        # タイトル
F_FORE  = 20.0        # 朝食見込・夜予約の値
F_DATE  = 18.0        # 日付・曜日
F_NAME  = 14.0        # 氏名・ラベル
F_CELL  = 12.0        # シフトセル
F_MEMO  = 11.0        # メモ1の文字

K_FT_SLOTS, K_PT_SLOTS = 2, 18   # キッチン社員 / A・P の枠数
H_FT_SLOTS, H_PT_SLOTS = 3, 16   # ホール社員 / A・P の枠数

# 印刷余白（テンプレートのページ設定: 左右0.118in・上下0.157in）
MARGIN_LR = 8.5
MARGIN_TB = 11.3

# ステータス別のセル塗り（完成イメージ SDU_Shift_full.xlsx に準拠）
COL_NOTE  = colors.HexColor("#FFFF00")  # 黄: 備考メモ
COL_LEAVE = colors.HexColor("#B3E5A1")  # 緑: 有給（3セル全体）
COL_FTOFF = colors.HexColor("#FF0000")  # 赤: 社員の休み・指定休（3セル全体）
COL_SAT   = colors.HexColor("#60CBF3")  # 水色: 土曜の日付・曜日
COL_SUN   = colors.HexColor("#F6C6AC")  # オレンジ: 日曜・祝日の日付・曜日
COL_MEMO  = colors.HexColor("#FF0000")  # メモ1・メモ2の文字色（赤）

MED, THIN = 0.8, 0.3  # 罫線幅(pt): medium / thin


# ── フォント登録 ─────────────────────────────────────────────────────────

def _register_font() -> str:
    """日本語フォントを登録。利用不可の場合は Helvetica にフォールバック。

    元シートが全セル太字のため、同梱の太字フォント（BIZ UDGothic Bold, OFL）を
    最優先で使う。見つからない場合のみシステムフォントを探す。"""
    import glob
    from pathlib import Path
    candidates = [
        # 同梱フォント（太字）
        str(Path(__file__).parent / "fonts" / "BIZUDGothic-Bold.ttf"),
        # Linux: IPA Gothic (.ttf, ReportLab互換)
        "/usr/share/fonts/opentype/ipafont-gothic/ipag.ttf",
        "/usr/share/fonts/opentype/ipafont-gothic/ipagp.ttf",
        # Windows
        "C:/Windows/Fonts/meiryo.ttc",
        "C:/Windows/Fonts/YuGothR.ttc",
        "C:/Windows/Fonts/msgothic.ttc",
        # macOS
        "/System/Library/Fonts/ヒラギノ角ゴシック W3.ttc",
        "/Library/Fonts/Osaka.ttf",
    ]
    # glob fallback
    candidates += sorted(glob.glob("/usr/share/fonts/**/*.ttf", recursive=True))

    for path in candidates:
        if os.path.exists(path):
            try:
                font = TTFont("JpFont", path)
                # 日本語グリフを持つフォントのみ採用（豆腐化防止）
                if 0x65E5 in getattr(font.face, "charToGlyph", {}):  # 「日」
                    pdfmetrics.registerFont(font)
                    return "JpFont"
            except Exception:
                continue
    # TTFが見つからない場合は reportlab 内蔵のCJK CIDフォントを使う
    try:
        from reportlab.pdfbase.cidfonts import UnicodeCIDFont
        pdfmetrics.registerFont(UnicodeCIDFont("HeiseiKakuGo-W5"))
        return "HeiseiKakuGo-W5"
    except Exception:
        return "Helvetica"


# ── ユーティリティ ────────────────────────────────────────────────────────

def _to_decimal(time_str: str) -> str:
    """'HH:MM' → 小数時刻文字列 ('5.75', '22.5', '13' など)"""
    if not time_str:
        return ""
    try:
        h, m = map(int, time_str.split(":"))
        if m == 0:
            return str(h)
        val = h + m / 60.0
        return f"{val:.2f}".rstrip("0").rstrip(".")
    except Exception:
        return time_str


def _get_shift_cells(
    emp_id: int,
    date_str: str,
    assignments: dict,
    req_map: dict,
    cell_notes: dict,
) -> tuple[str, str, str, str]:
    """
    (開始時刻セル, 区切り/備考セル, 終了時刻セル, スタイル種別) を返す。
    スタイル種別: 'assigned' | 'assigned_note' | 'leave' | 'off' | 'ft_off' | 'am_only' | 'pm_only'

    assignments の値は (position_value, is_reinforcement, reinf_start, reinf_end) の4タプル。
    """
    from utils.shift_patterns import PATTERN_MAP

    req  = req_map.get((emp_id, date_str))
    note = (req.note or "").strip() if req else ""
    # スタッフ個別コメント（セル単位のメモ）があれば優先して区切りセルに表示
    cnote = (cell_notes.get((emp_id, date_str)) or "").strip()
    if cnote:
        note = cnote

    # 正社員希望休
    if req and req.pattern_id == "off_request":
        return "", "-", "", "ft_off"

    # 有給チェック（paid_leave パターン or メモに「有給」）
    if (req and req.pattern_id == "paid_leave") or "有給" in note:
        return "", "有給", "", "leave"

    # アサイン確認
    b_raw = assignments.get((emp_id, date_str, TimeSlot.BREAKFAST.value))
    d_raw = assignments.get((emp_id, date_str, TimeSlot.DINNER.value))

    if not b_raw and not d_raw:
        if req and req.pattern_id == "am_only":
            return "", "朝のみ可", "", "am_only"
        if req and req.pattern_id == "pm_only":
            return "", "晩のみ可", "", "pm_only"
        if cnote:
            return "", cnote, "", "assigned_note"
        return "", "-", "", "off"

    # 4タプルから位置情報を展開
    b_pos, _b_is_reinf, b_rs, b_re = b_raw if b_raw else (None, False, None, None)
    d_pos, _d_is_reinf, d_rs, d_re = d_raw if d_raw else (None, False, None, None)

    # 応援・希望時間からの変更は reinf_start/reinf_end を優先して使用
    if (b_rs and b_re) or (d_rs and d_re):
        if b_raw and not d_raw and b_rs and b_re:
            s, e = _to_decimal(b_rs), _to_decimal(b_re)
        elif d_raw and not b_raw and d_rs and d_re:
            s, e = _to_decimal(d_rs), _to_decimal(d_re)
        elif b_raw and d_raw:
            s = _to_decimal(b_rs) if b_rs else "6"
            e = _to_decimal(d_re) if d_re else "23"
        else:
            s, e = _slot_default(b_pos, d_pos)
        if note:
            return s, note, e, "assigned_note"
        return s, "-", e, "assigned"

    # 時刻取得（パターンから）
    # 「ダブル」は朝食・ディナー両方が割当済みの場合のみ通し表示にする。
    # 片方のみの割当の場合は実際の割当時間帯を表示する。
    if req and req.pattern_id == "double" and b_raw and d_raw:
        s, e = "6", "23"
    elif req and req.pattern_id == "custom" and req.custom_start and req.custom_end:
        s = _to_decimal(req.custom_start)
        e = _to_decimal(req.custom_end)
    elif req and req.pattern_id and req.pattern_id != "double":
        p = PATTERN_MAP.get(req.pattern_id)
        if p and p.start and p.end:
            s = _to_decimal(p.start)
            e = _to_decimal(p.end)
        else:
            s, e = _slot_default(b_pos, d_pos)
    else:
        s, e = _slot_default(b_pos, d_pos)

    # 備考を時刻の間（区切りセル）に挟む形式
    if note:
        return s, note, e, "assigned_note"
    return s, "-", e, "assigned"


def _slot_default(b_pos, d_pos) -> tuple[str, str]:
    """ポジション別のデフォルト勤務時間を返す（HH:MM を小数時刻に変換）"""
    from db import repositories as repo
    pos_key = b_pos or d_pos or "hall"  # ポジション名 ("hall" or "kitchen")
    if b_pos and d_pos:
        s = _to_decimal(repo.get_app_setting(f"ft_{pos_key}_breakfast_start", "06:00"))
        e = _to_decimal(repo.get_app_setting(f"ft_{pos_key}_dinner_end",      "22:00"))
    elif b_pos:
        s = _to_decimal(repo.get_app_setting(f"ft_{pos_key}_breakfast_start", "06:00"))
        e = _to_decimal(repo.get_app_setting(f"ft_{pos_key}_breakfast_end",   "11:00"))
    else:
        s = _to_decimal(repo.get_app_setting(f"ft_{pos_key}_dinner_start", "17:00"))
        e = _to_decimal(repo.get_app_setting(f"ft_{pos_key}_dinner_end",   "22:00"))
    return s, e


def _block_col(i: int) -> int:
    """日付ブロック i (0始まり) の先頭列（テーブル内インデックス、左氏名列=0）"""
    return 1 + i * COLS_PER_DAY


# ── メイン出力 ────────────────────────────────────────────────────────────

def export_pdf(
    path: str,
    period: SchedulePeriod,
    employees: list[Employee],
    assignments: dict,
):
    """SDU_shift テンプレートと同じ紙面の PDF を出力する（A4横・1ページフィット）"""
    from db import repositories as repo
    requests = repo.get_shift_requests(period.id)
    req_map  = {(r.employee_id, r.date): r for r in requests}
    notes       = repo.get_schedule_notes(period.id)  # 全体向けお知らせ → メモ1欄
    staff_notes = repo.get_staff_notes(period.id)     # 社員向けお知らせ → メモ2欄
    reserves    = repo.get_reservation_counts(period.id)
    cell_notes  = repo.get_cell_notes(period.id)      # スタッフ個別コメント → 区切りセル

    font = _register_font()

    dates    = list(period.date_range())[:N_BLOCKS]
    n        = len(dates)
    holidays = holiday_set(dates)

    # ── 従業員をテンプレートの4グループに分類（Excel出力と同一ロジック）──
    def _out_pos(e):
        return e.output_position or e.primary_position

    def _is_ft(e):
        return e.employment_type == EmploymentType.FULL_TIME

    kitchen = [e for e in employees if _out_pos(e) == PrimaryPosition.KITCHEN]
    hall    = [e for e in employees if _out_pos(e) != PrimaryPosition.KITCHEN]
    k_ft = [e for e in kitchen if _is_ft(e)]
    k_pt = [e for e in kitchen if not _is_ft(e)]
    h_ft = [e for e in hall if _is_ft(e)]
    h_pt = [e for e in hall if not _is_ft(e)]

    k_ft_n = max(K_FT_SLOTS, len(k_ft))
    k_pt_n = max(K_PT_SLOTS, len(k_pt))
    h_ft_n = max(H_FT_SLOTS, len(h_ft))
    h_pt_n = max(H_PT_SLOTS, len(h_pt))

    # ── タイトル ───────────────────────────────────────────────────────
    start_d = date.fromisoformat(period.start_date)
    half    = "前半" if start_d.day <= 15 else "後半"
    draft   = "" if period.status == "confirmed" else "(案)"
    title   = f"{start_d.month}月　{half}{draft}"

    # ── 縮尺計算（A4横1ページフィットの再現）───────────────────────────
    page_w, page_h = landscape(A4)
    avail_w = page_w - 2 * MARGIN_LR
    avail_h = page_h - 2 * MARGIN_TB

    n_cols    = 2 + n * COLS_PER_DAY  # 月の日数に合わせブロック数を可変にし空白を作らない
    natural_w = (2 * NAME_W_CH + n * COLS_PER_DAY * DAY_W_CH) * CH_PT

    # 行構成: ヘッダー8行 + K枠 + 2ブロック目4行 + H枠 + フッター2行
    row_heights_nat: list[float] = []
    row_heights_nat += [H_DATE, H_DATE, H_MEMO1, H_MEMO1, H_MEMO1, H_MEMO2, H_FORE, H_FORE]
    row_heights_nat += [H_DATE] * (k_ft_n + k_pt_n)
    row_heights_nat += [H_DATE] * 4
    row_heights_nat += [H_DATE] * (h_ft_n + h_pt_n)
    row_heights_nat += [H_DATE] * 2
    row_heights_nat += [15.0]  # 凡例行
    natural_h = sum(row_heights_nat)

    sx = avail_w / natural_w   # 横は紙幅いっぱいに使う
    # 縦は1ページに収まるよう縮小（丸め誤差での改ページを防ぐため僅かに余裕を持たせる）
    sy = min(avail_h * 0.995 / natural_h, sx)
    f  = min(sx, sy)           # フォントは小さい方の縮尺に合わせる

    col_widths  = ([NAME_W_CH * CH_PT * sx]
                   + [DAY_W_CH * CH_PT * sx] * (n * COLS_PER_DAY)
                   + [NAME_W_CH * CH_PT * sx])
    row_heights = [h * sy for h in row_heights_nat]

    # ── 行インデックス ────────────────────────────────────────────────
    R_DATE1, R_DOW1, R_MEMO1, R_MEMO2, R_BF, R_DIN = 0, 1, 2, 5, 6, 7
    k_ft_row = 8
    k_pt_row = k_ft_row + k_ft_n
    r_date2  = k_pt_row + k_pt_n
    r_dow2, r_bf2, r_din2 = r_date2 + 1, r_date2 + 2, r_date2 + 3
    h_ft_row = r_date2 + 4
    h_pt_row = h_ft_row + h_ft_n
    r_date3  = h_pt_row + h_pt_n
    r_dow3   = r_date3 + 1
    r_legend = r_dow3 + 1
    n_rows   = r_legend + 1

    # ── データ行列とスタイル ──────────────────────────────────────────
    data = [["" for _ in range(n_cols)] for _ in range(n_rows)]
    cmds: list[tuple] = [
        ("FONTNAME",      (0, 0), (-1, -1), font),
        ("FONTSIZE",      (0, 0), (-1, -1), F_CELL * f),
        # LEADING を文字サイズに追従させないと縦中央からずれる（既定12pt固定のため）
        ("LEADING",       (0, 0), (-1, -1), F_CELL * f * 1.2),
        ("ALIGN",         (0, 0), (-1, -1), "CENTER"),
        ("VALIGN",        (0, 0), (-1, -1), "MIDDLE"),
        ("LEFTPADDING",   (0, 0), (-1, -1), 1),
        ("RIGHTPADDING",  (0, 0), (-1, -1), 1),
        ("TOPPADDING",    (0, 0), (-1, -1), 0),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 0),
        ("TEXTCOLOR",     (0, 0), (-1, -1), colors.black),
    ]

    # メモ1・メモ2は赤字18pt（完成イメージ準拠）
    memo_style = ParagraphStyle(
        "memo", fontName=font, fontSize=F_DATE * f, leading=F_DATE * f * 1.1,
        alignment=TA_CENTER, textColor=COL_MEMO)

    name_w = NAME_W_CH * CH_PT * sx
    cell_w = DAY_W_CH * CH_PT * sx

    def _shrink_size(text: str, base_pt: float, width_pt: float) -> float | None:
        """shrink_to_fit 相当: 文字列がセル幅を超える場合の縮小フォントサイズを返す。
        収まる場合は None（既定サイズのまま）。"""
        if not text:
            return None
        w = pdfmetrics.stringWidth(str(text), font, base_pt)
        avail = width_pt - 1.5
        if w <= avail:
            return None
        return max(2.5, base_pt * avail / w)

    def _set_title(c, r):
        """氏名列のタイトルセル（文字列で書き、縦横とも中央に揃える）"""
        data[r][c] = title
        sz = _shrink_size(title, F_TITLE * f, name_w) or F_TITLE * f
        cmds.append(("FONTSIZE", (c, r), (c, r), sz))
        cmds.append(("LEADING",  (c, r), (c, r), sz * 1.2))

    def _span(c0, r0, c1, r1):
        cmds.append(("SPAN", (c0, r0), (c1, r1)))

    def _font_size(row, size, c0=0, c1=-1):
        cmds.append(("FONTSIZE", (c0, row), (c1, row), size * f))
        cmds.append(("LEADING",  (c0, row), (c1, row), size * f * 1.2))

    # ── ヘッダー（日付・曜日・メモ1・メモ2・朝食見込・夜予約）──────────
    def _fill_date_rows(r_date, r_dow):
        for i in range(n):
            c0 = _block_col(i)
            _span(c0, r_date, c0 + 2, r_date)
            _span(c0, r_dow,  c0 + 2, r_dow)
            if i < n:
                d = dates[i]
                data[r_date][c0] = str(d.day)
                data[r_dow][c0]  = DAY_JP[d.weekday()]
                # 土曜=水色 / 日曜・祝日=オレンジ
                if d.weekday() == 5:
                    cmds.append(("BACKGROUND", (c0, r_date), (c0 + 2, r_dow), COL_SAT))
                elif d.weekday() == 6 or d.isoformat() in holidays:
                    cmds.append(("BACKGROUND", (c0, r_date), (c0 + 2, r_dow), COL_SUN))
        _font_size(r_date, F_DATE)
        _font_size(r_dow,  F_DATE)

    _fill_date_rows(R_DATE1, R_DOW1)
    # タイトル（左右氏名列、日付〜メモ1の5行ぶんを縦マージ）
    _span(0, R_DATE1, 0, R_MEMO1 + 2)
    _span(n_cols - 1, R_DATE1, n_cols - 1, R_MEMO1 + 2)
    _set_title(0, R_DATE1)
    _set_title(n_cols - 1, R_DATE1)

    for i in range(n):
        c0 = _block_col(i)
        _span(c0, R_MEMO1, c0 + 2, R_MEMO1 + 2)   # メモ1: 3列×3行
        _span(c0, R_MEMO2, c0 + 2, R_MEMO2)
        _span(c0, R_BF,    c0 + 2, R_BF)
        _span(c0, R_DIN,   c0 + 2, R_DIN)
        if i < n:
            ds    = dates[i].isoformat()
            note  = notes.get(ds, "")
            note2 = staff_notes.get(ds, "")
            res   = reserves.get(ds, {})
            if note:
                data[R_MEMO1][c0] = Paragraph(note, memo_style)
            if note2:
                data[R_MEMO2][c0] = Paragraph(note2, memo_style)
            if res.get("breakfast") is not None:
                data[R_BF][c0] = str(res["breakfast"])
            if res.get("dinner") is not None:
                data[R_DIN][c0] = str(res["dinner"])
    data[R_BF][0]           = "朝食見込"
    data[R_BF][n_cols - 1]  = "朝食見込"
    data[R_DIN][0]          = "夜予約"
    data[R_DIN][n_cols - 1] = "夜予約"
    _font_size(R_BF,  F_FORE)
    _font_size(R_DIN, F_FORE)
    _font_size(R_BF,  F_NAME, 0, 0)
    _font_size(R_BF,  F_NAME, n_cols - 1, n_cols - 1)
    _font_size(R_DIN, F_NAME, 0, 0)
    _font_size(R_DIN, F_NAME, n_cols - 1, n_cols - 1)

    # ── 2ブロック目ヘッダー・フッター ──────────────────────────────────
    _fill_date_rows(r_date2, r_dow2)
    _span(0, r_date2, 0, r_dow2)
    _span(n_cols - 1, r_date2, n_cols - 1, r_dow2)
    _set_title(0, r_date2)
    _set_title(n_cols - 1, r_date2)
    for i in range(n):
        c0 = _block_col(i)
        _span(c0, r_bf2,  c0 + 2, r_bf2)
        _span(c0, r_din2, c0 + 2, r_din2)
        if i < n:
            res = reserves.get(dates[i].isoformat(), {})
            if res.get("breakfast") is not None:
                data[r_bf2][c0] = str(res["breakfast"])
            if res.get("dinner") is not None:
                data[r_din2][c0] = str(res["dinner"])
    data[r_bf2][0]           = "朝食見込"
    data[r_bf2][n_cols - 1]  = "朝食見込"
    data[r_din2][0]          = "夜予約"
    data[r_din2][n_cols - 1] = "夜予約"
    _font_size(r_bf2,  F_FORE)
    _font_size(r_din2, F_FORE)
    for rr in (r_bf2, r_din2):
        _font_size(rr, F_NAME, 0, 0)
        _font_size(rr, F_NAME, n_cols - 1, n_cols - 1)

    _fill_date_rows(r_date3, r_dow3)
    _span(0, r_date3, 0, r_dow3)
    _span(n_cols - 1, r_date3, n_cols - 1, r_dow3)
    _set_title(0, r_date3)
    _set_title(n_cols - 1, r_date3)

    # ── 従業員行 ───────────────────────────────────────────────────────
    def _fill_group(start_row: int, slot_count: int, emps: list[Employee]):
        for j in range(slot_count):
            r = start_row + j
            _font_size(r, F_NAME, 0, 0)
            _font_size(r, F_NAME, n_cols - 1, n_cols - 1)
            if j >= len(emps):
                continue
            emp = emps[j]
            data[r][0]          = emp.name
            data[r][n_cols - 1] = emp.name
            # 氏名が列幅を超える場合は縮小（shrink_to_fit 再現）
            sz = _shrink_size(emp.name, F_NAME * f, name_w)
            if sz:
                for cc in (0, n_cols - 1):
                    cmds.append(("FONTSIZE", (cc, r), (cc, r), sz))
                    cmds.append(("LEADING",  (cc, r), (cc, r), sz * 1.2))
            for i, d in enumerate(dates):
                c0 = _block_col(i)
                s_txt, m_txt, e_txt, style = _get_shift_cells(
                    emp.id, d.isoformat(), assignments, req_map, cell_notes)
                data[r][c0]     = s_txt
                data[r][c0 + 1] = m_txt
                data[r][c0 + 2] = e_txt
                # 備考等が列幅を超える場合は縮小（shrink_to_fit 再現）
                for cc, txt in ((c0, s_txt), (c0 + 1, m_txt), (c0 + 2, e_txt)):
                    sz = _shrink_size(txt, F_CELL * f, cell_w)
                    if sz:
                        cmds.append(("FONTSIZE", (cc, r), (cc, r), sz))
                        cmds.append(("LEADING",  (cc, r), (cc, r), sz * 1.2))
                if style in ("assigned_note", "am_only", "pm_only"):
                    cmds.append(("BACKGROUND", (c0 + 1, r), (c0 + 1, r), COL_NOTE))
                elif style == "leave":
                    # 有給は3セル全体を緑塗り（完成イメージ準拠）
                    cmds.append(("BACKGROUND", (c0, r), (c0 + 2, r), COL_LEAVE))
                elif style == "ft_off" or (
                    style == "off" and emp.employment_type == EmploymentType.FULL_TIME
                ):
                    # 社員の休み・指定休は3セル全体を赤塗り
                    cmds.append(("BACKGROUND", (c0, r), (c0 + 2, r), COL_FTOFF))

    _fill_group(k_ft_row, k_ft_n, k_ft)
    _fill_group(k_pt_row, k_pt_n, k_pt)
    _fill_group(h_ft_row, h_ft_n, h_ft)
    _fill_group(h_pt_row, h_pt_n, h_pt)

    # ── 罫線（テンプレートの medium/thin 使い分けを再現）────────────────
    last = n_cols - 1
    # 縦線: 氏名列の両側と日付ブロック境界は medium
    for c in [0, 1] + [_block_col(i) for i in range(1, n)] + [last]:
        cmds.append(("LINEBEFORE", (c, 0), (c, -1), MED, colors.black))
    cmds.append(("LINEAFTER", (last, 0), (last, -1), MED, colors.black))

    def _hline(row, weight, where="below", data_only=False):
        """横罫線。data_only=True はタイトルの縦マージ内を貫通させないよう
        日付ブロック列のみに線を引く。"""
        cmd = "LINEBELOW" if where == "below" else "LINEABOVE"
        c0, c1 = (1, n_cols - 2) if data_only else (0, -1)
        cmds.append((cmd, (c0, row), (c1, row), weight, colors.black))

    # ヘッダー部（日付・曜日の区切りはタイトルセル(縦マージ)内なのでデータ列のみ）
    _hline(R_DATE1, MED, "above")
    _hline(R_DATE1, THIN, data_only=True)
    _hline(R_DOW1, MED, data_only=True)
    _hline(R_MEMO1 + 2, MED)
    _hline(R_MEMO2, MED)
    _hline(R_BF, MED)
    _hline(R_DIN, MED)
    # 従業員行: 行間 thin、ブロック末尾 medium
    for r in range(k_ft_row, r_date2):
        _hline(r, THIN)
    _hline(r_date2 - 1, MED)
    for r in range(h_ft_row, r_date3):
        _hline(r, THIN)
    _hline(r_date3 - 1, MED)
    # 2ブロック目ヘッダー
    _hline(r_date2, THIN, data_only=True)
    _hline(r_dow2, MED)
    _hline(r_bf2, MED)
    _hline(r_din2, MED)
    # フッター
    _hline(r_date3, THIN, data_only=True)
    _hline(r_dow3, MED)
    _hline(r_legend, MED)

    # ── 凡例行（黄=メモ / 赤=社員休 / 緑=有給）──────────────────────────
    data[r_legend][0] = "凡例:"
    for i, (bg, label) in enumerate(
            [(COL_NOTE, "メモ"), (COL_FTOFF, "社員休"), (COL_LEAVE, "有給")]):
        c0 = _block_col(i)
        _span(c0, r_legend, c0 + 2, r_legend)
        cmds.append(("BACKGROUND", (c0, r_legend), (c0 + 2, r_legend), bg))
        data[r_legend][c0] = label
    _font_size(r_legend, F_CELL)

    # ── ドキュメント生成（Canvas直描画で1ページを保証）─────────────────
    table = Table(data, colWidths=col_widths, rowHeights=row_heights)
    table.setStyle(TableStyle(cmds))

    canv = canvas.Canvas(path, pagesize=landscape(A4))
    canv.setTitle(f"シフト表 {period.start_date}〜{period.end_date}")
    _, table_h = table.wrapOn(canv, avail_w, avail_h)
    table.drawOn(canv, MARGIN_LR, page_h - MARGIN_TB - table_h)
    canv.save()
