"""Excel出力モジュール"""
from __future__ import annotations
from datetime import date
from collections import defaultdict
from openpyxl import Workbook
from openpyxl.styles import (
    PatternFill, Font, Alignment, Border, Side, GradientFill
)
from openpyxl.utils import get_column_letter
from models.employee import Employee
from models.schedule import SchedulePeriod
from utils.constants import TimeSlot, Position, DAY_OF_WEEK_LABELS, SHIFT_CONSTRAINTS, SkillLevel

SKILL_BADGE = {
    SkillLevel.LEADER: "★★", SkillLevel.VETERAN: "★",
    SkillLevel.GENERAL: "", SkillLevel.BEGINNER: "▼",
}

FILL_ASSIGNED = PatternFill("solid", fgColor="BFDBFE")
FILL_HEADER   = PatternFill("solid", fgColor="1E3A5F")
FILL_SUB_HDR  = PatternFill("solid", fgColor="3B82F6")
FILL_OK       = PatternFill("solid", fgColor="D1FAE5")
FILL_WARN     = PatternFill("solid", fgColor="FEF3C7")
FILL_ERR      = PatternFill("solid", fgColor="FEE2E2")
FILL_SAT      = PatternFill("solid", fgColor="DBEAFE")
FILL_SUN      = PatternFill("solid", fgColor="FCE7F3")
FILL_EVEN     = PatternFill("solid", fgColor="F8FAFC")

FONT_WHITE  = Font(color="FFFFFF", bold=True, size=10)
FONT_HEADER = Font(color="FFFFFF", bold=True, size=9)
FONT_BOLD   = Font(bold=True, size=9)
FONT_NORMAL = Font(size=9)

ALIGN_CENTER = Alignment(horizontal="center", vertical="center", wrap_text=True)
ALIGN_LEFT   = Alignment(horizontal="left", vertical="center")

THIN = Side(style="thin", color="CBD5E1")
BORDER = Border(left=THIN, right=THIN, top=THIN, bottom=THIN)


