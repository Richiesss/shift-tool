"""Excel出力モジュール（従業員×日付 マトリクス形式）

セルフォーマット: 1日=3列(開始時刻 / 区切り("-")または備考 / 終了時刻)
  通常シフト   : "13" / "-" / "22.5"
  備考付きシフト: "6.5" / "オムレツ" / "15"
  有給・休み等 : 3列マージして "有給" / "-" / "休" などを中央表示
"""
from __future__ import annotations
from datetime import date
from collections import defaultdict
from openpyxl import Workbook
from openpyxl.styles import PatternFill, Font, Alignment, Border, Side
from openpyxl.utils import get_column_letter
from openpyxl.worksheet.properties import PageSetupProperties
from models.employee import Employee
from models.schedule import SchedulePeriod
from utils.constants import TimeSlot, Position, SHIFT_CONSTRAINTS
from utils.holidays import holiday_set

# ── スタイル定数 ─────────────────────────────────────────────────────────
THIN = Side(style="thin",   color="CBD5E1")
MED  = Side(style="medium", color="94A3B8")
BORDER     = Border(left=THIN, right=THIN, top=THIN, bottom=THIN)
BORDER_MED = Border(left=MED,  right=MED,  top=MED,  bottom=MED)

FILL_HDR     = PatternFill("solid", fgColor="1E3A5F")   # 濃紺（タイトル）
FILL_NAME    = PatternFill("solid", fgColor="E0F2FE")   # 水色（名前列）
FILL_SAT_H   = PatternFill("solid", fgColor="93C5FD")   # 青（土曜ヘッダー）
FILL_SUN_H   = PatternFill("solid", fgColor="FCA5A5")   # 赤（日曜ヘッダー）
FILL_WD_H    = PatternFill("solid", fgColor="E2E8F0")   # 薄グレー（平日ヘッダー）
FILL_SAT_D   = PatternFill("solid", fgColor="EFF6FF")   # 極薄青（土曜データ）
FILL_SUN_D   = PatternFill("solid", fgColor="FFF1F2")   # 極薄赤（日曜データ）
FILL_LEAVE   = PatternFill("solid", fgColor="D1FAE5")   # 緑（有給）
FILL_FT_OFF  = PatternFill("solid", fgColor="FCA5A5")   # 赤塗りつぶし（正社員希望休）
FILL_AM_ONLY = PatternFill("solid", fgColor="DBEAFE")   # 水色（朝のみ可）
FILL_PM_ONLY = PatternFill("solid", fgColor="FED7AA")   # オレンジ（晩のみ可）
FILL_WHITE   = PatternFill("solid", fgColor="FFFFFF")   # 白（通常・白地）
FILL_EVEN    = PatternFill("solid", fgColor="FFFFFF")   # 白（偶数行も白で統一）
FILL_SUMMARY = PatternFill("solid", fgColor="F8FAFC")   # 集計行・新規メモ/見込行
# 備考欄：黄色網掛け（lightGray パターン × 黄色 on 白）
FILL_MEMO    = PatternFill(patternType="lightGray", fgColor="FDE047", bgColor="FFFFFF")

FONT_TITLE  = Font(color="FFFFFF", bold=True, size=12)
FONT_HDR    = Font(color="334155", bold=True, size=9)
FONT_SAT    = Font(color="1D4ED8", bold=True, size=9)   # 土曜：青
FONT_SUN    = Font(color="DC2626", bold=True, size=9)   # 日曜：赤
FONT_NAME   = Font(bold=True, size=9)
FONT_DATA   = Font(size=9)
FONT_NOTE   = Font(size=8, bold=True)
FONT_LEAVE  = Font(color="166534", bold=True, size=9)   # 有給：緑
FONT_FT_OFF = Font(color="DC2626", bold=True, size=9)   # 正社員希望休：赤
FONT_AM_ONLY = Font(color="1E40AF", bold=True, size=9)  # 朝のみ可：青
FONT_PM_ONLY = Font(color="C2410C", bold=True, size=9)  # 晩のみ可：オレンジ
FONT_OFF    = Font(color="9CA3AF", size=9)
FONT_SUM    = Font(size=8)

