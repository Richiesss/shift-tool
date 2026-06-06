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
from utils.holidays import holiday_set

DAY_JP = ["月", "火", "水", "木", "金", "土", "日"]

# セルスタイル色
COL_HDR_TITLE = colors.HexColor("#1E3A5F")   # タイトル背景
COL_HDR_WD    = colors.HexColor("#E2E8F0")   # 平日ヘッダー（薄グレー）
COL_HDR_SAT   = colors.HexColor("#93C5FD")   # 土曜ヘッダー（青）
COL_HDR_SUN   = colors.HexColor("#FCA5A5")   # 日曜ヘッダー（赤）
COL_NAME      = colors.HexColor("#E0F2FE")   # 氏名列
COL_SAT_D     = colors.HexColor("#EFF6FF")   # 土曜データ（極薄青）
COL_SUN_D     = colors.HexColor("#FFF1F2")   # 日曜データ（極薄赤）
COL_LEAVE     = colors.HexColor("#D1FAE5")   # 有給（緑）
COL_EVEN      = colors.white                  # 偶数行も白（白地統一）
COL_WHITE     = colors.white
COL_GRID      = colors.HexColor("#CBD5E1")   # グリッド線
COL_TXT_SAT   = colors.HexColor("#1D4ED8")   # 土曜文字（青）
COL_TXT_SUN   = colors.HexColor("#DC2626")   # 日曜文字（赤）
COL_TXT_OFF   = colors.HexColor("#9CA3AF")   # 休み文字
COL_TXT_LEAVE = colors.HexColor("#166534")   # 有給文字（緑）


# ── フォント登録 ─────────────────────────────────────────────────────────

