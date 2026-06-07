import io
from datetime import date as _date
from flask import Blueprint, send_file, flash, redirect, url_for
from db import repositories as repo
from export.excel_exporter import export_excel
from export.pdf_exporter import export_pdf

bp = Blueprint("export", __name__, url_prefix="/export")


def _period_filename(period, ext: str) -> str:
    """期間から「3月前半+SKY+DINING+UOMANシフト.xlsx」形式のファイル名を生成"""
    start = _date.fromisoformat(period.start_date)
    half  = "前半" if start.day <= 15 else "後半"
    return f"{start.month}月{half}+SKY+DINING+UOMANシフト.{ext}"


def _assignments_dict(period_id: int) -> dict:
    """list[ShiftAssignment] → exporterが期待するdict形式に変換"""
    return {
        (a.employee_id, a.date, a.time_slot.value):
        (a.position.value, a.is_reinforcement, a.reinf_start, a.reinf_end)
        for a in repo.get_assignments(period_id)
    }


@bp.get("/<int:period_id>/excel")
def excel(period_id):
    period = repo.get_period(period_id)
    if not period:
        flash("期間が見つかりません", "error")
        return redirect(url_for("schedule.index", period_id=period_id))
    if period.status != "confirmed":
        flash("確定済みのシフトのみエクスポートできます", "error")
        return redirect(url_for("schedule.index", period_id=period_id))
    employees = repo.get_all_employees(active_only=True)
    assignments = _assignments_dict(period_id)
    buf = io.BytesIO()
    export_excel(buf, period, employees, assignments)
    buf.seek(0)
    filename = _period_filename(period, "xlsx")
    return send_file(buf, as_attachment=True, download_name=filename,
                     mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")


@bp.get("/<int:period_id>/pdf")
def pdf(period_id):
    period = repo.get_period(period_id)
    if not period:
        flash("期間が見つかりません", "error")
        return redirect(url_for("schedule.index", period_id=period_id))
    if period.status != "confirmed":
        flash("確定済みのシフトのみエクスポートできます", "error")
        return redirect(url_for("schedule.index", period_id=period_id))
    employees = repo.get_all_employees(active_only=True)
    assignments = _assignments_dict(period_id)
    buf = io.BytesIO()
    export_pdf(buf, period, employees, assignments)
    buf.seek(0)
    filename = _period_filename(period, "pdf")
    return send_file(buf, as_attachment=True, download_name=filename,
                     mimetype="application/pdf")
