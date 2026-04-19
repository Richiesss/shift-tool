"""PDF出力モジュール（従業員×日付 マトリクス形式）

セルフォーマット: "13 - 22.5" / "6.5 オムレツ 15" / "有給" / "-"
"""
from __future__ import annotations
from datetime import date
import os

from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.units import mm
from reportlab.lib import colors
from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer
from reportlab.lib.styles import ParagraphStyle
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.lib.enums import TA_CENTER

from models.employee import Employee
from models.schedule import SchedulePeriod
from utils.constants import TimeSlot, Position, PrimaryPosition

DAY_JP = ["月", "火", "水", "木", "金", "土", "日"]

# セルスタイル色
COL_HDR_TITLE = colors.HexColor("#1E3A5F")   # タイトル背景
COL_HDR_WD    = colors.HexColor("#BFDBFE")   # 平日ヘッダー
COL_HDR_SAT   = colors.HexColor("#FDE68A")   # 土曜ヘッダー
COL_HDR_SUN   = colors.HexColor("#FECACA")   # 日曜ヘッダー
COL_NAME      = colors.HexColor("#DBEAFE")   # 氏名列
COL_SAT_D     = colors.HexColor("#FFFBEB")   # 土曜データ（淡）
COL_SUN_D     = colors.HexColor("#FFF5F5")   # 日曜データ（淡）
COL_LEAVE     = colors.HexColor("#FEF3C7")   # 有給
COL_EVEN      = colors.HexColor("#F8FAFC")   # 偶数行
COL_WHITE     = colors.white
COL_GRID      = colors.HexColor("#CBD5E1")   # グリッド線
COL_TXT_SAT   = colors.HexColor("#92400E")   # 土曜文字
COL_TXT_SUN   = colors.HexColor("#991B1B")   # 日曜文字
COL_TXT_OFF   = colors.HexColor("#9CA3AF")   # 休み文字


# ── フォント登録 ─────────────────────────────────────────────────────────

