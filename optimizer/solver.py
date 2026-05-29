"""CP-SATによるシフト最適化エンジン"""
from __future__ import annotations
from dataclasses import dataclass
from datetime import date
from ortools.sat.python import cp_model
from models.employee import Employee
from models.schedule import ShiftRequest, ShiftAssignment, SchedulePeriod
from utils.constants import (
    TimeSlot, Position, SkillLevel, EmploymentType, PrimaryPosition,
    LATE_NIGHT_START
)


@dataclass
class SolveResult:
    status: str          # 'optimal', 'feasible', 'infeasible', 'unknown'
    assignments: list[ShiftAssignment]
    warnings: list[str]
    errors: list[str]
    solve_time_sec: float


# スケール係数のプリセット
PRIORITY_SCALE = {"低": 0.1, "中": 1.0, "高": 10.0}


@dataclass
class SolverConfig:
    """最適化の優先度設定（各スケールは PRIORITY_SCALE の値を使用）"""
    cost_scale: float = 1.0            # 人件費最小化
    pt_pref_scale: float = 1.0         # アルバイト希望充当
    double_penalty_scale: float = 1.0  # 正社員両掛け持ち回避
    balance_scale: float = 1.0         # 人員バランス均等化
    late_night_scale: float = 1.0      # 深夜勤務分散