def export_excel(
    path: str,
    period: SchedulePeriod,
    employees: list[Employee],
    assignments: dict[tuple[int, str, str], str],
):
    wb = Workbook()
    ws = wb.active
    ws.title = f"シフト表_{period.start_date}"

    dates = period.date_range()
    slots = list(TimeSlot)
    positions = list(Position)

    # ── ヘッダー行1: タイトル ─────────────────────────────────────────
    total_cols = 1 + len(dates) * len(slots) + 1
    ws.merge_cells(start_row=1, start_column=1, end_row=1, end_column=total_cols)
    title_cell = ws.cell(1, 1, f"シフト表  {period.start_date} 〜 {period.end_date}")
    title_cell.fill = FILL_HEADER
    title_cell.font = Font(color="FFFFFF", bold=True, size=13)
    title_cell.alignment = ALIGN_CENTER
    ws.row_dimensions[1].height = 28

    # ── ヘッダー行2: 氏名 + 日付（マージ） ─────────────────────────────
    ws.cell(2, 1, "氏名").fill = FILL_SUB_HDR
    ws.cell(2, 1).font = FONT_HEADER
    ws.cell(2, 1).alignment = ALIGN_CENTER

    col = 2
    for d in dates:
        ws.merge_cells(start_row=2, start_column=col, end_row=2, end_column=col + len(slots) - 1)
        dow = DAY_OF_WEEK_LABELS[d.weekday()]
        date_label = f"{d.month}/{d.day}({dow})"
        cell = ws.cell(2, col, date_label)
        cell.alignment = ALIGN_CENTER
        cell.font = FONT_HEADER
        if d.weekday() == 5:
            cell.fill = PatternFill("solid", fgColor="2563EB")
        elif d.weekday() == 6:
            cell.fill = PatternFill("solid", fgColor="DC2626")
        else:
            cell.fill = FILL_SUB_HDR
        col += len(slots)

    ws.cell(2, col, "合計").fill = FILL_SUB_HDR
    ws.cell(2, col).font = FONT_HEADER
    ws.cell(2, col).alignment = ALIGN_CENTER
    ws.row_dimensions[2].height = 20

    # ── ヘッダー行3: スロット名 ──────────────────────────────────────
    ws.cell(3, 1, "").fill = FILL_SUB_HDR
    col = 2
    for d in dates:
        for slot in slots:
            cell = ws.cell(3, col, slot.short_label())
            cell.font = FONT_HEADER
            cell.alignment = ALIGN_CENTER
            if d.weekday() == 5:
                cell.fill = PatternFill("solid", fgColor="3B82F6")
            elif d.weekday() == 6:
                cell.fill = PatternFill("solid", fgColor="EC4899")
            else:
                cell.fill = FILL_SUB_HDR
            col += 1
    ws.cell(3, col, "").fill = FILL_SUB_HDR
    ws.row_dimensions[3].height = 16

    # ── 従業員行 ────────────────────────────────────────────────────
    data_start_row = 4
    for emp_idx, emp in enumerate(employees):
        r = data_start_row + emp_idx
        skill_h = SKILL_BADGE.get(emp.hall_skill, "")
        skill_k = SKILL_BADGE.get(emp.kitchen_skill, "")
        name_cell = ws.cell(r, 1, f"{emp.name}\nH:{skill_h} K:{skill_k}")
        name_cell.font = FONT_NORMAL
        name_cell.alignment = ALIGN_LEFT
        name_cell.border = BORDER
        if emp_idx % 2 == 1:
            name_cell.fill = FILL_EVEN

        total = 0
        col = 2
        for d in dates:
            for slot in slots:
                ds = d.isoformat()
                pos_v = assignments.get((emp.id, ds, slot.value))
                cell = ws.cell(r, col)
                cell.alignment = ALIGN_CENTER
                cell.border = BORDER
                cell.font = FONT_NORMAL

                if pos_v:
                    pos_label = "H" if pos_v == "hall" else "K"
                    skill = emp.hall_skill if pos_v == "hall" else emp.kitchen_skill
                    badge = SKILL_BADGE.get(skill, "")
                    cell.value = f"{pos_label}{badge}"
                    cell.fill = FILL_ASSIGNED
                    cell.font = Font(bold=True, size=9, color="1E40AF")
                    total += 1
                else:
                    cell.value = ""
                    if emp_idx % 2 == 1:
                        cell.fill = FILL_EVEN
                col += 1

        # 合計
        total_cell = ws.cell(r, col, total)
        total_cell.alignment = ALIGN_CENTER
        total_cell.font = FONT_BOLD
        total_cell.border = BORDER
        ws.row_dimensions[r].height = 24

    # ── 集計行 ──────────────────────────────────────────────────────
    count_map: dict[tuple[str, str, str], int] = defaultdict(int)
    skilled_map: dict[tuple[str, str, str], int] = defaultdict(int)
    for (emp_id, ds, slot_v), pos_v in assignments.items():
        count_map[(ds, slot_v, pos_v)] += 1
        emp = next((e for e in employees if e.id == emp_id), None)
        if emp and emp.is_skilled(pos_v):
            skilled_map[(ds, slot_v, pos_v)] += 1

    summary_start = data_start_row + len(employees)
    for pos_idx, pos in enumerate(positions):
        r = summary_start + pos_idx
        label_cell = ws.cell(r, 1, f"{pos.label()} 人数")
        label_cell.font = FONT_BOLD
        label_cell.fill = PatternFill("solid", fgColor="F1F5F9")
        label_cell.alignment = ALIGN_CENTER
        label_cell.border = BORDER

        col = 2
        for d in dates:
            for slot in slots:
                ds = d.isoformat()
                cnt = count_map[(ds, slot.value, pos.value)]
                sk = skilled_map[(ds, slot.value, pos.value)]
                key = (slot, pos)
                c = SHIFT_CONSTRAINTS.get(key, {})
                min_req = c.get("min", 0)
                min_sk = c.get("min_skilled", 0)

                cell = ws.cell(r, col, f"{cnt}名\n熟{sk}")
                cell.alignment = ALIGN_CENTER
                cell.font = FONT_NORMAL
                cell.border = BORDER
                if cnt < min_req or sk < min_sk:
                    cell.fill = FILL_ERR
                elif cnt == min_req:
                    cell.fill = FILL_WARN
                else:
                    cell.fill = FILL_OK
                col += 1

        ws.cell(r, col, "").border = BORDER
        ws.row_dimensions[r].height = 30

    # ── 列幅調整 ────────────────────────────────────────────────────
    ws.column_dimensions["A"].width = 14
    col = 2
    for d in dates:
        for slot in slots:
            ws.column_dimensions[get_column_letter(col)].width = 7
            col += 1
    ws.column_dimensions[get_column_letter(col)].width = 6

    # ウィンドウ固定（氏名列）
    ws.freeze_panes = "B4"

    wb.save(path)