ALIGN_C = Alignment(horizontal="center", vertical="center", wrap_text=False)
ALIGN_L = Alignment(horizontal="left",   vertical="center")
ALIGN_W = Alignment(horizontal="center", vertical="center", wrap_text=True)

DAY_JP = ["月", "火", "水", "木", "金", "土", "日"]

# 日付ブロックの列内訳（開始時刻 / 区切り・備考 / 終了時刻）
COLS_PER_DAY = 3


# ── ユーティリティ ────────────────────────────────────────────────────────

def _to_decimal(time_str: str) -> str:
    """'HH:MM' → 小数時刻文字列 ('5.75', '22.5', '13' など)"""
    if not time_str:
        return ""
    try:
        h, m = map(int, time_str.split(":"))
        if m == 0:
            return str(h)
        frac = m / 60.0
        val = h + frac
        # 小数点以下2桁、末尾0を除去
        s = f"{val:.2f}".rstrip("0").rstrip(".")
        return s
    except Exception:
        return time_str


def _get_shift_cells(
    emp_id: int,
    date_str: str,
    assignments: dict,
    req_map: dict,
) -> tuple[str, str, str, str]:
    """
    (開始時刻セル, 区切り/備考セル, 終了時刻セル, スタイル種別) を返す。
    スタイル種別: 'assigned' | 'assigned_note' | 'leave' | 'off' | 'ft_off' | 'am_only' | 'pm_only'

    表示フォーマット:
      通常シフト   : ("13", "-", "22.5")
      備考付きシフト: ("6.5", "オムレツ", "15")
      有給         : ("", "有給", "")
      休み         : ("", "-", "")

    assignments の値は (position_value, is_reinforcement, reinf_start, reinf_end) の4タプル。
    """
    from utils.shift_patterns import PATTERN_MAP

    req  = req_map.get((emp_id, date_str))
    note = (req.note or "").strip() if req else ""

    # 正社員希望休
    if req and req.pattern_id == "off_request":
        return "", "休", "", "ft_off"

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
        return "", "-", "", "off"

    # 4タプルから位置情報を展開
    b_pos, b_is_reinf, b_rs, b_re = b_raw if b_raw else (None, False, None, None)
    d_pos, d_is_reinf, d_rs, d_re = d_raw if d_raw else (None, False, None, None)

    # 応援要員は reinf_start/reinf_end を優先して使用
    if b_is_reinf or d_is_reinf:
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
    if req and req.pattern_id == "double":
        s, e = "6", "23"
    elif req and req.pattern_id == "custom" and req.custom_start and req.custom_end:
        s = _to_decimal(req.custom_start)
        e = _to_decimal(req.custom_end)
    elif req and req.pattern_id:
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


def _cell(ws, row, col, value="", fill=None, font=None, align=None, border=None):
    """セルを設定して返す"""
    c = ws.cell(row, col, value)
    if fill:   c.fill      = fill
    if font:   c.font      = font
    if align:  c.alignment = align
    if border: c.border    = border
    return c


def _cell_merged(ws, row, start_col, end_col, value="", fill=None, font=None, align=None, border=None):
    """指定範囲を横方向にマージしてセルを設定する（日付1日分=3列など）"""
    for c in range(start_col, end_col + 1):
        cell = ws.cell(row, c)
        if fill:   cell.fill   = fill
        if border: cell.border = border
    ws.merge_cells(start_row=row, start_column=start_col, end_row=row, end_column=end_col)
    top = ws.cell(row, start_col, value)
    if font:  top.font = font
    if align: top.alignment = align
    return top


# ── メイン出力 ────────────────────────────────────────────────────────────