def solve(
    period: SchedulePeriod,
    employees: list[Employee],
    requests: list[ShiftRequest],
    config: SolverConfig | None = None,
) -> SolveResult:
    """
    シフトを最適化して SolveResult を返す。

    優先順位（ソフト制約のペナルティ重み）:
      1. 人件費最小化（総時間削減）         weight=1000 × cost_scale
      2. 正社員両掛け持ち回避              weight=500 × double_penalty_scale
      2b. 深夜勤務分散                     weight=500 × late_night_scale
      3a. 正社員希望を必ず通す             weight=200000（固定）
      3b. アルバイト希望を通す             weight=100 × pt_pref_scale
      4. 人員バランス均等化                 weight=10 × balance_scale
    """
    import time
    t0 = time.time()
    if config is None:
        config = SolverConfig()

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

    # DB から制約・予約客数・アプリ設定を読み込む
    from db import repositories as repo
    shift_constraints      = repo.get_shift_constraints()
    band_constraints       = repo.get_breakfast_band_constraints()
    reservation_counts     = repo.get_reservation_counts(period.id)
    try:
        reserv_thresh_b = int(repo.get_app_setting("reserv_threshold_breakfast", "100"))
        reserv_extra_b  = int(repo.get_app_setting("reserv_extra_breakfast",     "1"))
        reserv_thresh_d = int(repo.get_app_setting("reserv_threshold_dinner",    "25"))
        reserv_extra_d  = int(repo.get_app_setting("reserv_extra_dinner",        "1"))
    except Exception:
        reserv_thresh_b = 100; reserv_extra_b = 1
        reserv_thresh_d = 25;  reserv_extra_d = 1

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

    # 0. 所属ポジション制約: primary_position が設定されていて兼務不可の場合は担当外のポジションに入れない
    for emp in active_employees:
        if emp.primary_position is None or emp.can_work_both_positions:
            continue
        restricted = [p for p in positions if p.value != emp.primary_position.value]
        for ds in date_strs:
            for slot in slots:
                for pos in restricted:
                    model.add(assign[emp.id][ds][slot.value][pos.value] == 0)

    # 1. 従業員は希望していない時間帯には入れない
    # 常時出勤可スタッフは固定不可日以外すべて勤務可能とする
    for emp in active_employees:
        for ds in date_strs:
            for slot in slots:
                if slot == TimeSlot.BREAKFAST and emp.always_available_breakfast:
                    can_work = ds not in emp.fixed_unavailable_dates
                elif slot == TimeSlot.DINNER and emp.always_available_dinner:
                    can_work = ds not in emp.fixed_unavailable_dates
                else:
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

    # 4. 人員数制約（最低/最大）＋予約客数による増員
    for ds in date_strs:
        rc = reservation_counts.get(ds, {})
        b_count = rc.get("breakfast", 0)
        d_count = rc.get("dinner",    0)
        for slot in slots:
            for pos in positions:
                key = (slot, pos)
                constraint = shift_constraints.get(key)
                if not constraint:
                    continue
                base_min = constraint["min"]
                base_max = constraint["max"]
                # 予約超過時の増員
                if slot == TimeSlot.BREAKFAST and b_count >= reserv_thresh_b > 0:
                    base_min += reserv_extra_b
                    base_max  = max(base_max, base_min)
                elif slot == TimeSlot.DINNER and d_count >= reserv_thresh_d > 0:
                    base_min += reserv_extra_d
                    base_max  = max(base_max, base_min)
                staff_vars = [
                    assign[emp.id][ds][slot.value][pos.value]
                    for emp in active_employees
                ]
                model.add(sum(staff_vars) >= base_min)
                model.add(sum(staff_vars) <= base_max)

    # 5. リーダー配置制約（絶対制約：リーダーの最低配置数）
    for ds in date_strs:
        for slot in slots:
            for pos in positions:
                key = (slot, pos)
                constraint = shift_constraints.get(key)
                if not constraint:
                    continue
                min_leader = constraint.get("min_leader", 0)
                if min_leader <= 0:
                    continue
                leader_vars = [
                    assign[emp.id][ds][slot.value][pos.value]
                    for emp in active_employees
                    if emp.is_leader(pos.value)
                ]
                model.add(sum(leader_vars) >= min_leader)

    # 5b. 開店準備制約（ポジション別）
    for pos in positions:
        bc_open = band_constraints.get(("open", pos.value), {})
        min_open = bc_open.get("min", 0)
        min_open_ldr = bc_open.get("min_leader", 0)
        if min_open <= 0 and min_open_ldr <= 0:
            continue
        for ds in date_strs:
            can_open_req = [
                emp for emp in active_employees
                if emp.can_open and req_map.get((emp.id, ds, TimeSlot.BREAKFAST.value))
            ]
            open_vars = [assign[emp.id][ds][TimeSlot.BREAKFAST.value][pos.value] for emp in can_open_req]
            if min_open > 0:
                if len(can_open_req) >= min_open:
                    model.add(sum(open_vars) >= min_open)
                else:
                    warnings.append(
                        f"{ds} 開店準備 {pos.label()}: 対応可 {len(can_open_req)} 名（必要 {min_open} 名）"
                    )
            if min_open_ldr > 0:
                ldr_open_vars = [
                    assign[emp.id][ds][TimeSlot.BREAKFAST.value][pos.value]
                    for emp in can_open_req if emp.is_leader(pos.value)
                ]
                if ldr_open_vars:
                    model.add(sum(ldr_open_vars) >= min(min_open_ldr, len(ldr_open_vars)))

    # 5c. 片付け制約（ポジション別）
    # can_cleanup スタッフが存在する場合はそのスタッフを対象にする。
    # 存在しない場合はリーダー以上を対象にしてフォールバック。
    cleanup_emps_by_pos = {
        pos: [e for e in active_employees if e.can_cleanup]
        for pos in positions
    }
    for pos in positions:
        bc_cln = band_constraints.get(("cleanup", pos.value), {})
        min_cln     = bc_cln.get("min", 0)
        min_cln_ldr = bc_cln.get("min_leader", 0)
        if min_cln <= 0 and min_cln_ldr <= 0:
            continue
        cleanup_pool = cleanup_emps_by_pos[pos]
        for ds in date_strs:
            if cleanup_pool:
                # can_cleanup スタッフで制約
                cln_vars = [
                    assign[emp.id][ds][TimeSlot.BREAKFAST.value][pos.value]
                    for emp in cleanup_pool
                    if req_map.get((emp.id, ds, TimeSlot.BREAKFAST.value))
                    or emp.always_available_breakfast
                ]
                if min_cln > 0 and cln_vars:
                    model.add(sum(cln_vars) >= min(min_cln, len(cln_vars)))
                if min_cln_ldr > 0:
                    ldr_cln_vars = [
                        assign[emp.id][ds][TimeSlot.BREAKFAST.value][pos.value]
                        for emp in cleanup_pool if emp.is_leader(pos.value)
                        if req_map.get((emp.id, ds, TimeSlot.BREAKFAST.value))
                        or emp.always_available_breakfast
                    ]
                    if ldr_cln_vars:
                        model.add(sum(ldr_cln_vars) >= min(min_cln_ldr, len(ldr_cln_vars)))
            else:
                # フォールバック: リーダー以上を対象
                all_b_vars = [assign[emp.id][ds][TimeSlot.BREAKFAST.value][pos.value] for emp in active_employees]
                if min_cln > 0:
                    model.add(sum(all_b_vars) >= min_cln)
                if min_cln_ldr > 0:
                    ldr_b_vars = [
                        assign[emp.id][ds][TimeSlot.BREAKFAST.value][pos.value]
                        for emp in active_employees if emp.is_leader(pos.value)
                    ]
                    if ldr_b_vars:
                        model.add(sum(ldr_b_vars) >= min(min_cln_ldr, len(ldr_b_vars)))

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

    # ── 従業員×日付のパターン別勤務時間マップ ────────────────────────────
    # (emp_id, date_str, slot_value) -> 実勤務時間(float)
    req_hours: dict[tuple[int, str, str], float] = {}
    for r in requests:
        from utils.shift_patterns import PATTERN_MAP, ShiftPattern
        if r.pattern_id and r.pattern_id != "custom":
            p = PATTERN_MAP.get(r.pattern_id)
            dur = p.duration_hours() if p else 5.0
        elif r.pattern_id == "custom" and r.custom_start and r.custom_end:
            sp = ShiftPattern("_c", "c", r.custom_start, r.custom_end)
            dur = sp.duration_hours()
        else:
            dur = 5.0  # フォールバック
        if r.breakfast:
            req_hours[(r.employee_id, r.date, TimeSlot.BREAKFAST.value)] = dur
        if r.dinner:
            req_hours[(r.employee_id, r.date, TimeSlot.DINNER.value)] = dur

    # ── ソフト制約（ペナルティ最小化） ──────────────────────────────────

    penalty_terms = []

    # P1: 人件費最小化 = 実勤務時間を最小化（パターン別の時間を使用）
    DEFAULT_SLOT_HOURS = {TimeSlot.BREAKFAST: 5.0, TimeSlot.DINNER: 6.0}
    for emp in active_employees:
        for ds in date_strs:
            for slot in slots:
                for pos in positions:
                    var = assign[emp.id][ds][slot.value][pos.value]
                    hours = req_hours.get(
                        (emp.id, ds, slot.value),
                        DEFAULT_SLOT_HOURS[slot]
                    )
                    # CP-SATは整数のみ → 時間×10で精度を保ちつつ整数化
                    w = max(1, int(1000 * hours * 10 * config.cost_scale))
                    penalty_terms.append(w * var)

    # P2: 正社員の両時間帯掛け持ちにペナルティ
    double_w = max(1, int(500 * config.double_penalty_scale))
    for dv in double_vars.values():
        penalty_terms.append(double_w * dv)

    # P2b: 深夜（ディナー22:00〜23:00）の集中を防ぐ
    # ディナー担当は全員深夜1時間が発生するが、特定人物への集中は偏差で管理
    # 深夜掛け持ちペナルティ: 個人のディナー合計の2乗を最小化（凸的分散）
    # ※ CP-SATは2次項不可のため、線形近似（合計に重み）
    late_night_w = max(1, int(500 * config.late_night_scale))
    for emp in active_employees:
        for ds in date_strs:
            for pos in positions:
                v = assign[emp.id][ds][TimeSlot.DINNER.value][pos.value]
                penalty_terms.append(late_night_w * v)  # ディナー1回ごとにペナルティ

    # P3: 希望を通す = 希望があるのに入れない場合ペナルティ
    # 正社員優先充当ルール: 正社員の未充当は最高優先でペナルティを課す
    # → 正社員が希望した枠には必ず入れ、余りをアルバイトで補完する
    FT_NOT_WORKED_PENALTY = 200_000  # 正社員: P1コスト（最大〜100000）を上回る重み（固定）
    PT_NOT_WORKED_PENALTY = max(1, int(100 * config.pt_pref_scale))
    for emp in active_employees:
        penalty = (FT_NOT_WORKED_PENALTY
                   if emp.employment_type == EmploymentType.FULL_TIME
                   else PT_NOT_WORKED_PENALTY)
        for ds in date_strs:
            for slot in slots:
                if req_map.get((emp.id, ds, slot.value)):
                    worked = sum(assign[emp.id][ds][slot.value][pos.value] for pos in positions)
                    not_worked = model.new_bool_var(f"nw_{emp.id}_{ds}_{slot.value}")
                    model.add(worked == 0).only_enforce_if(not_worked)
                    model.add(worked >= 1).only_enforce_if(not_worked.negated())
                    penalty_terms.append(penalty * not_worked)

    # P3b: primary_timeslot 専任制約（ソフト）
    # 専任設定のある従業員が専任外の時間帯に入る場合にペナルティ
    TIMESLOT_PENALTY = 5_000
    for emp in active_employees:
        if emp.primary_timeslot is None:
            continue
        non_primary = [s for s in slots if s != emp.primary_timeslot]
        for s in non_primary:
            for ds in date_strs:
                worked_off = sum(assign[emp.id][ds][s.value][pos.value] for pos in positions)
                off_var = model.new_bool_var(f"off_ts_{emp.id}_{ds}_{s.value}")
                model.add(worked_off == 0).only_enforce_if(off_var)
                model.add(worked_off >= 1).only_enforce_if(off_var.negated())
                penalty_terms.append(TIMESLOT_PENALTY * off_var.negated())

    # P4: 人員バランス = ポジション毎の総シフト数の偏差を最小化
    # 各日の各（スロット×ポジション）の担当人数を均等にするため、
    # 1日の担当数の最大・最小差をペナルティに
    balance_w = max(1, int(10 * config.balance_scale))
    for emp in active_employees:
        total_worked = sum(
            assign[emp.id][ds][slot.value][pos.value]
            for ds in date_strs for slot in slots for pos in positions
        )
        # ダミーで総勤務回数の二乗偏差を線形近似
        penalty_terms.append(balance_w * total_worked)

    model.minimize(sum(penalty_terms))

    # ── 求解 ────────────────────────────────────────────────────────────
    solver = cp_model.CpSolver()
    solver.parameters.max_time_in_seconds = 10.0
    solver.parameters.num_search_workers = 1
    solver.parameters.log_search_progress = False
    status = solver.solve(model)

    elapsed = time.time() - t0
    status_name = solver.status_name(status)

    if status in (cp_model.OPTIMAL, cp_model.FEASIBLE):
        result_assignments = _extract_assignments(solver, assign, active_employees, date_strs, slots, positions)
        _check_warnings(result_assignments, date_strs, warnings)
        return SolveResult(
            status="optimal" if status == cp_model.OPTIMAL else "feasible",
            assignments=result_assignments,
            warnings=warnings,
            errors=errors,
            solve_time_sec=elapsed,
        )
    else:
        # 実行不可能 → 制約違反の診断メッセージを収集
        errs = _diagnose_infeasible(active_employees, date_strs, req_map)

        # ベストエフォート生成: 人数・リーダー制約をソフト化して再実行
        best_assignments, best_warnings = _solve_best_effort(
            active_employees, date_strs, slots, positions,
            req_map, req_hours, config, shift_constraints
        )
        _check_warnings(best_assignments, date_strs, best_warnings)

        return SolveResult(
            status="feasible",   # ベストエフォートなので feasible 扱い
            assignments=best_assignments,
            warnings=best_warnings,
            errors=errs,         # どこが不足しているかを表示
            solve_time_sec=time.time() - t0,
        )


