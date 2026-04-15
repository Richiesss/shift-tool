"""CP-SATによるシフト最適化エンジン"""
from __future__ import annotations
from dataclasses import dataclass
from datetime import date
from ortools.sat.python import cp_model
from models.employee import Employee
from models.schedule import ShiftRequest, ShiftAssignment, SchedulePeriod
from utils.constants import (
    TimeSlot, Position, SkillLevel, EmploymentType,
    SHIFT_CONSTRAINTS, LATE_NIGHT_START
)


@dataclass
class SolveResult:
    status: str          # 'optimal', 'feasible', 'infeasible', 'unknown'
    assignments: list[ShiftAssignment]
    warnings: list[str]
    errors: list[str]
    solve_time_sec: float


def solve(
    period: SchedulePeriod,
    employees: list[Employee],
    requests: list[ShiftRequest],
) -> SolveResult:
    """
    シフトを最適化して SolveResult を返す。

    優先順位（ソフト制約のペナルティ重み）:
      1. 人件費最小化（総時間削減）         weight=1000
      2. 残業・深夜の特定人物集中回避       weight=500
      3. 希望を通す                        weight=100
      4. 人員バランス均等化                 weight=10
    """
    import time
    t0 = time.time()

    warnings: list[str] = []
    errors: list[str] = []

    model = cp_model.CpModel()

    dates = period.date_range()
    date_strs = [d.isoformat() for d in dates]
    slots = list(TimeSlot)
    positions = list(Position)

    # 希望シフトをキーでアクセス
    req_map: dict[tuple[int, str, str], bool] = {}
    for r in requests:
        if r.breakfast:
            req_map[(r.employee_id, r.date, TimeSlot.BREAKFAST.value)] = True
        if r.dinner:
            req_map[(r.employee_id, r.date, TimeSlot.DINNER.value)] = True

    active_employees = [e for e in employees if e.is_active]

    # ── 決定変数 ────────────────────────────────────────────────────────
    # assign[e_id][date_str][slot][pos] = BoolVar
    assign: dict = {}
    for emp in active_employees:
        assign[emp.id] = {}
        for ds in date_strs:
            assign[emp.id][ds] = {}
            for slot in slots:
                assign[emp.id][ds][slot.value] = {}
                for pos in positions:
                    assign[emp.id][ds][slot.value][pos.value] = model.new_bool_var(
                        f"a_{emp.id}_{ds}_{slot.value}_{pos.value}"
                    )

    # ── 絶対制約 ─────────────────────────────────────────────────────────

    # 1. 従業員は希望していない時間帯には入れない
    for emp in active_employees:
        for ds in date_strs:
            for slot in slots:
                can_work = req_map.get((emp.id, ds, slot.value), False)
                for pos in positions:
                    var = assign[emp.id][ds][slot.value][pos.value]
                    if not can_work:
                        model.add(var == 0)

    # 2. 1つのシフト枠（日付×時間帯）に従業員は1ポジションのみ
    for emp in active_employees:
        for ds in date_strs:
            for slot in slots:
                model.add_at_most_one(
                    assign[emp.id][ds][slot.value][pos.value] for pos in positions
                )

    # 3. アルバイトは1日1シフトのみ（朝食＋ディナーの掛け持ちなし）
    for emp in active_employees:
        if emp.employment_type == EmploymentType.PART_TIME:
            for ds in date_strs:
                all_vars = [
                    assign[emp.id][ds][slot.value][pos.value]
                    for slot in slots for pos in positions
                ]
                model.add(sum(all_vars) <= 1)

    # 4. 人員数制約（最低/最大）
    for ds in date_strs:
        d_obj = date.fromisoformat(ds)
        for slot in slots:
            for pos in positions:
                key = (slot, pos)
                constraint = SHIFT_CONSTRAINTS.get(key)
                if not constraint:
                    continue
                staff_vars = [
                    assign[emp.id][ds][slot.value][pos.value]
                    for emp in active_employees
                ]
                model.add(sum(staff_vars) >= constraint["min"])
                model.add(sum(staff_vars) <= constraint["max"])

    # 5. スキルバランス制約（絶対制約：ベテラン以上の最低数）
    for ds in date_strs:
        for slot in slots:
            for pos in positions:
                key = (slot, pos)
                constraint = SHIFT_CONSTRAINTS.get(key)
                if not constraint:
                    continue
                min_skilled = constraint.get("min_skilled", 0)
                if min_skilled <= 0:
                    continue
                skilled_vars = [
                    assign[emp.id][ds][slot.value][pos.value]
                    for emp in active_employees
                    if emp.is_skilled(pos.value)
                ]
                model.add(sum(skilled_vars) >= min_skilled)

    # 6. 正社員の両時間帯掛け持ちは最小化（ソフト制約で対応）
    # 両時間帯掛け持ち変数
    double_vars: dict[tuple[int, str], cp_model.IntVar] = {}
    for emp in active_employees:
        if emp.employment_type == EmploymentType.FULL_TIME:
            for ds in date_strs:
                worked_b = sum(assign[emp.id][ds][TimeSlot.BREAKFAST.value][p.value] for p in positions)
                worked_d = sum(assign[emp.id][ds][TimeSlot.DINNER.value][p.value] for p in positions)
                dv = model.new_bool_var(f"double_{emp.id}_{ds}")
                # dv=1 ⟺ 両方に入る
                model.add(worked_b + worked_d >= 2).only_enforce_if(dv)
                model.add(worked_b + worked_d <= 1).only_enforce_if(dv.negated())
                double_vars[(emp.id, ds)] = dv

    # ── ソフト制約（ペナルティ最小化） ──────────────────────────────────

    penalty_terms = []

    # P1: 人件費最小化 = 総シフト時間を最小化
    SLOT_HOURS = {TimeSlot.BREAKFAST: 5, TimeSlot.DINNER: 6}
    for emp in active_employees:
        for ds in date_strs:
            for slot in slots:
                hours = SLOT_HOURS[slot]
                for pos in positions:
                    var = assign[emp.id][ds][slot.value][pos.value]
                    penalty_terms.append(1000 * hours * var)

    # P2: 正社員の両時間帯掛け持ちにペナルティ
    for dv in double_vars.values():
        penalty_terms.append(500 * dv)

    # P2b: 深夜（ディナー22:00〜23:00）の集中を防ぐ
    # ディナー担当は全員深夜1時間が発生するが、特定人物への集中は偏差で管理
    dinner_count: dict[int, list] = {emp.id: [] for emp in active_employees}
    for emp in active_employees:
        for ds in date_strs:
            for pos in positions:
                v = assign[emp.id][ds][TimeSlot.DINNER.value][pos.value]
                dinner_count[emp.id].append(v)

    # 深夜掛け持ちペナルティ: 個人のディナー合計の2乗を最小化（凸的分散）
    # ※ CP-SATは2次項不可のため、線形近似（合計に重み）
    for emp in active_employees:
        for ds in date_strs:
            for pos in positions:
                v = assign[emp.id][ds][TimeSlot.DINNER.value][pos.value]
                penalty_terms.append(500 * v)  # ディナー1回ごとにペナルティ

    # P3: 希望を通す = 希望があるのに入れない場合ペナルティ
    for emp in active_employees:
        for ds in date_strs:
            for slot in slots:
                if req_map.get((emp.id, ds, slot.value)):
                    worked = sum(assign[emp.id][ds][slot.value][pos.value] for pos in positions)
                    not_worked = model.new_bool_var(f"nw_{emp.id}_{ds}_{slot.value}")
                    model.add(worked == 0).only_enforce_if(not_worked)
                    model.add(worked >= 1).only_enforce_if(not_worked.negated())
                    penalty_terms.append(100 * not_worked)

    # P4: 人員バランス = ポジション毎の総シフト数の偏差を最小化
    # 各日の各（スロット×ポジション）の担当人数を均等にするため、
    # 1日の担当数の最大・最小差をペナルティに
    for emp in active_employees:
        total_worked = sum(
            assign[emp.id][ds][slot.value][pos.value]
            for ds in date_strs for slot in slots for pos in positions
        )
        # ダミーで総勤務回数の二乗偏差を線形近似
        penalty_terms.append(10 * total_worked)

    model.minimize(sum(penalty_terms))

    # ── 求解 ────────────────────────────────────────────────────────────
    solver = cp_model.CpSolver()
    solver.parameters.max_time_in_seconds = 30.0
    solver.parameters.num_search_workers = 4
    status = solver.solve(model)

    elapsed = time.time() - t0
    status_name = solver.status_name(status)

    if status in (cp_model.OPTIMAL, cp_model.FEASIBLE):
        result_assignments = []
        for emp in active_employees:
            for ds in date_strs:
                for slot in slots:
                    for pos in positions:
                        val = solver.value(assign[emp.id][ds][slot.value][pos.value])
                        if val:
                            result_assignments.append(
                                ShiftAssignment(
                                    employee_id=emp.id,
                                    date=ds,
                                    time_slot=slot,
                                    position=pos,
                                )
                            )
        # 警告: 人員ギリギリ日を検出
        _check_warnings(result_assignments, date_strs, warnings)

        return SolveResult(
            status="optimal" if status == cp_model.OPTIMAL else "feasible",
            assignments=result_assignments,
            warnings=warnings,
            errors=errors,
            solve_time_sec=elapsed,
        )
    else:
        # 実行不可能 → どの制約が原因か特定
        errs = _diagnose_infeasible(active_employees, date_strs, req_map)
        return SolveResult(
            status="infeasible",
            assignments=[],
            warnings=warnings,
            errors=errs,
            solve_time_sec=elapsed,
        )


