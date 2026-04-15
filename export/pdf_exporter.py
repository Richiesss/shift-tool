"""PDF出力モジュール"""
from __future__ import annotations
from datetime import date
from collections import defaultdict
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.units import mm
from reportlab.lib import colors
from reportlab.platypus import (
    SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer
)
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.lib.enums import TA_CENTER, TA_LEFT
import os
from models.employee import Employee
from models.schedule import SchedulePeriod
from utils.constants import TimeSlot, Position, DAY_OF_WEEK_LABELS, SHIFT_CONSTRAINTS, SkillLevel

SKILL_BADGE = {
    SkillLevel.LEADER: "★★", SkillLevel.VETERAN: "★",
    SkillLevel.GENERAL: "", SkillLevel.BEGINNER: "▼",
}


def _register_font():
    """日本語フォントを登録（利用可能な場合）"""
    font_paths = [
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
        "/System/Library/Fonts/ヒラギノ角ゴシック W3.ttc",
        "C:/Windows/Fonts/meiryo.ttc",
        "C:/Windows/Fonts/msgothic.ttc",
    ]
    for path in font_paths:
        if os.path.exists(path):
            try:
                pdfmetrics.registerFont(TTFont("JpFont", path))
                return "JpFont"
            except Exception:
                continue
    return "Helvetica"