def _extract_assignments(solver, assign, active_employees, date_strs, slots, positions) -> list[ShiftAssignment]:
    result = []
    for emp in active_employees:
        for ds in date_strs:
            for slot in slots:
                for pos in positions:
                    if solver.value(assign[emp.id][ds][slot.value][pos.value]):
                        result.append(ShiftAssignment(
                            employee_id=emp.id, date=ds,
                            time_slot=slot, position=pos,
                        ))
    return result


def _solve_best_effort(
    active_employees, date_strs, slots, positions,
    req_map, req_hours, config: SolverConfig | None = None,
    shift_constraints: dict | None = None,
) -> tuple[list[ShiftAssignment], list[str]]:
    """人数・リーダー制約をソフト化してベストエフォートのシフトを生成する"""
    import time
    if config is None:
        config = SolverConfig()
    if shift_constraints is None:
        from db import repositories as repo
        shift_constraints = repo.get_shift_constraints()
    model = cp_model.CpModel()

    assign: dict = {}
    for emp in active_employees:
        assign[emp.id] = {}
        for ds in date_strs:
            assign[emp.id][ds] = {}
            for slot in slots:
                assign[emp.id][ds][slot.value] = {}
                for pos in positions:
                    assign[emp.id][ds][slot.value][pos.value] = model.new_bool_var(
                        f"be_{emp.id}_{ds}_{slot.value}_{pos.value}"
                    )

    # 絶対制約 0-3（ポジション・希望なし・1枠1ポジション・アルバイト1日制限）
    for emp in active_employees:
        if emp.primary_position is not None and not emp.can_work_both_positions:
            restricted = [p for p in positions if p.value != emp.primary_position.value]
            for ds in date_strs:
                for slot in slots:
                    for pos in restricted:
                        model.add(assign[emp.id][ds][slot.value][pos.value] == 0)

    for emp in active_employees:
        for ds in date_strs:
            for slot in slots:
                can_work = req_map.get((emp.id, ds, slot.value), False)
                for pos in positions:
                    if not can_work:
                        model.add(assign[emp.id][ds][slot.value][pos.value] == 0)

    for emp in active_employees:
        for ds in date_strs:
            for slot in slots:
                model.add_at_most_one(
                    assign[emp.id][ds][slot.value][pos.value] for pos in positions
                )

    for emp in active_employees:
        if emp.employment_type.value == "part_time":
            for ds in date_strs:
                all_vars = [
                    assign[emp.id][ds][slot.value][pos.value]
                    for slot in slots for pos in positions
                ]
                model.add(sum(all_vars) <= 1)

    penalty_terms = []
    STAFF_PENALTY = 1_000_000   # 人数不足ペナルティ（非常に高い）
    LEADER_PENALTY = 800_000    # リーダー不足ペナルティ

    for ds in date_strs:
        for slot in slots:
            for pos in positions:
                constraint = shift_constraints.get((slot, pos))
                if not constraint:
                    continue
                min_req = constraint["min"]
                max_req = constraint["max"]
                staff_vars = [assign[emp.id][ds][slot.value][pos.value] for emp in active_employees]

                # 最大は引き続き絶対制約
                model.add(sum(staff_vars) <= max_req)

                # 最低人数はソフト制約: shortfall 分をペナルティ
                shortfall = model.new_int_var(0, min_req, f"sf_{ds}_{slot.value}_{pos.value}")
                model.add(sum(staff_vars) + shortfall >= min_req)
                penalty_terms.append(STAFF_PENALTY * shortfall)

                # リーダー最低数もソフト制約
                min_leader = constraint.get("min_leader", 0)
                if min_leader > 0:
                    leader_vars = [
                        assign[emp.id][ds][slot.value][pos.value]
                        for emp in active_employees if emp.is_leader(pos.value)
                    ]
                    lshortfall = model.new_int_var(0, min_leader, f"lsf_{ds}_{slot.value}_{pos.value}")
                    model.add(sum(leader_vars) + lshortfall >= min_leader)
                    penalty_terms.append(LEADER_PENALTY * lshortfall)

    # 元のソフト制約（P1: 人件費、P3: 希望充当）も追加
    DEFAULT_SLOT_HOURS = {TimeSlot.BREAKFAST: 5.0, TimeSlot.DINNER: 6.0}
    for emp in active_employees:
        for ds in date_strs:
            for slot in slots:
                for pos in positions:
                    var = assign[emp.id][ds][slot.value][pos.value]
                    hours = req_hours.get((emp.id, ds, slot.value), DEFAULT_SLOT_HOURS[slot])
                    w = max(1, int(1000 * hours * 10 * config.cost_scale))
                    penalty_terms.append(w * var)

    FT_PENALTY = 200_000
    PT_PENALTY = max(1, int(100 * config.pt_pref_scale))
    for emp in active_employees:
        penalty = FT_PENALTY if emp.employment_type.value == "full_time" else PT_PENALTY
        for ds in date_strs:
            for slot in slots:
                if req_map.get((emp.id, ds, slot.value)):
                    worked = sum(assign[emp.id][ds][slot.value][pos.value] for pos in positions)
                    not_worked = model.new_bool_var(f"nw_be_{emp.id}_{ds}_{slot.value}")
                    model.add(worked == 0).only_enforce_if(not_worked)
                    model.add(worked >= 1).only_enforce_if(not_worked.negated())
                    penalty_terms.append(penalty * not_worked)

    model.minimize(sum(penalty_terms))

    solver = cp_model.CpSolver()
    solver.parameters.max_time_in_seconds = 10.0
    solver.parameters.num_search_workers = 1
    solver.parameters.log_search_progress = False
    status = solver.solve(model)

    warnings: list[str] = []
    if status in (cp_model.OPTIMAL, cp_model.FEASIBLE):
        return _extract_assignments(solver, assign, active_employees, date_strs, slots, positions), warnings
    # フォールバック: 全員不在
    return [], ["⚠️ ベストエフォート生成にも失敗しました。希望シフトの入力状況を確認してください。"]