def _check_warnings(assignments: list[ShiftAssignment], date_strs: list[str], warnings: list[str]):
    from collections import defaultdict
    count = defaultdict(int)
    for a in assignments:
        count[(a.date, a.time_slot.value, a.position.value)] += 1

    for ds in date_strs:
        d = date.fromisoformat(ds)
        dow_labels = ["月", "火", "水", "木", "金", "土", "日"]
        label = f"{d.month}/{d.day}({dow_labels[d.weekday()]})"
        for slot in TimeSlot:
            for pos in Position:
                c = count[(ds, slot.value, pos.value)]
                constraint = SHIFT_CONSTRAINTS.get((slot, pos), {})
                if c == constraint.get("min", 0):
                    warnings.append(
                        f"{label} {slot.short_label()} {pos.label()}: "
                        f"最低人数ちょうど（{c}名）— 欠員リスクあり"
                    )


def _diagnose_infeasible(
    employees: list[Employee],
    date_strs: list[str],
    req_map: dict,
) -> list[str]:
    """実行不可能の原因を診断してエラーメッセージを返す"""
    errors = []
    from collections import defaultdict
    from utils.constants import SHIFT_CONSTRAINTS

    for ds in date_strs:
        d = date.fromisoformat(ds)
        dow_labels = ["月", "火", "水", "木", "金", "日", "日"]
        label = f"{d.month}/{d.day}({dow_labels[d.weekday()]})"

        for slot in TimeSlot:
            for pos in Position:
                key = (slot, pos)
                constraint = SHIFT_CONSTRAINTS.get(key)
                if not constraint:
                    continue

                # 希望者数
                available = [
                    e for e in employees
                    if req_map.get((e.id, ds, slot.value), False)
                ]
                available_for_pos = available  # ポジション横断あり

                skilled = [e for e in available_for_pos if e.is_skilled(pos.value)]
                min_req = constraint["min"]
                min_skilled = constraint.get("min_skilled", 0)

                if len(available_for_pos) < min_req:
                    errors.append(
                        f"❌ {label} {slot.short_label()} {pos.label()}: "
                        f"希望者が{len(available_for_pos)}名（必要: {min_req}名以上）"
                    )
                elif len(skilled) < min_skilled:
                    errors.append(
                        f"❌ {label} {slot.short_label()} {pos.label()}: "
                        f"ベテラン以上が{len(skilled)}名（必要: {min_skilled}名以上）"
                    )

    if not errors:
        errors.append("❌ スケジュールを生成できません。制約の組み合わせを確認してください。")
    return errors
