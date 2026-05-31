from collections import defaultdict
from flask import Blueprint, render_template, request, redirect, url_for, flash, jsonify
from db import repositories as repo
from models.schedule import ShiftAssignment
from utils.constants import TimeSlot, Position


def _compute_staffing(assignments, employees, dates, constraints) -> dict:
    """
    各日×スロット×ポジションの人員充足状況を計算して返す。

    返り値: {(date_str, slot_val, pos_val): {
        count, min, leaders, min_leader, short_staff, short_leader
    }}
    """
    emp_map = {e.id: e for e in employees}
    count_map  = defaultdict(int)
    leader_map = defaultdict(int)
    for a in assignments:
        key = (a.date, a.time_slot.value, a.position.value)
        count_map[key] += 1
        e = emp_map.get(a.employee_id)
        if e and e.is_leader(a.position.value):
            leader_map[key] += 1

    result = {}
    for d in dates:
        ds = d.isoformat()
        for slot in TimeSlot:
            for pos in Position:
                c = constraints.get((slot, pos), {})
                min_s = c.get("min", 0)
                min_l = c.get("min_leader", 0)
                cnt   = count_map[(ds, slot.value, pos.value)]
                ldrs  = leader_map[(ds, slot.value, pos.value)]
                result[(ds, slot.value, pos.value)] = {
                    "count":        cnt,
                    "min":          min_s,
                    "leaders":      ldrs,
                    "min_leader":   min_l,
                    "short_staff":  cnt < min_s,
                    "short_leader": min_l > 0 and ldrs < min_l,
                }
    return result


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

    from datetime import date as _date
    slot  = request.args.get("slot", "breakfast")
    pos   = request.args.get("pos",  "hall")
    today = _date.today().isoformat()
    notes = repo.get_schedule_notes(period_id)
    shift_requests = repo.get_shift_requests(period_id)
    time_map = _build_time_map(shift_requests)

    # 人員充足チェック
    all_employees = repo.get_all_employees(active_only=True)
    constraints   = repo.get_shift_constraints()
    staffing      = _compute_staffing(assignments, all_employees, dates, constraints)

    # 人員不足をスロット×ポジション別に集計
    _DAY_JP = ['月','火','水','木','金','土','日']
    _SLOTS  = [('breakfast','朝食'), ('dinner','ディナー')]
    _POSES  = [('hall','ホール'), ('kitchen','キッチン')]
    _bucket = {}   # (slot_lbl, pos_lbl) -> list of chip dicts
    for d in dates:
        ds = d.isoformat()
        dlabel = f"{d.month}/{d.day}({_DAY_JP[d.weekday()]})"
        for sv, sl in _SLOTS:
            for pv, pl in _POSES:
                st = staffing.get((ds, sv, pv), {})
                if st.get('short_staff') or st.get('short_leader'):
                    _bucket.setdefault((sl, pl), []).append({
                        'label':      dlabel,
                        'date':       ds,
                        'is_staff':   bool(st.get('short_staff')),
                        'count':      st.get('count', 0),
                        'min':        st.get('min', 0),
                        'leaders':    st.get('leaders', 0),
                        'min_leader': st.get('min_leader', 0),
                    })
    # Jinja2 はネストしたタプル展開不可のためリスト化して渡す
    shortage_groups = [
        {'slot': sl, 'pos': pl, 'chips': chips}
        for (sl, pl), chips in _bucket.items()
    ]
    total_shortage = sum(len(g['chips']) for g in shortage_groups)

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
        staffing=staffing,
        shortage_groups=shortage_groups,
        total_shortage=total_shortage,
        today=today,
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
            is_reinf   = bool(data.get("is_reinforcement", False))
            reinf_start = data.get("reinf_start") or None
            reinf_end   = data.get("reinf_end")   or None
            a = ShiftAssignment(
                employee_id=emp_id, date=date_str, time_slot=slot, position=pos,
                is_reinforcement=is_reinf, reinf_start=reinf_start, reinf_end=reinf_end,
            )
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


@bp.get("/<int:period_id>/stats")
def stats(period_id):
    period = repo.get_period(period_id)
    if not period:
        return redirect(url_for("schedule.list_periods"))
    employees  = repo.get_all_employees(active_only=True)
    assignments = repo.get_assignments(period_id)
    from utils.constants import TimeSlot, Position
    from collections import defaultdict
    counts = defaultdict(lambda: defaultdict(int))
    for a in assignments:
        key = f"{a.time_slot.value[0].upper()}/{a.position.value[0].upper()}"
        counts[a.employee_id][key] += 1
        counts[a.employee_id]["total"] += 1
    cols = ["B/H","B/K","D/H","D/K"]
    col_labels = {"B/H":"朝食ホール","B/K":"朝食キッチン","D/H":"ディナーホール","D/K":"ディナーキッチン"}
    col_totals = {c: sum(counts[e.id][c] for e in employees) for c in cols}
    return render_template("schedule/stats.html",
        period=period, employees=employees, counts=counts,
        cols=cols, col_labels=col_labels, col_totals=col_totals,
        total_assignments=len(assignments),
        periods=repo.get_all_periods())


@bp.post("/<int:period_id>/confirm")
def confirm(period_id):
    period = repo.get_period(period_id)
    if period:
        period.status = "confirmed"
        repo.save_period(period)
        flash("シフトを確定しました", "success")
    return redirect(url_for("schedule.index", period_id=period_id))