def _check_warnings(assignments: list[ShiftAssignment], date_strs: list[str], warnings: list[str]):
    from collections import defaultdict
    from db import repositories as repo
    shift_constraints = repo.get_shift_constraints()
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
                constraint = shift_constraints.get((slot, pos), {})
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
    from db import repositories as repo
    shift_constraints = repo.get_shift_constraints()

    for ds in date_strs:
        d = date.fromisoformat(ds)
        dow_labels = ["月", "火", "水", "木", "金", "日", "日"]
        label = f"{d.month}/{d.day}({dow_labels[d.weekday()]})"

        for slot in TimeSlot:
            for pos in Position:
                key = (slot, pos)
                constraint = shift_constraints.get(key)
                if not constraint:
                    continue

                # 所属ポジションを考慮した希望者数
                available = [
                    e for e in employees
                    if req_map.get((e.id, ds, slot.value), False)
                    and (e.can_work_both_positions or e.primary_position is None or e.primary_position.value == pos.value)
                ]
                leaders = [e for e in available if e.is_leader(pos.value)]
                min_req = constraint["min"]
                min_leader = constraint.get("min_leader", 0)

                if len(available) < min_req:
                    errors.append(
                        f"❌ {label} {slot.short_label()} {pos.label()}: "
                        f"希望者が{len(available)}名（必要: {min_req}名以上）"
                    )
                elif len(leaders) < min_leader:
                    errors.append(
                        f"❌ {label} {slot.short_label()} {pos.label()}: "
                        f"リーダーが{len(leaders)}名（必要: {min_leader}名以上）"
                    )

    if not errors:
        errors.append("❌ スケジュールを生成できません。制約の組み合わせを確認してください。")
    return errors
