import io
from datetime import date as _date
from flask import Blueprint, send_file, flash, redirect, url_for, request
from db import repositories as repo
from export.excel_exporter import export_excel
from export.pdf_exporter import export_pdf

bp = Blueprint("export", __name__, url_prefix="/export")


def _period_filename(period, ext: str) -> str:
    """期間から「3月前半+SKY+DINING+UOMANシフト.xlsx」形式のファイル名を生成。
    未確定の期間は「(案)」を付ける。"""
    start = _date.fromisoformat(period.start_date)
    half  = "前半" if start.day <= 15 else "後半"
    draft = "" if period.status == "confirmed" else "(案)"
    return f"{start.month}月{half}+SKY+DINING+UOMANシフト{draft}.{ext}"


def _assignments_dict(period_id: int) -> dict:
    """list[ShiftAssignment] → exporterが期待するdict形式に変換"""
    return {
        (a.employee_id, a.date, a.time_slot.value):
        (a.position.value, a.is_reinforcement, a.reinf_start, a.reinf_end)
        for a in repo.get_assignments(period_id)
    }


@bp.get("/<int:period_id>/excel")
def excel(period_id):
    """Excel出力。確定済みの期間のみ出力できる"""
    period = repo.get_period(period_id)
    if not period:
        flash("期間が見つかりません", "error")
        return redirect(url_for("schedule.index", period_id=period_id))
    if period.status != "confirmed":
        flash("確定前のシフトはExcel出力できません。確定後に出力してください。", "error")
        return redirect(url_for("schedule.index", period_id=period_id))
    employees = repo.get_all_employees(active_only=True)
    assignments = _assignments_dict(period_id)
    show_reserve = repo.get_app_setting("export_show_reservation_counts", "1") == "1"
    buf = io.BytesIO()
    export_excel(buf, period, employees, assignments, show_reservation_counts=show_reserve)
    buf.seek(0)
    filename = _period_filename(period, "xlsx")
    resp = send_file(buf, as_attachment=True, download_name=filename,
                     mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
    resp.headers["Cache-Control"] = "no-store"
    return resp


@bp.get("/<int:period_id>/pdf")
def pdf(period_id):
    """PDF出力。確定済みの期間のみダウンロードできる。
    ?inline=1 の場合は未確定でも「(案)」付きでブラウザ内プレビューできる"""
    period = repo.get_period(period_id)
    if not period:
        flash("期間が見つかりません", "error")
        return redirect(url_for("schedule.index", period_id=period_id))
    inline = request.args.get("inline") == "1"
    if period.status != "confirmed" and not inline:
        flash("確定前のシフトはPDF出力できません。PDFプレビューをご利用ください。", "error")
        return redirect(url_for("schedule.index", period_id=period_id))
    employees = repo.get_all_employees(active_only=True)
    assignments = _assignments_dict(period_id)
    show_reserve = repo.get_app_setting("export_show_reservation_counts", "1") == "1"
    buf = io.BytesIO()
    export_pdf(buf, period, employees, assignments, show_reservation_counts=show_reserve)
    buf.seek(0)
    filename = _period_filename(period, "pdf")
    resp = send_file(buf, as_attachment=not inline, download_name=filename,
                     mimetype="application/pdf")
    resp.headers["Cache-Control"] = "no-store"
    return resp
