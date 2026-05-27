from flask import Blueprint, render_template, request, redirect, url_for, flash, jsonify
from db import repositories as repo
from models.schedule import ShiftAssignment
from utils.constants import TimeSlot, Position

bp = Blueprint("schedule", __name__, url_prefix="/schedule")


@bp.get("/")
def list_periods():
    periods = repo.get_all_periods()
    if periods:
        return redirect(url_for("schedule.index", period_id=periods[0].id))
    flash("期間がありません。まずシフト期間を作成してください", "info")
    return redirect(url_for("shifts.index"))


@bp.get("/<int:period_id>")
def index(period_id):
    period = repo.get_period(period_id)
    if not period:
        flash("期間が見つかりません", "error")
        return redirect(url_for("shifts.index"))

    periods = repo.get_all_periods()
    employees = repo.get_all_employees(active_only=True)
    emp_map = {e.id: e for e in employees}
    assignments = repo.get_assignments(period_id)
    dates = period.date_range()

    # {date_str: {time_slot: [ShiftAssignment, ...]}}
    asgn_map: dict = {}
    for a in assignments:
        asgn_map.setdefault(a.date, {}).setdefault(a.time_slot.value, []).append(a)

    slot = request.args.get("slot", "breakfast")

    return render_template(
        "schedule/index.html",
        period=period,
        periods=periods,
        employees=employees,
        emp_map=emp_map,
        dates=dates,
        asgn_map=asgn_map,
        slot=slot,
        TimeSlot=TimeSlot,
        Position=Position,
    )


@bp.post("/<int:period_id>/assign")
def assign(period_id):
    data = request.get_json()
    emp_id = data.get("employee_id")
    date_str = data.get("date")
    slot_val = data.get("time_slot")
    pos_val = data.get("position")
    action = data.get("action", "add")

    try:
        slot = TimeSlot(slot_val)
        if action == "remove":
            repo.remove_assignment(period_id, emp_id, date_str, slot)
        else:
            pos = Position(pos_val)
            a = ShiftAssignment(employee_id=emp_id, date=date_str, time_slot=slot, position=pos)
            repo.add_assignment(period_id, a)
        return jsonify({"ok": True})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 400


@bp.post("/<int:period_id>/confirm")
def confirm(period_id):
    period = repo.get_period(period_id)
    if period:
        period.status = "confirmed"
        repo.save_period(period)
        flash("シフトを確定しました", "success")
    return redirect(url_for("schedule.index", period_id=period_id))
