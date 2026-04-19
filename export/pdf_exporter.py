"""PDF出力モジュール（従業員×日付 マトリクス形式）

セルフォーマット: "13 - 22.5" / "6.5 オムレツ 15" / "有給" / "-"
"""
from __future__ import annotations
from datetime import date
from collections import defaultdict
import os

from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.units import mm
from reportlab.lib import colors
from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.lib.enums import TA_CENTER, TA_LEFT

from models.employee import Employee
from models.schedule import SchedulePeriod
from utils.constants import TimeSlot, Position, SHIFT_CONSTRAINTS

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
COL_SUM_OK    = colors.HexColor("#D1FAE5")   # 集計 OK
COL_SUM_WARN  = colors.HexColor("#FEF3C7")   # 集計 警告
COL_SUM_ERR   = colors.HexColor("#FEE2E2")   # 集計 エラー
COL_SUM_BG    = colors.HexColor("#F1F5F9")   # 集計ラベル
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
    return f"{s} - {e}", "assigned"


def _slot_default(b_pos, d_pos) -> tuple[str, str]:
    if b_pos and d_pos:
        return "6", "23"
    elif b_pos:
        return "6", "11"
    else:
        return "17", "23"


# ── メイン出力 ────────────────────────────────────────────────────────────

def _build_block_table(
    block_emps, dates, assignments, req_map, col_widths,
    font_name, font_size, n, emp_h, hdr_h, dow_h, period_label,
):
    """従業員グループ1ブロック分のテーブルを生成する。"""
    n_block = len(block_emps)

    row_dates = [period_label] + [str(d.day) for d in dates] + [period_label]
    row_dows  = [""] + [DAY_JP[d.weekday()] for d in dates] + [""]

    emp_rows = []
    for emp in block_emps:
        row = [emp.name]
        for d in dates:
            text, _ = _get_shift_text(emp.id, d.isoformat(), assignments, req_map)
            row.append(text)
        row.append(emp.name)
        emp_rows.append(row)

    table_data  = [row_dates, row_dows] + emp_rows
    row_heights = [hdr_h, dow_h] + [emp_h] * n_block

    tbl = Table(table_data, colWidths=col_widths,
                rowHeights=row_heights, repeatRows=0)

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
        ("BACKGROUND", (0, 0),  (0, -1),  COL_NAME),
        ("BACKGROUND", (-1, 0), (-1, -1), COL_NAME),
        ("FONTNAME",   (0, 2),  (0, -1),  font_name),
        ("FONTNAME",   (-1, 2), (-1, -1), font_name),
        # ヘッダー行（日付・曜日）
        ("BACKGROUND", (1, 0), (n, 1), COL_HDR_WD),
        ("FONTSIZE",   (0, 0), (-1, 1), font_size + 1),
        ("FONTNAME",   (0, 0), (-1, 1), font_name),
        # 期間ラベルセルは濃紺
        ("BACKGROUND", (0, 0),  (0, 0),  COL_HDR_TITLE),
        ("BACKGROUND", (-1, 0), (-1, 0), COL_HDR_TITLE),
        ("TEXTCOLOR",  (0, 0),  (0, 0),  colors.white),
        ("TEXTCOLOR",  (-1, 0), (-1, 0), colors.white),
        ("FONTSIZE",   (0, 0),  (0, 0),  font_size),
        ("FONTSIZE",   (-1, 0), (-1, 0), font_size),
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
        r      = 2 + row_idx
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
      従業員を複数グループに分けて縦に積み重ねて表示し、
      すべてが A4 横向き 1 枚に収まるよう行高さを自動調整する。
      末尾に集計行（朝/夜 × ホール/厨房）を追加。
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

    dates  = list(period.date_range())
    n      = len(dates)
    n_emp  = len(employees)
    N_SUM  = 4   # 集計行数（固定）

    # ── 列幅 ─────────────────────────────────────────────────────────
    page_w = landscape(A4)[0] - 2 * MARGIN
    name_w = 16 * mm
    data_w = (page_w - 2 * name_w) / n
    col_widths = [name_w] + [data_w] * n + [name_w]

    font_size = max(6, min(9, int(data_w / mm * 0.6)))

    start_d = date.fromisoformat(period.start_date)
    period_label = f"{start_d.month}月"  # 左右ヘッダーセルのラベル

    # ── 集計データ ────────────────────────────────────────────────────
    count_map   = defaultdict(int)
    skilled_map = defaultdict(int)
    for (emp_id, ds, slot_v), pos_v in assignments.items():
        count_map[(ds, slot_v, pos_v)] += 1
        emp = next((e for e in employees if e.id == emp_id), None)
        if emp and emp.is_skilled(pos_v):
            skilled_map[(ds, slot_v, pos_v)] += 1

    SLOT_ROWS = [
        ("朝 ホール", TimeSlot.BREAKFAST, Position.HALL),
        ("朝 厨房",   TimeSlot.BREAKFAST, Position.KITCHEN),
        ("夜 ホール", TimeSlot.DINNER,    Position.HALL),
        ("夜 厨房",   TimeSlot.DINNER,    Position.KITCHEN),
    ]

    # ── レイアウト計算 ────────────────────────────────────────────────
    # タイトル段落の実高さ: fontSize=11, leading≈13pt, spaceAfter=3pt
    TITLE_H  = 16
    GAP      = 3    # ブロック間・ブロックと集計の隙間 (pt)
    HDR_H    = 11   # 日付番号行高さ
    DOW_H    = 10   # 曜日行高さ
    SUM_H    = 13   # 集計行高さ

    page_h = landscape(A4)[1] - 2 * MARGIN - TITLE_H

    # ブロック数を 2〜5 で試して emp_h ≥ 9pt になる最小値を採用
    best_nb, best_per, best_emp_h = 2, n_emp, 9
    for nb in range(2, 6):
        emps_per   = (n_emp + nb - 1) // nb
        # ブロック間の gap + 集計前の gap
        gap_total  = GAP * (nb + 1)
        sum_total  = N_SUM * SUM_H
        block_h    = (page_h - sum_total - gap_total) / nb
        emp_h_calc = (block_h - HDR_H - DOW_H) / max(emps_per, 1)
        if emp_h_calc >= 9:
            best_nb, best_per, best_emp_h = nb, emps_per, emp_h_calc
            break

    n_blocks = best_nb
    emps_per = best_per
    emp_h    = max(9, min(20, int(best_emp_h)))

    # 従業員をブロックに分割
    blocks = [employees[i:i + emps_per] for i in range(0, n_emp, emps_per)]

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

    for block_emps in blocks:
        tbl = _build_block_table(
            block_emps, dates, assignments, req_map,
            col_widths, font_name, font_size, n,
            emp_h, HDR_H, DOW_H, period_label,
        )
        story.append(tbl)
        story.append(Spacer(1, GAP))

    # ── 集計テーブル ──────────────────────────────────────────────────
    sum_rows = []
    for lbl, slot, pos in SLOT_ROWS:
        row = [lbl]
        for d in dates:
            ds  = d.isoformat()
            cnt = count_map[(ds, slot.value, pos.value)]
            sk  = skilled_map[(ds, slot.value, pos.value)]
            row.append(f"{cnt} 熟{sk}")
        row.append("")
        sum_rows.append(row)

    sum_tbl = Table(sum_rows, colWidths=col_widths,
                    rowHeights=[SUM_H] * N_SUM, repeatRows=0)

    sum_cmds = [
        ("FONTNAME",      (0, 0), (-1, -1), font_name),
        ("FONTSIZE",      (0, 0), (-1, -1), max(5, font_size - 1)),
        ("ALIGN",         (0, 0), (-1, -1), "CENTER"),
        ("VALIGN",        (0, 0), (-1, -1), "MIDDLE"),
        ("GRID",          (0, 0), (-1, -1), 0.4, COL_GRID),
        ("TOPPADDING",    (0, 0), (-1, -1), 1),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 1),
        ("LEFTPADDING",   (0, 0), (-1, -1), 2),
        ("RIGHTPADDING",  (0, 0), (-1, -1), 2),
        ("BACKGROUND",    (0, 0), (0, -1),  COL_SUM_BG),
        ("BACKGROUND",    (-1, 0), (-1, -1), COL_SUM_BG),
        ("ALIGN",         (0, 0), (0, -1),  "LEFT"),
        ("FONTSIZE",      (0, 0), (0, -1),  max(5, font_size - 1)),
    ]
    for rel, (lbl, slot, pos) in enumerate(SLOT_ROWS):
        key     = (slot, pos)
        c_      = SHIFT_CONSTRAINTS.get(key, {})
        min_req = c_.get("min", 0)
        min_sk  = c_.get("min_skilled", 0)
        for i, d in enumerate(dates):
            col = i + 1
            ds  = d.isoformat()
            cnt = count_map[(ds, slot.value, pos.value)]
            sk  = skilled_map[(ds, slot.value, pos.value)]
            if cnt < min_req or sk < min_sk:
                bg = COL_SUM_ERR
            elif cnt == min_req:
                bg = COL_SUM_WARN
            else:
                bg = COL_SUM_OK
            sum_cmds.append(("BACKGROUND", (col, rel), (col, rel), bg))

    sum_tbl.setStyle(TableStyle(sum_cmds))
    story.append(sum_tbl)

    doc.build(story)