def export_excel(
    path: str,
    period: SchedulePeriod,
    employees: list[Employee],
    assignments: dict,
):
    """
    シフト表を Excel に出力する。

    フォーマット:
      行: 従業員（1行1人）
      列: 日付（1日=3列: 開始時刻 / 区切り・備考 / 終了時刻）
      両端に氏名列
      ヘッダー部に メモ1・メモ2・朝食見込・夜予約 の行（現状は空欄でレイアウトのみ）
    """
    from db import repositories as repo
    requests = repo.get_shift_requests(period.id)
    req_map  = {(r.employee_id, r.date): r for r in requests}
    notes    = repo.get_schedule_notes(period.id)  # 日付メモ

    wb = Workbook()
    ws = wb.active
    ws.title = f"シフト表_{period.start_date}"

    dates    = period.date_range()
    n        = len(dates)
    holidays = holiday_set(dates)

    # 列インデックス
    C_NAME_L = 1                    # 氏名（左）
    C_DATA   = 2                    # データ先頭
    C_NAME_R = C_DATA + n * COLS_PER_DAY  # 氏名（右）

    # ──────────────────────────────────────────────────────────────────
    # 行 1: 期間タイトル
    # ──────────────────────────────────────────────────────────────────
    start_d  = date.fromisoformat(period.start_date)
    title    = f"{start_d.month}月 シフト表　　{period.start_date} 〜 {period.end_date}"
    ws.merge_cells(start_row=1, start_column=1, end_row=1, end_column=C_NAME_R)
    _cell(ws, 1, 1, title, fill=FILL_HDR, font=FONT_TITLE, align=ALIGN_C)
    ws.row_dimensions[1].height = 22

    # ──────────────────────────────────────────────────────────────────
    # 行 2: 日付番号
    # ──────────────────────────────────────────────────────────────────
    _cell(ws, 2, C_NAME_L, "氏名",  fill=FILL_NAME, font=FONT_NAME, align=ALIGN_C, border=BORDER)
    _cell(ws, 2, C_NAME_R, "氏名",  fill=FILL_NAME, font=FONT_NAME, align=ALIGN_C, border=BORDER)
    for i, d in enumerate(dates):
        start_col = C_DATA + i * COLS_PER_DAY
        end_col   = start_col + COLS_PER_DAY - 1
        dow  = d.weekday()
        is_h = d.isoformat() in holidays
        fill = FILL_SUN_H if (dow == 6 or is_h) else (FILL_SAT_H if dow == 5 else FILL_WD_H)
        font = FONT_SUN   if (dow == 6 or is_h) else (FONT_SAT   if dow == 5 else FONT_HDR)
        _cell_merged(ws, 2, start_col, end_col, d.day, fill=fill, font=font, align=ALIGN_C, border=BORDER)
    ws.row_dimensions[2].height = 18

    # ──────────────────────────────────────────────────────────────────
    # 行 3: 曜日
    # ──────────────────────────────────────────────────────────────────
    _cell(ws, 3, C_NAME_L, "", fill=FILL_NAME, border=BORDER)
    _cell(ws, 3, C_NAME_R, "", fill=FILL_NAME, border=BORDER)
    for i, d in enumerate(dates):
        start_col = C_DATA + i * COLS_PER_DAY
        end_col   = start_col + COLS_PER_DAY - 1
        dow  = d.weekday()
        is_h = d.isoformat() in holidays
        fill = FILL_SUN_H if (dow == 6 or is_h) else (FILL_SAT_H if dow == 5 else FILL_WD_H)
        font = FONT_SUN   if (dow == 6 or is_h) else (FONT_SAT   if dow == 5 else FONT_HDR)
        label = DAY_JP[dow] + ("(祝)" if is_h else "")
        _cell_merged(ws, 3, start_col, end_col, label, fill=fill, font=font, align=ALIGN_C, border=BORDER)
    ws.row_dimensions[3].height = 15

    # ──────────────────────────────────────────────────────────────────
    # 行 4: 備考欄（黄色網掛け）
    # ──────────────────────────────────────────────────────────────────
    _cell(ws, 4, C_NAME_L, "備考", fill=FILL_MEMO,
          font=Font(size=8, color="78350F", bold=True), align=ALIGN_C, border=BORDER)
    _cell(ws, 4, C_NAME_R, "", fill=FILL_MEMO, border=BORDER)
    for i, d in enumerate(dates):
        start_col = C_DATA + i * COLS_PER_DAY
        end_col   = start_col + COLS_PER_DAY - 1
        note = notes.get(d.isoformat(), "")
        _cell_merged(ws, 4, start_col, end_col, note, fill=FILL_MEMO,
                      font=Font(size=8, color="78350F"), align=ALIGN_L, border=BORDER)
    ws.row_dimensions[4].height = 16

    # ──────────────────────────────────────────────────────────────────
    # 行 5-8: メモ1・メモ2・朝食見込・夜予約（SDU_shift.xlsx準拠のレイアウト、現状は空欄）
    # ──────────────────────────────────────────────────────────────────
    EXTRA_ROWS = [
        (5, "メモ1",   36),
        (6, "メモ2",   18),
        (7, "朝食見込", 18),
        (8, "夜予約",   18),
    ]
    for row_num, label, height in EXTRA_ROWS:
        _cell(ws, row_num, C_NAME_L, label, fill=FILL_SUMMARY, font=FONT_HDR, align=ALIGN_C, border=BORDER)
        _cell(ws, row_num, C_NAME_R, "", fill=FILL_SUMMARY, border=BORDER)
        for i in range(n):
            start_col = C_DATA + i * COLS_PER_DAY
            end_col   = start_col + COLS_PER_DAY - 1
            _cell_merged(ws, row_num, start_col, end_col, "", fill=FILL_SUMMARY,
                          font=FONT_HDR, align=ALIGN_C, border=BORDER)
        ws.row_dimensions[row_num].height = height

    # ──────────────────────────────────────────────────────────────────
    # 行 9+: 従業員データ
    # ──────────────────────────────────────────────────────────────────
    R_DATA = 9
    for row_idx, emp in enumerate(employees):
        r       = R_DATA + row_idx
        is_even = (row_idx % 2 == 1)
        base_fill = FILL_EVEN if is_even else FILL_WHITE

        # 氏名（左）
        _cell(ws, r, C_NAME_L, emp.name, fill=FILL_NAME, font=FONT_NAME,
              align=ALIGN_L, border=BORDER)

        for i, d in enumerate(dates):
            start_col = C_DATA + i * COLS_PER_DAY
            sep_col   = start_col + 1
            end_col   = start_col + 2
            date_str  = d.isoformat()
            dow       = d.weekday()
            is_h      = date_str in holidays
            s_txt, m_txt, e_txt, style = _get_shift_cells(emp.id, date_str, assignments, req_map)

            if style == "ft_off":
                fill = FILL_FT_OFF
                font = FONT_FT_OFF
            elif style == "leave":
                fill = FILL_LEAVE
                font = FONT_LEAVE
            elif style == "am_only":
                fill = FILL_AM_ONLY
                font = FONT_AM_ONLY
            elif style == "pm_only":
                fill = FILL_PM_ONLY
                font = FONT_PM_ONLY
            elif style == "off":
                fill = FILL_SUN_D if (dow == 6 or is_h) else (FILL_SAT_D if dow == 5 else base_fill)
                font = FONT_OFF
            elif style == "assigned_note":
                fill = base_fill
                font = FONT_NOTE
            else:
                fill = base_fill
                font = FONT_DATA

            if style in ("assigned", "assigned_note"):
                _cell(ws, r, start_col, s_txt, fill=fill, font=font, align=ALIGN_C, border=BORDER)
                _cell(ws, r, sep_col,   m_txt, fill=fill, font=font, align=ALIGN_C, border=BORDER)
                _cell(ws, r, end_col,   e_txt, fill=fill, font=font, align=ALIGN_C, border=BORDER)
            else:
                _cell_merged(ws, r, start_col, end_col, m_txt, fill=fill, font=font, align=ALIGN_C, border=BORDER)

        # 氏名（右）
        _cell(ws, r, C_NAME_R, emp.name, fill=FILL_NAME, font=FONT_NAME,
              align=ALIGN_L, border=BORDER)

        ws.row_dimensions[r].height = 18

    # ──────────────────────────────────────────────────────────────────
    # 集計行（ポジション×スロット別の人数・熟練者数）
    # ──────────────────────────────────────────────────────────────────
    count_map   = defaultdict(int)
    skilled_map = defaultdict(int)
    for (emp_id, ds, slot_v), asgn_val in assignments.items():
        pos_v = asgn_val[0] if isinstance(asgn_val, tuple) else asgn_val
        count_map[(ds, slot_v, pos_v)] += 1
        emp = next((e for e in employees if e.id == emp_id), None)
        if emp and emp.is_skilled(pos_v, slot_v):
            skilled_map[(ds, slot_v, pos_v)] += 1

    SLOT_ROWS = [
        ("朝食 ホール",    TimeSlot.BREAKFAST, Position.HALL),
        ("朝食 キッチン",  TimeSlot.BREAKFAST, Position.KITCHEN),
        ("ﾃﾞｨﾅｰ ホール",  TimeSlot.DINNER,    Position.HALL),
        ("ﾃﾞｨﾅｰ ｷｯﾁﾝ",   TimeSlot.DINNER,    Position.KITCHEN),
    ]
    R_SUM = R_DATA + len(employees)
    for rel, (lbl, slot, pos) in enumerate(SLOT_ROWS):
        r = R_SUM + rel
        _cell(ws, r, C_NAME_L, lbl, fill=FILL_SUMMARY,
              font=Font(bold=True, size=8), align=ALIGN_C, border=BORDER)

        key     = (slot, pos)
        const   = SHIFT_CONSTRAINTS.get(key, {})
        min_req = const.get("min", 0)
        min_sk  = const.get("min_skilled", 0)

        for i, d in enumerate(dates):
            start_col = C_DATA + i * COLS_PER_DAY
            end_col   = start_col + COLS_PER_DAY - 1
            ds  = d.isoformat()
            cnt = count_map[(ds, slot.value, pos.value)]
            sk  = skilled_map[(ds, slot.value, pos.value)]

            if cnt < min_req or sk < min_sk:
                fill = PatternFill("solid", fgColor="FEE2E2")
            elif cnt == min_req:
                fill = PatternFill("solid", fgColor="FEF3C7")
            else:
                fill = PatternFill("solid", fgColor="D1FAE5")

            _cell_merged(ws, r, start_col, end_col, f"{cnt}名\n熟{sk}",
                          fill=fill, font=FONT_SUM, align=ALIGN_W, border=BORDER)

        _cell(ws, r, C_NAME_R, "", fill=FILL_SUMMARY, border=BORDER)
        ws.row_dimensions[r].height = 26

    # ──────────────────────────────────────────────────────────────────
    # 列幅・印刷設定
    # ──────────────────────────────────────────────────────────────────
    ws.column_dimensions[get_column_letter(C_NAME_L)].width = 10
    ws.column_dimensions[get_column_letter(C_NAME_R)].width = 10
    for i in range(n):
        start_col = C_DATA + i * COLS_PER_DAY
        ws.column_dimensions[get_column_letter(start_col)].width     = 4.5  # 開始時刻
        ws.column_dimensions[get_column_letter(start_col + 1)].width = 3    # 区切り・備考
        ws.column_dimensions[get_column_letter(start_col + 2)].width = 4.5  # 終了時刻

    ws.freeze_panes = "B9"  # 名前列・ヘッダー(1-8行)を固定

    # 印刷設定: A4横向き・1ページに収める
    ws.sheet_properties.pageSetUpPr = PageSetupProperties(fitToPage=True)
    ws.page_setup.orientation  = "landscape"
    ws.page_setup.paperSize    = 9   # A4
    ws.page_setup.fitToWidth   = 1   # 横1ページ
    ws.page_setup.fitToHeight  = 1   # 縦1ページ
    ws.print_title_rows        = "1:8"

    wb.save(path)