def _register_font() -> str:
    """日本語フォントを登録。利用不可の場合は Helvetica にフォールバック。"""
    candidates = [
        # Windows
        "C:/Windows/Fonts/meiryo.ttc",
        "C:/Windows/Fonts/YuGothR.ttc",
        "C:/Windows/Fonts/msgothic.ttc",
        # macOS
        "/System/Library/Fonts/ヒラギノ角ゴシック W3.ttc",
        "/Library/Fonts/Osaka.ttf",
        # Linux
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
        "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc",
        "/usr/share/fonts/noto-cjk/NotoSansCJK-Regular.ttc",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    ]
    for path in candidates:
        if os.path.exists(path):
            try:
                pdfmetrics.registerFont(TTFont("JpFont", path))
                return "JpFont"
            except Exception:
                continue
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
    """
    from utils.shift_patterns import PATTERN_MAP

    req  = req_map.get((emp_id, date_str))
    note = (req.note or "").strip() if req else ""

    if "有給" in note:
        return "有給", "leave"

    b_pos = assignments.get((emp_id, date_str, TimeSlot.BREAKFAST.value))
    d_pos = assignments.get((emp_id, date_str, TimeSlot.DINNER.value))

    if not b_pos and not d_pos:
        return "-", "off"

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

    if note:
        return f"{s} {note} {e}", "assigned_note"
    return f"{s}-{e}", "assigned"


def _slot_default(b_pos, d_pos) -> tuple[str, str]:
    if b_pos and d_pos:
        return "6", "23"
    elif b_pos:
        return "6", "11"
    else:
        return "17", "23"


# ── メイン出力 ────────────────────────────────────────────────────────────

COL_EVENT_BG = colors.HexColor("#FFFDE7")  # 行事行の背景（淡黄）


def _build_block_table(
    block_emps, dates, assignments, req_map, col_widths,
    font_name, font_size, n, emp_h, hdr_h, dow_h, events_h, period_label,
):
    """
    従業員グループ1ブロック分のテーブルを生成する。

    行構成:
      Row 0 : 日付番号（期間ラベル付き）
      Row 1 : 曜日
      Row 2 : 行事メモ欄（空欄・手書き記入用）
      Row 3+: 従業員シフト行
    """
    n_block = len(block_emps)

    row_dates  = [period_label] + [str(d.day) for d in dates] + [period_label]
    row_dows   = [""] + [DAY_JP[d.weekday()] for d in dates] + [""]
    row_events = ["行事"] + [""] * n + [""]   # 行事メモ欄（空欄）

    emp_rows = []
    for emp in block_emps:
        row = [emp.name]
        for d in dates:
            text, _ = _get_shift_text(emp.id, d.isoformat(), assignments, req_map)
            row.append(text)
        row.append(emp.name)
        emp_rows.append(row)

    table_data  = [row_dates, row_dows, row_events] + emp_rows
    row_heights = [hdr_h, dow_h, events_h] + [emp_h] * n_block

    tbl = Table(table_data, colWidths=col_widths,
                rowHeights=row_heights, repeatRows=0)

    EMP_START = 3  # 従業員行の開始行インデックス

    cmds = [
        ("FONTNAME",      (0, 0), (-1, -1), font_name),
        ("FONTSIZE",      (0, 0), (-1, -1), font_size),
        ("ALIGN",         (0, 0), (-1, -1), "CENTER"),
        ("VALIGN",        (0, 0), (-1, -1), "MIDDLE"),
        ("GRID",          (0, 0), (-1, -1), 0.4, COL_GRID),
        ("TOPPADDING",    (0, 0), (-1, -1), 1),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 1),
        ("LEFTPADDING",   (0, 0), (-1, -1), 2),
        ("RIGHTPADDING",  (0, 0), (-1, -1), 2),
        # 左右 氏名列
        ("ALIGN",      (0, 0),  (0, -1),  "LEFT"),
        ("ALIGN",      (-1, 0), (-1, -1), "LEFT"),
        ("BACKGROUND", (0, EMP_START),  (0, -1),  COL_NAME),
        ("BACKGROUND", (-1, EMP_START), (-1, -1), COL_NAME),
        # ヘッダー行（日付・曜日）
        ("BACKGROUND", (1, 0), (n, 1), COL_HDR_WD),
        ("FONTSIZE",   (0, 0), (-1, 1), font_size + 1),
        # 期間ラベルセル（左右）: 濃紺背景・白文字
        ("BACKGROUND", (0, 0),  (0, 0),  COL_HDR_TITLE),
        ("BACKGROUND", (-1, 0), (-1, 0), COL_HDR_TITLE),
        ("TEXTCOLOR",  (0, 0),  (0, 0),  colors.white),
        ("TEXTCOLOR",  (-1, 0), (-1, 0), colors.white),
        # 行事行
        ("BACKGROUND", (0, 2),  (-1, 2), COL_EVENT_BG),
        ("FONTSIZE",   (0, 2),  (0, 2),  max(5, font_size - 1)),
        ("TEXTCOLOR",  (0, 2),  (0, 2),  COL_TXT_OFF),
        ("ALIGN",      (0, 2),  (0, 2),  "LEFT"),
        ("ALIGN",      (1, 2),  (-1, 2), "LEFT"),
    ]

    # 土日ヘッダー着色
    for i, d in enumerate(dates):
        col = i + 1
        dow = d.weekday()
        if dow == 5:
            cmds += [("BACKGROUND", (col, 0), (col, 1), COL_HDR_SAT),
                     ("TEXTCOLOR",  (col, 0), (col, 1), COL_TXT_SAT)]
        elif dow == 6:
            cmds += [("BACKGROUND", (col, 0), (col, 1), COL_HDR_SUN),
                     ("TEXTCOLOR",  (col, 0), (col, 1), COL_TXT_SUN)]

    # 従業員行の着色
    for row_idx, emp in enumerate(block_emps):
        r      = EMP_START + row_idx
        row_bg = COL_EVEN if row_idx % 2 == 1 else COL_WHITE
        cmds.append(("BACKGROUND", (1, r), (n, r), row_bg))
        for i, d in enumerate(dates):
            col      = i + 1
            date_str = d.isoformat()
            dow      = d.weekday()
            text, style = _get_shift_text(emp.id, date_str, assignments, req_map)
            if style == "leave":
                cmds.append(("BACKGROUND", (col, r), (col, r), COL_LEAVE))
                cmds.append(("TEXTCOLOR",  (col, r), (col, r), COL_TXT_SAT))
            elif style == "off":
                if dow == 5:
                    cmds.append(("BACKGROUND", (col, r), (col, r), COL_SAT_D))
                elif dow == 6:
                    cmds.append(("BACKGROUND", (col, r), (col, r), COL_SUN_D))
                cmds.append(("TEXTCOLOR", (col, r), (col, r), COL_TXT_OFF))

    tbl.setStyle(TableStyle(cmds))
    return tbl


def export_pdf(
    path: str,
    period: SchedulePeriod,
    employees: list[Employee],
    assignments: dict,
):
    """
    シフト表を PDF に出力する（横向き A4・1ページ）。

    レイアウト:
      上段: キッチン所属スタッフ
      下段: ホール所属スタッフ（primary_position が KITCHEN 以外 / 未設定）
      各段に行事メモ欄を設ける。集計行は出力しない。
    """
    from db import repositories as repo
    requests = repo.get_shift_requests(period.id)
    req_map  = {(r.employee_id, r.date): r for r in requests}

    font_name = _register_font()

    MARGIN = 5 * mm
    doc = SimpleDocTemplate(
        path,
        pagesize=landscape(A4),
        leftMargin=MARGIN, rightMargin=MARGIN,
        topMargin=MARGIN,  bottomMargin=MARGIN,
    )

    dates = list(period.date_range())
    n     = len(dates)

    # ── 従業員をキッチン／ホールに分類 ───────────────────────────────
    kitchen_emps = [e for e in employees
                    if e.primary_position == PrimaryPosition.KITCHEN]
    hall_emps    = [e for e in employees
                    if e.primary_position != PrimaryPosition.KITCHEN]

    # ── 列幅 ─────────────────────────────────────────────────────────
    page_w = landscape(A4)[0] - 2 * MARGIN
    name_w = 16 * mm
    data_w = (page_w - 2 * name_w) / n
    col_widths = [name_w] + [data_w] * n + [name_w]

    # 最長セル "5.75-14.75"(9文字) を基準にフォントサイズを算出
    # 0.6 = 典型的な等幅フォントの文字幅係数（pt/pt）
    font_size = max(5, min(8, int(data_w / (9 * 0.6))))

    start_d      = date.fromisoformat(period.start_date)
    period_label = f"{start_d.month}月"

    # ── レイアウト計算 ────────────────────────────────────────────────
    TITLE_H  = 16   # タイトル段落高さ (pt)
    GAP      = 4    # グループ間スペーサー (pt)
    HDR_H    = 11   # 日付番号行高さ
    DOW_H    = 9    # 曜日行高さ
    EVENTS_H = 14   # 行事メモ行高さ

    BLOCK_HDR_H = HDR_H + DOW_H + EVENTS_H  # 1ブロックあたりのヘッダー合計

    page_h    = landscape(A4)[1] - 2 * MARGIN - TITLE_H
    total_emp = len(kitchen_emps) + len(hall_emps)

    if total_emp > 0:
        # 2ブロック（キッチン・ホール）のヘッダー高さとギャップを引いた残りを従業員行に充当
        available = page_h - 2 * BLOCK_HDR_H - GAP
        emp_h = max(9, min(20, int(available / total_emp)))
    else:
        emp_h = 14

    # ── ストーリー構築 ────────────────────────────────────────────────
    title_style = ParagraphStyle(
        "Title",
        fontName=font_name, fontSize=11,
        alignment=TA_CENTER, textColor=COL_HDR_TITLE,
        spaceAfter=3,
    )
    title_text = (
        f"{start_d.month}月 シフト表　"
        f"{period.start_date} 〜 {period.end_date}"
    )

    story = [Paragraph(title_text, title_style)]

    # 上段: キッチン
    if kitchen_emps:
        tbl = _build_block_table(
            kitchen_emps, dates, assignments, req_map,
            col_widths, font_name, font_size, n,
            emp_h, HDR_H, DOW_H, EVENTS_H, period_label,
        )
        story.append(tbl)
        story.append(Spacer(1, GAP))

    # 下段: ホール
    if hall_emps:
        tbl = _build_block_table(
            hall_emps, dates, assignments, req_map,
            col_widths, font_name, font_size, n,
            emp_h, HDR_H, DOW_H, EVENTS_H, period_label,
        )
        story.append(tbl)

    doc.build(story)
