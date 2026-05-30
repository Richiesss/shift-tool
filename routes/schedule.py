from flask import Blueprint, render_template, request, redirect, url_for, flash, jsonify
from db import repositories as repo
from models.schedule import ShiftAssignment
from utils.constants import TimeSlot, Position


def _build_time_map(requests) -> dict:
    """(employee_id, date, slot_value) → 時間文字列 のマップを生成"""
    from utils.shift_patterns import PATTERN_MAP
    time_map = {}
    for r in requests:
        # パターンから時間を決定
        if r.pattern_id == "custom" and r.custom_start and r.custom_end:
            t = f"{r.custom_start}〜{r.custom_end}"
        elif r.pattern_id == "double":
            t = "6:00〜23:00"
        elif r.pattern_id:
            p = PATTERN_MAP.get(r.pattern_id)
            t = f"{p.start}〜{p.end}" if (p and p.start and p.end) else ""
        else:
            t = ""
        if r.breakfast:
            time_map[(r.employee_id, r.date, TimeSlot.BREAKFAST.value)] = t
        if r.dinner:
            time_map[(r.employee_id, r.date, TimeSlot.DINNER.value)] = t
    return time_map

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
    pos  = request.args.get("pos",  "hall")
    notes = repo.get_schedule_notes(period_id)
    shift_requests = repo.get_shift_requests(period_id)
    time_map = _build_time_map(shift_requests)

    # ポジションタブでメンバーを絞り込む
    # 兼任（primary_position=None or can_work_both_positions=True）は両タブに表示
    filtered_employees = [
        e for e in employees
        if e.primary_position is None
        or e.can_work_both_positions
        or e.primary_position.value == pos
    ]

    return render_template(
        "schedule/index.html",
        period=period,
        periods=periods,
        employees=filtered_employees,
        emp_map=emp_map,
        dates=dates,
        asgn_map=asgn_map,
        slot=slot,
        pos=pos,
        notes=notes,
        time_map=time_map,
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


@bp.post("/<int:period_id>/note")
def save_note(period_id):
    data = request.get_json()
    date_str = data.get("date", "")
    note = data.get("note", "")
    repo.save_schedule_note(period_id, date_str, note)
    return jsonify({"ok": True})


@bp.post("/<int:period_id>/confirm")
def confirm(period_id):
    period = repo.get_period(period_id)
    if period:
        period.status = "confirmed"
        repo.save_period(period)
        flash("シフトを確定しました", "success")
    return redirect(url_for("schedule.index", period_id=period_id))
