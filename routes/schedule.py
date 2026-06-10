from collections import defaultdict
from flask import Blueprint, render_template, request, redirect, url_for, flash, jsonify
from db import repositories as repo
from models.schedule import ShiftAssignment
from utils.constants import TimeSlot, Position

from utils.holidays import holiday_set
from utils.reservation import tiered_extra


def _compute_staffing(assignments, employees, dates, constraints,
                      reservation_counts=None,
                      reserv_tiers_b=None, reserv_tiers_d=None) -> dict:
    """
    各日×スロット×ポジションの人員充足状況を計算して返す。
    予約客数が閾値を超える日は最小人員を動的に加算する（ソルバーと同じロジック）。

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
        if e and e.is_leader(a.position.value, a.time_slot):
            leader_map[key] += 1

    rc_map = reservation_counts or {}
    tiers_b = reserv_tiers_b or []
    tiers_d = reserv_tiers_d or []
    result = {}
    for d in dates:
        ds  = d.isoformat()
        rc  = rc_map.get(ds, {})
        b_count = rc.get("breakfast", 0)
        d_count = rc.get("dinner", 0)
        for slot in TimeSlot:
            for pos in Position:
                c     = constraints.get((slot, pos), {})
                min_s = c.get("min", 0)
                min_l = c.get("min_leader", 0)
                # 予約客数による動的増員（ソルバーと同じ判定）
                if slot == TimeSlot.BREAKFAST:
                    min_s += tiered_extra(b_count, tiers_b)
                elif slot == TimeSlot.DINNER:
                    min_s += tiered_extra(d_count, tiers_d)
                cnt  = count_map[(ds, slot.value, pos.value)]
                ldrs = leader_map[(ds, slot.value, pos.value)]
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
        elif r.pattern_id == "am_only":
            t = "朝のみ可"
        elif r.pattern_id == "pm_only":
            t = "晩のみ可"
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
    # 表示順: スタッフ管理画面の display_order を維持しつつ、
    # 社員 → 専任スタッフ → ホール・キッチン兼任スタッフ の順にグループ化する
    employees = sorted(employees, key=lambda e: e.staff_category_rank())
    # emp_map はアーカイブ済みスタッフも含める
    # （アーカイブ前に生成されたシフトで参照される可能性があるため）
    emp_map = {e.id: e for e in repo.get_all_employees(active_only=False)}
    assignments = repo.get_assignments(period_id)
    dates = period.date_range()

    # {date_str: {time_slot: [ShiftAssignment, ...]}}
    asgn_map: dict = {}
    for a in assignments:
        asgn_map.setdefault(a.date, {}).setdefault(a.time_slot.value, []).append(a)

    from datetime import date as _date
    pos   = request.args.get("pos",  "hall")
    today = _date.today().isoformat()
    notes = repo.get_schedule_notes(period_id)
    shift_requests = repo.get_shift_requests(period_id)
    time_map = _build_time_map(shift_requests)

    # 人員充足チェック
    all_employees    = repo.get_all_employees(active_only=True)
    constraints      = repo.get_shift_constraints()
    reservation_counts = repo.get_reservation_counts(period_id)
    try:
        reserv_thresh_b  = int(repo.get_app_setting("reserv_threshold_breakfast",  "100"))
        reserv_extra_b   = int(repo.get_app_setting("reserv_extra_breakfast",      "1"))
        reserv_thresh_b2 = int(repo.get_app_setting("reserv_threshold_breakfast2", "0"))
        reserv_extra_b2  = int(repo.get_app_setting("reserv_extra_breakfast2",     "0"))
        reserv_thresh_d  = int(repo.get_app_setting("reserv_threshold_dinner",     "25"))
        reserv_extra_d   = int(repo.get_app_setting("reserv_extra_dinner",         "1"))
        reserv_thresh_d2 = int(repo.get_app_setting("reserv_threshold_dinner2",    "0"))
        reserv_extra_d2  = int(repo.get_app_setting("reserv_extra_dinner2",        "0"))
    except Exception:
        reserv_thresh_b = 100; reserv_extra_b = 1; reserv_thresh_b2 = 0; reserv_extra_b2 = 0
        reserv_thresh_d = 25;  reserv_extra_d = 1; reserv_thresh_d2 = 0; reserv_extra_d2 = 0
    reserv_tiers_b = [(reserv_thresh_b, reserv_extra_b), (reserv_thresh_b2, reserv_extra_b2)]
    reserv_tiers_d = [(reserv_thresh_d, reserv_extra_d), (reserv_thresh_d2, reserv_extra_d2)]
    staffing = _compute_staffing(
        assignments, all_employees, dates, constraints,
        reservation_counts=reservation_counts,
        reserv_tiers_b=reserv_tiers_b, reserv_tiers_d=reserv_tiers_d,
    )

    # 人員不足を 4区分（朝食/ディナー × ホール/キッチン）ごとに集計
    # 不足がない区分も含めて常時表示するため全組み合わせを構築する
    _DAY_JP = ['月','火','水','木','金','土','日']
    _COMBOS = [
        ('breakfast', '朝食',    'hall',    'ホール'),
        ('breakfast', '朝食',    'kitchen', 'キッチン'),
        ('dinner',    'ディナー', 'hall',    'ホール'),
        ('dinner',    'ディナー', 'kitchen', 'キッチン'),
    ]
    _bucket = {(sl, pl): [] for _, sl, _, pl in _COMBOS}
    for d in dates:
        ds     = d.isoformat()
        dlabel = f"{d.month}/{d.day}({_DAY_JP[d.weekday()]})"
        for sv, sl, pv, pl in _COMBOS:
            st = staffing.get((ds, sv, pv), {})
            if st.get('short_staff') or st.get('short_leader'):
                _bucket[(sl, pl)].append({
                    'label':      dlabel,
                    'date':       ds,
                    'is_staff':   bool(st.get('short_staff')),
                    'count':      st.get('count', 0),
                    'min':        st.get('min', 0),
                    'leaders':    st.get('leaders', 0),
                    'min_leader': st.get('min_leader', 0),
                })
    # 4区分すべてを渡す（不足なし区分も含む）
    shortage_groups = [
        {
            'slot':             sl,
            'pos':              pl,
            'chips':            _bucket[(sl, pl)],
            'short_count':      len(_bucket[(sl, pl)]),
            'has_short_staff':  any(c['is_staff']     for c in _bucket[(sl, pl)]),
            'has_short_leader': any(not c['is_staff'] for c in _bucket[(sl, pl)]),
        }
        for _, sl, _, pl in _COMBOS
    ]
    total_shortage = sum(g['short_count'] for g in shortage_groups)

    # ポジションでメンバーを絞り込み（朝食/ディナーは1画面に両方表示するため、スロットごとのリストを用意する）
    # 並び順は employees の取得順（社員 → 専任 → 兼任、各カテゴリ内は display_order）をそのまま維持
    def _filter_employees_for_slot(slot_val):
        # このスロットで実際に担当が入っている従業員ID（手動割当済みは専任外でも表示）
        assigned_in_slot = {a.employee_id for a in assignments if a.time_slot.value == slot_val}
        # このスロットで希望シフトを提出している従業員ID（専任外でも「希望はあるのに未アサイン」が見えるよう表示対象に含める）
        submitted_in_slot = {eid for (eid, _ds, sv) in time_map.keys() if sv == slot_val}
        return [
            e for e in employees
            if (e.primary_position is None or e.can_work_both_positions or e.primary_position.value == pos)
            and (e.primary_timeslot is None or e.primary_timeslot.value == slot_val
                 or e.id in assigned_in_slot or e.id in submitted_in_slot)
        ]

    employees_by_slot = {
        "breakfast": _filter_employees_for_slot("breakfast"),
        "dinner":    _filter_employees_for_slot("dinner"),
    }
    # 朝食/ディナーを1つの表にまとめるため、どちらかのスロットに該当する従業員を統合した一覧と、
    # 各従業員がどのスロットに該当するか（セルを実セルとして描画するか「該当なし」にするか）を渡す
    slot_ids = {sk: {e.id for e in lst} for sk, lst in employees_by_slot.items()}
    _combined_ids = slot_ids["breakfast"] | slot_ids["dinner"]
    employees_combined = [e for e in employees if e.id in _combined_ids]

    needs_regen = repo.get_period_gen_status(period_id).get("needs_regen", False)

    cell_notes    = repo.get_cell_notes(period_id)
    submitted_ids = {r.employee_id for r in shift_requests}
    unsubmitted_count = sum(1 for e in all_employees if e.id not in submitted_ids)

    # 正社員の希望休日（off_request）セット
    ft_off_dates = {
        (r.employee_id, r.date)
        for r in shift_requests
        if r.pattern_id == 'off_request'
        and emp_map.get(r.employee_id) is not None
        and emp_map[r.employee_id].employment_type.value == 'full_time'
    }

    holidays = holiday_set(dates)

    # 社員デフォルト勤務時間（ポジション別・設定画面で変更可能）
    def _ft_time(pos_key, slot_key):
        s = repo.get_app_setting(f"ft_{pos_key}_{slot_key}_start",
                                 "06:00" if slot_key == "breakfast" else "17:00")
        e = repo.get_app_setting(f"ft_{pos_key}_{slot_key}_end",
                                 "11:00" if slot_key == "breakfast" else "22:00")
        return f"{s}〜{e}"

    ft_times = {
        "hall":    {"breakfast": _ft_time("hall",    "breakfast"),
                    "dinner":    _ft_time("hall",    "dinner")},
        "kitchen": {"breakfast": _ft_time("kitchen", "breakfast"),
                    "dinner":    _ft_time("kitchen", "dinner")},
    }

    return render_template(
        "schedule/index.html",
        period=period,
        periods=periods,
        employees_combined=employees_combined,
        slot_ids=slot_ids,
        emp_map=emp_map,
        dates=dates,
        asgn_map=asgn_map,
        pos=pos,
        notes=notes,
        time_map=time_map,
        staffing=staffing,
        shortage_groups=shortage_groups,
        total_shortage=total_shortage,
        needs_regen=needs_regen,
        unsubmitted_count=unsubmitted_count,
        reservation_counts=reservation_counts,
        ft_off_dates=ft_off_dates,
        cell_notes=cell_notes,
        today=today,
        ft_times=ft_times,
        holidays=holidays,
        TimeSlot=TimeSlot,
        Position=Position,
    )


@bp.post("/<int:period_id>/assign")
def assign(period_id):
    import re as _re
    data = request.get_json(silent=True)
    if not data:
        return jsonify({"ok": False, "error": "Invalid JSON body"}), 400

    emp_id   = data.get("employee_id")
    date_str = data.get("date")
    slot_val = data.get("time_slot")
    pos_val  = data.get("position")
    action   = data.get("action", "add")

    try:
        # 期間の存在確認
        period = repo.get_period(period_id)
        if not period:
            return jsonify({"ok": False, "error": "期間が見つかりません"}), 400

        if period.status == "confirmed":
            return jsonify({"ok": False, "error": "確定済みのシフトは変更できません。変更するには確定を解除してください。"}), 403

        # 日付が期間内かチェック
        if date_str and not (period.start_date <= date_str <= period.end_date):
            return jsonify({"ok": False, "error": "指定日付が期間外です"}), 400

        slot = TimeSlot(slot_val)
        if action == "remove":
            repo.remove_assignment(period_id, emp_id, date_str, slot)
        else:
            pos = Position(pos_val)
            is_reinf    = bool(data.get("is_reinforcement", False))
            reinf_start = data.get("reinf_start") or None
            reinf_end   = data.get("reinf_end")   or None
            # 応援時刻の形式チェック
            _time_re = _re.compile(r'^\d{1,2}:\d{2}$')
            if reinf_start and not _time_re.match(reinf_start):
                return jsonify({"ok": False, "error": "開始時刻の形式が不正です"}), 400
            if reinf_end and not _time_re.match(reinf_end):
                return jsonify({"ok": False, "error": "終了時刻の形式が不正です"}), 400
            a = ShiftAssignment(
                employee_id=emp_id, date=date_str, time_slot=slot, position=pos,
                is_reinforcement=is_reinf, reinf_start=reinf_start, reinf_end=reinf_end,
            )
            repo.add_assignment(period_id, a)
        return jsonify({"ok": True})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 400


@bp.post("/<int:period_id>/cell_note")
def save_cell_note(period_id):
    data = request.get_json()
    emp_id   = data.get("employee_id")
    date_str = data.get("date", "")
    note     = data.get("note", "")
    if not emp_id or not date_str:
        return jsonify({"ok": False, "error": "パラメータが不足しています"}), 400
    repo.save_cell_note(period_id, emp_id, date_str, note)
    return jsonify({"ok": True})


@bp.post("/<int:period_id>/note")
def save_note(period_id):
    data = request.get_json()
    date_str = data.get("date", "")
    note = data.get("note", "")
    repo.save_schedule_note(period_id, date_str, note)
    return jsonify({"ok": True})


@bp.get("/<int:period_id>/stats")
def stats(period_id):
    return redirect(url_for("schedule.stats_multi", p=period_id))


@bp.get("/<int:period_id>/history")
def history(period_id):
    """手動割当の変更履歴をJSONで返す"""
    logs = repo.get_assignment_log(period_id, limit=60)
    _SLOT_JP = {"breakfast": "朝食", "dinner": "ディナー"}
    _POS_JP  = {"hall": "ホール", "kitchen": "キッチン"}
    for entry in logs:
        entry["slot_label"] = _SLOT_JP.get(entry.get("time_slot", ""), entry.get("time_slot", ""))
        entry["pos_label"]  = _POS_JP.get(entry.get("position", ""), entry.get("position", "") or "")
    return jsonify(logs)


@bp.get("/stats")
def stats_multi():
    """複数期間をまたいだ統計ページ"""
    all_periods = repo.get_all_periods()
    # 選択された期間ID（未選択なら最新4件）
    sel_ids = request.args.getlist("p", type=int)
    if not sel_ids and all_periods:
        sel_ids = [p.id for p in all_periods[:4]]

    employees = repo.get_all_employees(active_only=True)
    stats_data = repo.get_multi_period_stats(sel_ids)
    sel_periods = [p for p in all_periods if p.id in sel_ids]

    any_wage = any(e.hourly_wage > 0 for e in employees)

    # 36協定チェック（週40時間超え）
    WEEKLY_LIMIT = 40.0
    emp_map_local = {e.id: e for e in employees}
    overtime_alerts = []
    for eid, d in stats_data.items():
        emp = emp_map_local.get(eid)
        if not emp:
            continue
        for wk, hrs in d.get("weekly_hours", {}).items():
            if hrs > WEEKLY_LIMIT:
                year_s, w_s = wk.split("W")
                overtime_alerts.append({
                    "name":       emp.name,
                    "week_label": f"{year_s}年第{int(w_s)}週",
                    "hours":      hrs,
                })
    overtime_alerts.sort(key=lambda x: -x["hours"])

    # 客数あたり人件費（人員配置の妥当性を判断する指標）
    customer_counts = repo.get_total_customer_counts(sel_ids)
    total_labor_cost = sum(d.get("cost", 0.0) for d in stats_data.values())
    cost_per_customer = (
        total_labor_cost / customer_counts["total"] if customer_counts["total"] > 0 else None
    )

    return render_template(
        "schedule/stats_multi.html",
        all_periods=all_periods,
        sel_periods=sel_periods,
        sel_ids=sel_ids,
        employees=employees,
        stats_data=stats_data,
        any_wage=any_wage,
        overtime_alerts=overtime_alerts,
        weekly_limit=WEEKLY_LIMIT,
        customer_counts=customer_counts,
        total_labor_cost=total_labor_cost,
        cost_per_customer=cost_per_customer,
    )


@bp.post("/<int:period_id>/confirm")
def confirm(period_id):
    period = repo.get_period(period_id)
    if period:
        period.status = "confirmed"
        repo.save_period(period)
        flash("シフトを確定しました", "success")
    return redirect(url_for("schedule.index", period_id=period_id))


@bp.post("/<int:period_id>/unconfirm")
def unconfirm(period_id):
    period = repo.get_period(period_id)
    if period:
        period.status = "draft"
        repo.save_period(period)
        flash("確定を解除しました", "info")
    return redirect(url_for("schedule.index", period_id=period_id))
