"""Excel出力モジュール（従業員×日付 マトリクス形式）

セルフォーマット: "13 - 22.5" / "6.5 オムレツ 15" / "有給" / "-"
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

# ── スタイル定数 ─────────────────────────────────────────────────────────
THIN = Side(style="thin",   color="CBD5E1")
MED  = Side(style="medium", color="94A3B8")
BORDER     = Border(left=THIN, right=THIN, top=THIN, bottom=THIN)
BORDER_MED = Border(left=MED,  right=MED,  top=MED,  bottom=MED)

FILL_HDR     = PatternFill("solid", fgColor="1E3A5F")  # 濃紺（タイトル）
FILL_NAME    = PatternFill("solid", fgColor="DBEAFE")  # 水色（名前列）
FILL_SAT_H   = PatternFill("solid", fgColor="FDE68A")  # アンバー（土曜ヘッダー）
FILL_SUN_H   = PatternFill("solid", fgColor="FECACA")  # 薄赤（日曜ヘッダー）
FILL_WD_H    = PatternFill("solid", fgColor="BFDBFE")  # 水色（平日ヘッダー）
FILL_SAT_D   = PatternFill("solid", fgColor="FFFBEB")  # 淡アンバー（土曜データ）
FILL_SUN_D   = PatternFill("solid", fgColor="FFF5F5")  # 淡赤（日曜データ）
FILL_LEAVE   = PatternFill("solid", fgColor="FEF3C7")  # 黄（有給）
FILL_WHITE   = PatternFill("solid", fgColor="FFFFFF")  # 白（通常）
FILL_EVEN    = PatternFill("solid", fgColor="F8FAFC")  # 薄グレー（偶数行）
FILL_SUMMARY = PatternFill("solid", fgColor="F1F5F9")  # 集計行

FONT_TITLE  = Font(color="FFFFFF", bold=True, size=12)
FONT_HDR    = Font(color="1E3A5F", bold=True, size=9)
FONT_SAT    = Font(color="92400E", bold=True, size=9)  # 土曜
FONT_SUN    = Font(color="991B1B", bold=True, size=9)  # 日曜
FONT_NAME   = Font(bold=True, size=9)
FONT_DATA   = Font(size=9)
FONT_NOTE   = Font(size=8, bold=True)     # 備考付きシフト
FONT_LEAVE  = Font(color="92400E", bold=True, size=9)
FONT_OFF    = Font(color="9CA3AF", size=9)
FONT_SUM    = Font(size=8)

ALIGN_C = Alignment(horizontal="center", vertical="center", wrap_text=False)
ALIGN_L = Alignment(horizontal="left",   vertical="center")
ALIGN_W = Alignment(horizontal="center", vertical="center", wrap_text=True)

DAY_JP = ["月", "火", "水", "木", "金", "土", "日"]


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


def _get_shift_text(
    emp_id: int,
    date_str: str,
    assignments: dict,
    req_map: dict,
) -> tuple[str, str]:
    """
    (セルテキスト, スタイル種別) を返す。
    スタイル種別: 'assigned' | 'assigned_note' | 'leave' | 'off'

    表示フォーマット:
      通常シフト   : "13 - 22.5"
      備考付きシフト: "6.5 オムレツ 15"
      有給         : "有給"
      休み         : "-"
    """
    from utils.shift_patterns import PATTERN_MAP

    req  = req_map.get((emp_id, date_str))
    note = (req.note or "").strip() if req else ""

    # 有給チェック
    if "有給" in note:
        return "有給", "leave"

    # アサイン確認
    b_pos = assignments.get((emp_id, date_str, TimeSlot.BREAKFAST.value))
    d_pos = assignments.get((emp_id, date_str, TimeSlot.DINNER.value))

    if not b_pos and not d_pos:
        return "-", "off"

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

    # 備考を時刻の間に挟む形式
    if note:
        return f"{s} {note} {e}", "assigned_note"
    return f"{s} - {e}", "assigned"


def _slot_default(b_pos, d_pos) -> tuple[str, str]:
    if b_pos and d_pos:
        return "6", "23"
    elif b_pos:
        return "6", "11"
    else:
        return "17", "23"


def _cell(ws, row, col, value="", fill=None, font=None, align=None, border=None):
    """セルを設定して返す"""
    c = ws.cell(row, col, value)
    if fill:   c.fill      = fill
    if font:   c.font      = font
    if align:  c.alignment = align
    if border: c.border    = border
    return c


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
      列: 日付（1列1日）
      セル: 実勤務時間 ("13 - 22.5") または "有給" / "-"
      両端に氏名列
    """
    from db import repositories as repo
    requests = repo.get_shift_requests(period.id)
    req_map  = {(r.employee_id, r.date): r for r in requests}

    wb = Workbook()
    ws = wb.active
    ws.title = f"シフト表_{period.start_date}"

    dates = period.date_range()
    n     = len(dates)

    # 列インデックス
    C_NAME_L = 1          # 氏名（左）
    C_DATA   = 2          # データ先頭
    C_NAME_R = C_DATA + n # 氏名（右）

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
        col = C_DATA + i
        dow = d.weekday()
        fill = FILL_SAT_H if dow == 5 else (FILL_SUN_H if dow == 6 else FILL_WD_H)
        font = FONT_SAT   if dow == 5 else (FONT_SUN   if dow == 6 else FONT_HDR)
        _cell(ws, 2, col, d.day, fill=fill, font=font, align=ALIGN_C, border=BORDER)
    ws.row_dimensions[2].height = 18

    # ──────────────────────────────────────────────────────────────────
    # 行 3: 曜日
    # ──────────────────────────────────────────────────────────────────
    _cell(ws, 3, C_NAME_L, "", fill=FILL_NAME, border=BORDER)
    _cell(ws, 3, C_NAME_R, "", fill=FILL_NAME, border=BORDER)
    for i, d in enumerate(dates):
        col = C_DATA + i
        dow = d.weekday()
        fill = FILL_SAT_H if dow == 5 else (FILL_SUN_H if dow == 6 else FILL_WD_H)
        font = FONT_SAT   if dow == 5 else (FONT_SUN   if dow == 6 else FONT_HDR)
        _cell(ws, 3, col, DAY_JP[dow], fill=fill, font=font, align=ALIGN_C, border=BORDER)
    ws.row_dimensions[3].height = 15

    # ──────────────────────────────────────────────────────────────────
    # 行 4+: 従業員データ
    # ──────────────────────────────────────────────────────────────────
    R_DATA = 4
    for row_idx, emp in enumerate(employees):
        r       = R_DATA + row_idx
        is_even = (row_idx % 2 == 1)
        base_fill = FILL_EVEN if is_even else FILL_WHITE

        # 氏名（左）
        _cell(ws, r, C_NAME_L, emp.name, fill=FILL_NAME, font=FONT_NAME,
              align=ALIGN_L, border=BORDER)

        for i, d in enumerate(dates):
            col      = C_DATA + i
            date_str = d.isoformat()
            dow      = d.weekday()
            text, style = _get_shift_text(emp.id, date_str, assignments, req_map)

            if style == "leave":
                fill = FILL_LEAVE
                font = FONT_LEAVE
            elif style == "off":
                fill = FILL_SAT_D if dow == 5 else (FILL_SUN_D if dow == 6 else base_fill)
                font = FONT_OFF
            elif style == "assigned_note":
                fill = base_fill
                font = FONT_NOTE
            else:
                fill = base_fill
                font = FONT_DATA

            _cell(ws, r, col, text, fill=fill, font=font, align=ALIGN_C, border=BORDER)

        # 氏名（右）
        _cell(ws, r, C_NAME_R, emp.name, fill=FILL_NAME, font=FONT_NAME,
              align=ALIGN_L, border=BORDER)

        ws.row_dimensions[r].height = 18

    # ──────────────────────────────────────────────────────────────────
    # 集計行（ポジション×スロット別の人数・熟練者数）
    # ──────────────────────────────────────────────────────────────────
    count_map   = defaultdict(int)
    skilled_map = defaultdict(int)
    for (emp_id, ds, slot_v), pos_v in assignments.items():
        count_map[(ds, slot_v, pos_v)] += 1
        emp = next((e for e in employees if e.id == emp_id), None)
        if emp and emp.is_skilled(pos_v):
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
            col = C_DATA + i
            ds  = d.isoformat()
            cnt = count_map[(ds, slot.value, pos.value)]
            sk  = skilled_map[(ds, slot.value, pos.value)]

            if cnt < min_req or sk < min_sk:
                fill = PatternFill("solid", fgColor="FEE2E2")
            elif cnt == min_req:
                fill = PatternFill("solid", fgColor="FEF3C7")
            else:
                fill = PatternFill("solid", fgColor="D1FAE5")

            c = ws.cell(r, col, f"{cnt}名\n熟{sk}")
            c.fill      = fill
            c.font      = FONT_SUM
            c.alignment = ALIGN_W
            c.border    = BORDER

        _cell(ws, r, C_NAME_R, "", fill=FILL_SUMMARY, border=BORDER)
        ws.row_dimensions[r].height = 26

    # ──────────────────────────────────────────────────────────────────
    # 列幅・印刷設定
    # ──────────────────────────────────────────────────────────────────
    ws.column_dimensions[get_column_letter(C_NAME_L)].width = 10
    ws.column_dimensions[get_column_letter(C_NAME_R)].width = 10
    for i in range(n):
        ws.column_dimensions[get_column_letter(C_DATA + i)].width = 12

    ws.freeze_panes = "B4"  # 名前列・ヘッダーを固定

    # 印刷設定: A4横向き・1ページに収める
    ws.sheet_properties.pageSetUpPr = PageSetupProperties(fitToPage=True)
    ws.page_setup.orientation  = "landscape"
    ws.page_setup.paperSize    = 9   # A4
    ws.page_setup.fitToWidth   = 1   # 横1ページ
    ws.page_setup.fitToHeight  = 1   # 縦1ページ
    ws.print_title_rows        = "1:3"

    wb.save(path)