def _register_font() -> str:
    """日本語フォントを登録。利用不可の場合は Helvetica にフォールバック。"""
    import glob
    candidates = [
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

    assignments の値は (position_value, is_reinforcement, reinf_start, reinf_end) の4タプル。
    """
    from utils.shift_patterns import PATTERN_MAP

    req  = req_map.get((emp_id, date_str))
    note = (req.note or "").strip() if req else ""

    if (req and req.pattern_id == "paid_leave") or "有給" in note:
        return "有給", "leave"

    b_raw = assignments.get((emp_id, date_str, TimeSlot.BREAKFAST.value))
    d_raw = assignments.get((emp_id, date_str, TimeSlot.DINNER.value))

    if not b_raw and not d_raw:
        return "-", "off"

    # 4タプルから位置情報を展開
    b_pos,  b_is_reinf, b_rs, b_re = b_raw if b_raw else (None, False, None, None)
    d_pos,  d_is_reinf, d_rs, d_re = d_raw if d_raw else (None, False, None, None)

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
            return f"{s} {note} {e}", "assigned_note"
        return f"{s}-{e}", "assigned"

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
    """ポジション別のデフォルト勤務時間を返す（HH:MM を小数時刻に変換）"""
    from db import repositories as repo
    pos_key = b_pos or d_pos or "hall"
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


# ── メイン出力 ────────────────────────────────────────────────────────────

COL_EVENT_BG = colors.HexColor("#FEF08A")  # 行事行（備考欄）の背景（黄色）


def _build_block_table(
    block_emps, dates, assignments, req_map, col_widths,
    font_name, font_size, n, emp_h, hdr_h, dow_h, period_label,
    events_h: int = 0, notes: dict | None = None, holidays: set | None = None,
):
    """
    従業員グループ1ブロック分のテーブルを生成する。

    行構成（events_h > 0 のとき）:
      Row 0 : 日付番号
      Row 1 : 曜日
      Row 2 : 行事メモ欄（空欄）
      Row 3+: 従業員シフト行

    行構成（events_h == 0 のとき）:
      Row 0 : 日付番号
      Row 1 : 曜日
      Row 2+: 従業員シフト行
    """
    n_block    = len(block_emps)
    has_events = events_h > 0
    EMP_START  = 3 if has_events else 2

    _hols     = holidays or set()
    row_dates = [period_label] + [str(d.day) for d in dates] + [period_label]
    row_dows  = [""] + [DAY_JP[d.weekday()] + ("(祝)" if d.isoformat() in _hols else "") for d in dates] + [""]

    emp_rows = []
    for emp in block_emps:
        row = [emp.name]
        for d in dates:
            text, _ = _get_shift_text(emp.id, d.isoformat(), assignments, req_map)
            row.append(text)
        row.append(emp.name)
        emp_rows.append(row)

    if has_events:
        note_cells = [(notes or {}).get(d.isoformat(), "") for d in dates]
        row_notes  = ["備考"] + note_cells + [""]
        table_data  = [row_dates, row_dows, row_notes] + emp_rows
        row_heights = [hdr_h, dow_h, events_h] + [emp_h] * n_block
    else:
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
    ]

    if has_events:
        cmds += [
            ("BACKGROUND", (0, 2),  (-1, 2), COL_EVENT_BG),
            ("FONTSIZE",   (0, 2),  (0, 2),  max(5, font_size - 1)),
            ("TEXTCOLOR",  (0, 2),  (0, 2),  COL_TXT_OFF),
            ("ALIGN",      (0, 2),  (0, 2),  "LEFT"),
            ("ALIGN",      (1, 2),  (-1, 2), "LEFT"),
        ]

    # 土日・祝日ヘッダー着色
    for i, d in enumerate(dates):
        col  = i + 1
        dow  = d.weekday()
        is_h = d.isoformat() in _hols
        if dow == 6 or is_h:
            cmds += [("BACKGROUND", (col, 0), (col, 1), COL_HDR_SUN),
                     ("TEXTCOLOR",  (col, 0), (col, 1), COL_TXT_SUN)]
        elif dow == 5:
            cmds += [("BACKGROUND", (col, 0), (col, 1), COL_HDR_SAT),
                     ("TEXTCOLOR",  (col, 0), (col, 1), COL_TXT_SAT)]

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
            is_h = date_str in _hols
            if style == "leave":
                cmds.append(("BACKGROUND", (col, r), (col, r), COL_LEAVE))
                cmds.append(("TEXTCOLOR",  (col, r), (col, r), COL_TXT_LEAVE))
            elif style == "off":
                if dow == 6 or is_h:
                    cmds.append(("BACKGROUND", (col, r), (col, r), COL_SUN_D))
                elif dow == 5:
                    cmds.append(("BACKGROUND", (col, r), (col, r), COL_SAT_D))
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
    notes    = repo.get_schedule_notes(period.id)

    font_name = _register_font()

    MARGIN = 5 * mm
    doc = SimpleDocTemplate(
        path,
        pagesize=landscape(A4),
        leftMargin=MARGIN, rightMargin=MARGIN,
        topMargin=MARGIN,  bottomMargin=MARGIN,
    )

    dates    = list(period.date_range())
    n        = len(dates)
    holidays = holiday_set(dates)

    # ── 従業員をキッチン／ホールに分類 ───────────────────────────────
    # output_position 優先、未設定なら primary_position で判断
    def _out_pos(e):
        return e.output_position or e.primary_position

    kitchen_emps = [e for e in employees if _out_pos(e) == PrimaryPosition.KITCHEN]
    hall_emps    = [e for e in employees if _out_pos(e) != PrimaryPosition.KITCHEN]

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
    HDR_H    = 11   # 日付番号行高さ (pt)
    DOW_H    = 9    # 曜日行高さ (pt)
    EVENTS_H = 13   # 行事メモ行高さ (pt, キッチンブロックのみ)
    GAP      = 4    # グループ間スペーサー (pt)
    TITLE_H  = 18   # タイトル段落高さ余裕込み (pt)

    # フレーム高さ（reportlab が使える縦スペース）
    frame_h = landscape(A4)[1] - 2 * MARGIN

    # 固定消費高さ: タイトル + 各ブロックのヘッダー行 + スペーサー
    kit_hdr_h  = HDR_H + DOW_H + EVENTS_H   # キッチンブロックヘッダー
    hall_hdr_h = HDR_H + DOW_H              # ホールブロックヘッダー（行事なし）

    has_kitchen = bool(kitchen_emps)
    has_hall    = bool(hall_emps)
    gap_total   = GAP if (has_kitchen and has_hall) else 0
    fixed_h     = (TITLE_H
                   + (kit_hdr_h  if has_kitchen else 0)
                   + (hall_hdr_h if has_hall    else 0)
                   + gap_total)

    total_emp = len(kitchen_emps) + len(hall_emps)
    if total_emp > 0:
        emp_h = max(9, min(20, int((frame_h - fixed_h) / total_emp)))
    else:
        emp_h = 14

    # ── ストーリー構築 ────────────────────────────────────────────────
    title_style = ParagraphStyle(
        "Title",
        fontName=font_name, fontSize=11,
        alignment=TA_CENTER, textColor=COL_HDR_TITLE,
        spaceAfter=4,
    )
    title_text = (
        f"{start_d.month}月 シフト表　"
        f"{period.start_date} 〜 {period.end_date}"
    )

    story = [Paragraph(title_text, title_style)]

    # 上段: キッチン（備考行あり）
    if kitchen_emps:
        tbl = _build_block_table(
            kitchen_emps, dates, assignments, req_map,
            col_widths, font_name, font_size, n,
            emp_h, HDR_H, DOW_H, period_label,
            events_h=EVENTS_H, notes=notes, holidays=holidays,
        )
        story.append(tbl)
        if hall_emps:
            story.append(Spacer(1, GAP))

    # 下段: ホール（行事メモ行なし）
    if hall_emps:
        tbl = _build_block_table(
            hall_emps, dates, assignments, req_map,
            col_widths, font_name, font_size, n,
            emp_h, HDR_H, DOW_H, period_label,
            events_h=0, holidays=holidays,
        )
        story.append(tbl)

    doc.build(story)
