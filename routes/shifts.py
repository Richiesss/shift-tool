from datetime import date, timedelta
from flask import Blueprint, render_template, request, redirect, url_for, flash
from db import repositories as repo
from models.schedule import SchedulePeriod, ShiftRequest
from utils.shift_patterns import ALL_PATTERNS, PATTERN_MAP

bp = Blueprint("shifts", __name__, url_prefix="/shifts")


@bp.get("/")
def index():
    periods = repo.get_all_periods()
    return render_template("shifts/index.html", periods=periods)


@bp.post("/period/new")
def new_period():
    start_str = request.form.get("start_date", "")
    try:
        start = date.fromisoformat(start_str)
    except ValueError:
        flash("日付が不正です", "error")
        return redirect(url_for("shifts.index"))
    end = start + timedelta(days=13)
    period = SchedulePeriod(id=None, start_date=str(start), end_date=str(end))
    period = repo.save_period(period)
    return redirect(url_for("shifts.input", period_id=period.id))


@bp.get("/<int:period_id>")
def input(period_id):
    period = repo.get_period(period_id)
    if not period:
        flash("期間が見つかりません", "error")
        return redirect(url_for("shifts.index"))
    employees = repo.get_all_employees(active_only=True)
    requests = repo.get_shift_requests(period_id)
    req_map = {(r.employee_id, r.date): r for r in requests}

    dates = period.date_range()
    return render_template(
        "shifts/input.html",
        period=period,
        employees=employees,
        dates=dates,
        req_map=req_map,
        patterns=ALL_PATTERNS,
    )


@bp.post("/<int:period_id>/save")
def save(period_id):
    period = repo.get_period(period_id)
    if not period:
        flash("期間が見つかりません", "error")
        return redirect(url_for("shifts.index"))
    employees = repo.get_all_employees(active_only=True)
    dates = period.date_range()

    new_requests = []
    for emp in employees:
        for d in dates:
            key = f"pattern_{emp.id}_{d}"
            pid = request.form.get(key)
            if not pid:
                continue
            cs = request.form.get(f"custom_start_{emp.id}_{d}") or None
            ce = request.form.get(f"custom_end_{emp.id}_{d}") or None
            new_requests.append(ShiftRequest(
                employee_id=emp.id,
                date=str(d),
                pattern_id=pid,
                custom_start=cs,
                custom_end=ce,
            ))

    repo.save_shift_requests(period_id, new_requests)
    flash("希望シフトを保存しました", "success")
    return redirect(url_for("shifts.input", period_id=period_id))