def export_pdf(
    path: str,
    period: SchedulePeriod,
    employees: list[Employee],
    assignments: dict[tuple[int, str, str], str],
):
    font_name = _register_font()

    doc = SimpleDocTemplate(
        path,
        pagesize=landscape(A4),
        leftMargin=10 * mm,
        rightMargin=10 * mm,
        topMargin=10 * mm,
        bottomMargin=10 * mm,
    )

    styles = getSampleStyleSheet()
    title_style = ParagraphStyle(
        "Title", fontName=font_name, fontSize=13, alignment=TA_CENTER,
        spaceAfter=4 * mm, textColor=colors.HexColor("#1e3a5f")
    )

    story = []

    # タイトル
    story.append(Paragraph(
        f"シフト表　{period.start_date} 〜 {period.end_date}",
        title_style
    ))

    dates = period.date_range()
    slots = list(TimeSlot)
    positions = list(Position)

    # 集計
    count_map: dict[tuple[str, str, str], int] = defaultdict(int)
    skilled_map: dict[tuple[str, str, str], int] = defaultdict(int)
    for (emp_id, ds, slot_v), pos_v in assignments.items():
        count_map[(ds, slot_v, pos_v)] += 1
        emp = next((e for e in employees if e.id == emp_id), None)
        if emp and emp.is_skilled(pos_v):
            skilled_map[(ds, slot_v, pos_v)] += 1

    # テーブルデータ構築
    # ヘッダー行1: 氏名 + 日付ラベル
    header1 = ["氏名"]
    for d in dates:
        dow = DAY_OF_WEEK_LABELS[d.weekday()]
        header1.append(f"{d.month}/{d.day}\n({dow})")
    header1.append("計")

    # ヘッダー行2: 空 + スロット
    header2 = [""]
    for d in dates:
        for slot in slots:
            header2.append(slot.short_label())
    header2.append("")

    table_data = [header1, header2]

    # 従業員行
    for emp in employees:
        skill_h = SKILL_BADGE.get(emp.hall_skill, "")
        skill_k = SKILL_BADGE.get(emp.kitchen_skill, "")
        row = [f"{emp.name}\nH:{skill_h} K:{skill_k}"]
        total = 0
        for d in dates:
            for slot in slots:
                ds = d.isoformat()
                pos_v = assignments.get((emp.id, ds, slot.value))
                if pos_v:
                    pos_label = "H" if pos_v == "hall" else "K"
                    skill = emp.hall_skill if pos_v == "hall" else emp.kitchen_skill
                    badge = SKILL_BADGE.get(skill, "")
                    row.append(f"{pos_label}{badge}")
                    total += 1
                else:
                    row.append("")
        row.append(str(total))
        table_data.append(row)

    # 集計行（ポジション別）
    for pos in positions:
        row = [f"{pos.label()}\n人数"]
        for d in dates:
            for slot in slots:
                ds = d.isoformat()
                cnt = count_map[(ds, slot.value, pos.value)]
                sk = skilled_map[(ds, slot.value, pos.value)]
                row.append(f"{cnt}\n熟{sk}")
        row.append("")
        table_data.append(row)

    # 列数の整合性修正（ヘッダー1はスパンがあるので後で処理）
    # 列幅
    page_w = landscape(A4)[0] - 20 * mm
    name_w = 22 * mm
    slot_w = (page_w - name_w - 10 * mm) / (len(dates) * len(slots))
    total_w = 10 * mm
    col_widths = [name_w] + [slot_w] * (len(dates) * len(slots)) + [total_w]

    # header1の列数をheader2に合わせる（日付列はスパンで後で設定）
    # header1を実際の列数に展開
    header1_expanded = ["氏名"]
    for d in dates:
        dow = DAY_OF_WEEK_LABELS[d.weekday()]
        header1_expanded.append(f"{d.month}/{d.day}\n({dow})")
        if len(slots) > 1:
            for _ in range(len(slots) - 1):
                header1_expanded.append("")
    header1_expanded.append("計")
    table_data[0] = header1_expanded

    tbl = Table(table_data, colWidths=col_widths, repeatRows=2)

    # スタイル
    style_cmds = [
        ("FONTNAME", (0, 0), (-1, -1), font_name),
        ("FONTSIZE", (0, 0), (-1, -1), 7),
        ("ALIGN", (0, 0), (-1, -1), "CENTER"),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#CBD5E1")),
        # ヘッダー
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#1E3A5F")),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("FONTSIZE", (0, 0), (-1, 0), 7),
        ("BACKGROUND", (0, 1), (-1, 1), colors.HexColor("#3B82F6")),
        ("TEXTCOLOR", (0, 1), (-1, 1), colors.white),
        # 氏名列
        ("ALIGN", (0, 2), (0, -1), "LEFT"),
        ("FONTSIZE", (0, 2), (0, -1), 7),
        # 集計行
        ("BACKGROUND", (0, len(table_data) - 2), (-1, len(table_data) - 1), colors.HexColor("#F1F5F9")),
        ("FONTNAME", (0, len(table_data) - 2), (-1, -1), font_name),
    ]

    # 日付ヘッダーのスパン（日付ごとにスロット列をマージ）
    col_offset = 1
    for d_idx, d in enumerate(dates):
        end_col = col_offset + len(slots) - 1
        if len(slots) > 1:
            style_cmds.append(("SPAN", (col_offset, 0), (end_col, 0)))
        dow = d.weekday()
        if dow == 5:
            style_cmds.append(("BACKGROUND", (col_offset, 0), (end_col, 1), colors.HexColor("#2563EB")))
        elif dow == 6:
            style_cmds.append(("BACKGROUND", (col_offset, 0), (end_col, 1), colors.HexColor("#DC2626")))
        col_offset += len(slots)

    # アサイン済セルの色付け
    for emp_idx, emp in enumerate(employees):
        r = 2 + emp_idx
        col = 1
        for d in dates:
            for slot in slots:
                ds = d.isoformat()
                pos_v = assignments.get((emp.id, ds, slot.value))
                if pos_v:
                    style_cmds.append(("BACKGROUND", (col, r), (col, r), colors.HexColor("#BFDBFE")))
                col += 1

    # 集計行の色付け（制約チェック）
    for pos_idx, pos in enumerate(positions):
        r = 2 + len(employees) + pos_idx
        col = 1
        for d in dates:
            for slot in slots:
                ds = d.isoformat()
                cnt = count_map[(ds, slot.value, pos.value)]
                sk = skilled_map[(ds, slot.value, pos.value)]
                key = (slot, pos)
                c = SHIFT_CONSTRAINTS.get(key, {})
                if cnt < c.get("min", 0) or sk < c.get("min_skilled", 0):
                    style_cmds.append(("BACKGROUND", (col, r), (col, r), colors.HexColor("#FEE2E2")))
                elif cnt == c.get("min", 0):
                    style_cmds.append(("BACKGROUND", (col, r), (col, r), colors.HexColor("#FEF3C7")))
                else:
                    style_cmds.append(("BACKGROUND", (col, r), (col, r), colors.HexColor("#D1FAE5")))
                col += 1

    # 行の高さ
    row_heights = [14, 12] + [18] * len(employees) + [22] * len(positions)
    tbl._rowHeights = row_heights

    tbl.setStyle(TableStyle(style_cmds))
    story.append(tbl)

    doc.build(story)
