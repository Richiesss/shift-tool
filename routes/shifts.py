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
    requests_list = repo.get_shift_requests(period_id)
    req_map = {(r.employee_id, r.date): r for r in requests_list}
    dates = period.date_range()

    # 1人フォーカスモードをデフォルト（grid=1 で全員表示）
    show_grid = request.args.get("grid", type=int, default=0)
    if show_grid:
        emp_idx = None
    else:
        emp_idx = request.args.get("emp_idx", type=int, default=0)

    current_emp = None
    prev_idx = next_idx = None
    if emp_idx is not None and 0 <= emp_idx < len(employees):
        current_emp = employees[emp_idx]
        prev_idx = emp_idx - 1 if emp_idx > 0 else None
        next_idx = emp_idx + 1 if emp_idx < len(employees) - 1 else None

    # 入力済み従業員数（1日以上入力があるもの）
    filled_emp_ids = {r.employee_id for r in requests_list}
    filled_count = sum(1 for e in employees if e.id in filled_emp_ids)

    return render_template(
        "shifts/input.html",
        period=period,
        employees=employees,
        dates=dates,
        req_map=req_map,
        patterns=ALL_PATTERNS,
        emp_idx=emp_idx,
        current_emp=current_emp,
        prev_idx=prev_idx,
        next_idx=next_idx,
        filled_count=filled_count,
        filled_emp_ids=filled_emp_ids,
        DAY_JP=["月", "火", "水", "木", "金", "土", "日"],
    )


@bp.post("/<int:period_id>/save")
def save(period_id):
    period = repo.get_period(period_id)
    if not period:
        flash("期間が見つかりません", "error")
        return redirect(url_for("shifts.index"))
    employees = repo.get_all_employees(active_only=True)
    dates = period.date_range()

    # フォームに含まれる従業員のみ保存（1人モード対応）
    emp_id_filter = request.form.get("save_emp_id", type=int)
    target_emps = [e for e in employees if emp_id_filter is None or e.id == emp_id_filter]

    new_requests = []
    for emp in target_emps:
        for d in dates:
            pid = request.form.get(f"pattern_{emp.id}_{d}")
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

    # 1人モードなら次の従業員へ
    next_idx = request.form.get("next_emp_idx", type=int)
    if next_idx is not None:
        return redirect(url_for("shifts.input", period_id=period_id, emp_idx=next_idx))
    flash("希望シフトを保存しました", "success")
    return redirect(url_for("shifts.input", period_id=period_id))
