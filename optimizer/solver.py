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
from utils.solver_logger import logger


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
    balance_scale: float = 1.0         # 人員バランス均等化
    late_night_scale: float = 1.0      # 深夜勤務分散（アルバイトのみ）


class SolveProgressCallback(cp_model.CpSolverSolutionCallback):
    """解が見つかるたびに DB の gen_message を更新するコールバック"""

    def __init__(self, period_id: int, max_time: float = 10.0):
        super().__init__()
        self._period_id = period_id
        self._max_time = max_time
        self._n = 0
        self._last_update = 0.0

    def on_solution_callback(self):
        self._n += 1
        elapsed = self.WallTime()
        if elapsed - self._last_update < 1.5:
            return
        self._last_update = elapsed
        pct = min(90, int(elapsed / self._max_time * 100))
        try:
            obj = int(self.ObjectiveValue())
        except Exception:
            obj = -1
        try:
            from db import repositories as repo
            repo.update_period_gen_status(
                self._period_id, "generating",
                f"phase:1,progress:{pct},solutions:{self._n},elapsed:{elapsed:.1f},obj:{obj}"
            )
        except Exception:
            pass


def solve(
    period: SchedulePeriod,
    employees: list[Employee],
    requests: list[ShiftRequest],
    config: SolverConfig | None = None,
    progress_callback: SolveProgressCallback | None = None,
    period_id: int | None = None,
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

    # 希望シフト（アルバイト用）と社員の休希望・有休を分けて管理
    req_map: dict[tuple[int, str, str], bool] = {}
    off_map: dict[tuple[int, str], bool] = {}   # 社員の休希望・有休
    for r in requests:
        if r.pattern_id in ("off_request", "paid_leave"):
            off_map[(r.employee_id, r.date)] = True
        else:
            if r.breakfast:
                req_map[(r.employee_id, r.date, TimeSlot.BREAKFAST.value)] = True
            if r.dinner:
                req_map[(r.employee_id, r.date, TimeSlot.DINNER.value)] = True

    active_employees = [e for e in employees if e.is_active]

    # ── ログ: 入力サマリー ────────────────────────────────────────────────
    logger.info("=" * 60)
    logger.info(f"[SOLVE START] period_id={period.id}  {period.start_date} 〜 {period.end_date}")
    logger.info(f"  日数={len(date_strs)}  有効従業員={len(active_employees)}  希望レコード={len(requests)}")
    logger.info(f"  config: cost={config.cost_scale} pt_pref={config.pt_pref_scale} "
                f"double={config.double_penalty_scale} balance={config.balance_scale} "
                f"late_night={config.late_night_scale}")
    ft_count = sum(1 for e in active_employees if e.employment_type == EmploymentType.FULL_TIME)
    pt_count = len(active_employees) - ft_count
    always_b = sum(1 for e in active_employees if e.always_available_breakfast)
    always_d = sum(1 for e in active_employees if e.always_available_dinner)
    logger.info(f"  正社員={ft_count}  アルバイト={pt_count}  "
                f"常時出勤可(朝)={always_b}  常時出勤可(夜)={always_d}")
    req_counts = {"breakfast": sum(1 for k in req_map if k[2] == TimeSlot.BREAKFAST.value),
                  "dinner":    sum(1 for k in req_map if k[2] == TimeSlot.DINNER.value)}
    logger.info(f"  req_map: 朝食希望={req_counts['breakfast']}件  ディナー希望={req_counts['dinner']}件")

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

    # ── ログ: 制約設定 & 充足前チェック ──────────────────────────────────
    logger.info("  [制約設定]")
    for (slot, pos), c in sorted(shift_constraints.items(), key=lambda x: (x[0][0].value, x[0][1].value)):
        logger.info(f"    {slot.value}/{pos.value}: min={c['min']} max={c['max']} min_leader={c.get('min_leader',0)}")
    for (band, pos), c in sorted(band_constraints.items()):
        if c.get("min", 0) > 0 or c.get("min_leader", 0) > 0:
            logger.info(f"    band {band}/{pos}: min={c.get('min',0)} min_leader={c.get('min_leader',0)}")
    logger.info("  [充足前チェック] (各日×スロット×ポジションの利用可能人数 vs 必要最低数)")
    shortage_days: list[str] = []
    for ds in date_strs:
        rc = reservation_counts.get(ds, {})
        for slot in slots:
            for pos in positions:
                c = shift_constraints.get((slot, pos))
                if not c:
                    continue
                base_min = c["min"]
                if slot == TimeSlot.BREAKFAST and rc.get("breakfast", 0) >= reserv_thresh_b > 0:
                    base_min += reserv_extra_b
                elif slot == TimeSlot.DINNER and rc.get("dinner", 0) >= reserv_thresh_d > 0:
                    base_min += reserv_extra_d
                avail = []
                for e in active_employees:
                    # FIX①: FT社員を正しくカウント（実際の可用性チェックと統一）
                    if e.employment_type == EmploymentType.FULL_TIME:
                        ok = not off_map.get((e.id, ds), False) and ds not in e.fixed_unavailable_dates
                    elif slot == TimeSlot.BREAKFAST and e.always_available_breakfast:
                        ok = ds not in e.fixed_unavailable_dates
                    elif slot == TimeSlot.DINNER and e.always_available_dinner:
                        ok = ds not in e.fixed_unavailable_dates
                    else:
                        ok = req_map.get((e.id, ds, slot.value), False)
                    if not ok:
                        continue
                    if e.primary_position is not None and not e.can_work_both_positions:
                        if e.primary_position.value != pos.value:
                            continue
                    avail.append(e)
                leaders = [e for e in avail if e.is_leader(pos.value)]
                min_ldr = c.get("min_leader", 0)
                ok_staff  = len(avail) >= base_min
                ok_leader = len(leaders) >= min_ldr
                if not ok_staff or not ok_leader:
                    msg = (f"    NG  {ds} {slot.value}/{pos.value}: "
                           f"利用可={len(avail)}(必要{base_min})  "
                           f"リーダー={len(leaders)}(必要{min_ldr})")
                    logger.warning(msg)
                    shortage_days.append(msg)
    if not shortage_days:
        logger.info("    → 全日程で最低人数を満たせる見込み")

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
                if emp.employment_type == EmploymentType.FULL_TIME:
                    # 社員：休希望・有休の日以外は常時出勤可
                    can_work = not off_map.get((emp.id, ds), False) \
                               and ds not in emp.fixed_unavailable_dates
                elif slot == TimeSlot.BREAKFAST and emp.always_available_breakfast:
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
            # FIX③: FT社員も can_open 要件に含める
            can_open_req = [
                emp for emp in active_employees
                if emp.can_open and (
                    req_map.get((emp.id, ds, TimeSlot.BREAKFAST.value))
                    or (emp.employment_type == EmploymentType.FULL_TIME
                        and not off_map.get((emp.id, ds), False)
                        and ds not in emp.fixed_unavailable_dates)
                )
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
                # FIX④: FT社員も can_cleanup 要件に含める
                cln_vars = [
                    assign[emp.id][ds][TimeSlot.BREAKFAST.value][pos.value]
                    for emp in cleanup_pool
                    if req_map.get((emp.id, ds, TimeSlot.BREAKFAST.value))
                    or emp.always_available_breakfast
                    or (emp.employment_type == EmploymentType.FULL_TIME
                        and not off_map.get((emp.id, ds), False)
                        and ds not in emp.fixed_unavailable_dates)
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

    # 6. 正社員の両時間帯掛け持ちを物理的に禁止（ハード制約）
    # 朝食＋ディナーの合計を 1 以下に制限
    for emp in active_employees:
        if emp.employment_type == EmploymentType.FULL_TIME:
            for ds in date_strs:
                worked_b = sum(assign[emp.id][ds][TimeSlot.BREAKFAST.value][p.value] for p in positions)
                worked_d = sum(assign[emp.id][ds][TimeSlot.DINNER.value][p.value] for p in positions)
                model.add(worked_b + worked_d <= 1)
    double_vars = {}  # P2ペナルティ参照のため空dict（使用しない）

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

    # P2: 両時間帯掛け持ちはハード制約で禁止済み（ペナルティ不要）

    # P2b: 深夜勤務分散（アルバイトのみ対象）
    # FIX⑤: FT社員はディナーが基本業務のためペナルティ不要
    # PT社員のディナー担当を均等分散させる
    late_night_w = max(1, int(500 * config.late_night_scale))
    for emp in active_employees:
        if emp.employment_type == EmploymentType.FULL_TIME:
            continue  # 社員はディナー出勤がデフォルト、ペナルティ対象外
        for ds in date_strs:
            for pos in positions:
                v = assign[emp.id][ds][TimeSlot.DINNER.value][pos.value]
                penalty_terms.append(late_night_w * v)

    # P3: アルバイトの希望充当（FIX②: FT社員は req_map を持たないため除外）
    # 社員は off_map で管理済みのため、ここではアルバイトのみ対象
    PT_NOT_WORKED_PENALTY = max(1, int(100 * config.pt_pref_scale))
    for emp in active_employees:
        if emp.employment_type == EmploymentType.FULL_TIME:
            continue  # 社員は希望提出しない（off_map で管理）
        for ds in date_strs:
            for slot in slots:
                if req_map.get((emp.id, ds, slot.value)):
                    worked = sum(assign[emp.id][ds][slot.value][pos.value] for pos in positions)
                    not_worked = model.new_bool_var(f"nw_{emp.id}_{ds}_{slot.value}")
                    model.add(worked == 0).only_enforce_if(not_worked)
                    model.add(worked >= 1).only_enforce_if(not_worked.negated())
                    penalty_terms.append(PT_NOT_WORKED_PENALTY * not_worked)

    # P3b: primary_timeslot 専任制約は P5 のハード制約でカバー済みのため削除（FIX⑦）

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

    # P5: スロット別雇用形態優先度 + 条件付きハード制約
    # ──────────────────────────────────────────────────────────────────
    # ① PT が最低人数以上いる朝食：FT を物理的に配置不可（ハード制約）
    # ② FT が最低人数以上いるディナー：PT を物理的に配置不可（ハード制約）
    # ③ 上記を満たせない日のみ P5 ソフトペナルティで誘導
    # ──────────────────────────────────────────────────────────────────
    logger.info("  [P5 スロット別分業チェック]")
    for ds in date_strs:
        rc = reservation_counts.get(ds, {})
        for slot in slots:
            for pos in positions:
                c = shift_constraints.get((slot, pos), {})
                base_min = c.get("min", 0)
                if slot == TimeSlot.BREAKFAST and rc.get("breakfast", 0) >= reserv_thresh_b > 0:
                    base_min += reserv_extra_b
                elif slot == TimeSlot.DINNER and rc.get("dinner", 0) >= reserv_thresh_d > 0:
                    base_min += reserv_extra_d

                # PT の利用可能人数を数える
                pt_avail = 0
                for emp in active_employees:
                    if emp.employment_type == EmploymentType.FULL_TIME:
                        continue
                    if slot == TimeSlot.BREAKFAST and emp.always_available_breakfast:
                        slot_ok = ds not in emp.fixed_unavailable_dates
                    elif slot == TimeSlot.DINNER and emp.always_available_dinner:
                        slot_ok = ds not in emp.fixed_unavailable_dates
                    else:
                        slot_ok = req_map.get((emp.id, ds, slot.value), False)
                    if not slot_ok:
                        continue
                    if emp.primary_position is not None and not emp.can_work_both_positions:
                        if emp.primary_position.value != pos.value:
                            continue
                    pt_avail += 1

                # FT の利用可能人数を数える
                ft_avail = 0
                for emp in active_employees:
                    if emp.employment_type != EmploymentType.FULL_TIME:
                        continue
                    if off_map.get((emp.id, ds), False) or ds in emp.fixed_unavailable_dates:
                        continue
                    if emp.primary_position is not None and not emp.can_work_both_positions:
                        if emp.primary_position.value != pos.value:
                            continue
                    ft_avail += 1

                slot_pos = f"{ds} {slot.value}/{pos.value}"

                if slot == TimeSlot.BREAKFAST and pt_avail >= base_min:
                    # 朝食：PT が充足 → FT を物理的に配置禁止
                    for emp in active_employees:
                        if emp.employment_type == EmploymentType.FULL_TIME:
                            model.add(assign[emp.id][ds][slot.value][pos.value] == 0)
                    logger.info(f"    {slot_pos}: PT={pt_avail}≥min={base_min} → FT配置禁止（ハード）")
                elif slot == TimeSlot.DINNER and ft_avail >= base_min:
                    # ディナー：FT が充足 → PT を物理的に配置禁止
                    for emp in active_employees:
                        if emp.employment_type != EmploymentType.FULL_TIME:
                            model.add(assign[emp.id][ds][slot.value][pos.value] == 0)
                    logger.info(f"    {slot_pos}: FT={ft_avail}≥min={base_min} → PT配置禁止（ハード）")
                else:
                    # 充足しない場合はソフトペナルティで誘導
                    shortage_type = "PT不足" if slot == TimeSlot.BREAKFAST else "FT不足"
                    logger.info(f"    {slot_pos}: {shortage_type}(PT={pt_avail},FT={ft_avail},min={base_min}) → ソフト誘導")
                    BREAKFAST_FT_COST = 500_000
                    DINNER_PT_COST    = 300_000
                    for emp in active_employees:
                        if slot == TimeSlot.BREAKFAST and emp.employment_type == EmploymentType.FULL_TIME:
                            penalty_terms.append(
                                BREAKFAST_FT_COST * assign[emp.id][ds][slot.value][pos.value]
                            )
                        elif slot == TimeSlot.DINNER and emp.employment_type != EmploymentType.FULL_TIME:
                            penalty_terms.append(
                                DINNER_PT_COST * assign[emp.id][ds][slot.value][pos.value]
                            )

    model.minimize(cp_model.LinearExpr.Sum(penalty_terms))

    # ── 求解 ────────────────────────────────────────────────────────────
    solver = cp_model.CpSolver()
    solver.parameters.max_time_in_seconds = 25.0
    solver.parameters.num_search_workers = 1
    solver.parameters.log_search_progress = False
    logger.info(f"  [CP-SAT 求解開始] max_time={solver.parameters.max_time_in_seconds}s "
                f"workers={solver.parameters.num_search_workers}")
    status = solver.solve(model, progress_callback) if progress_callback else solver.solve(model)

    elapsed = time.time() - t0
    status_name = solver.status_name(status)

    try:
        nb = solver.num_branches() if callable(solver.num_branches) else solver.num_branches
        nc = solver.num_conflicts() if callable(solver.num_conflicts) else solver.num_conflicts
    except Exception:
        nb = nc = "?"
    logger.info(f"  [CP-SAT 求解完了] status={status_name}  wall={elapsed:.2f}s  "
                f"branches={nb}  conflicts={nc}")

    if status in (cp_model.OPTIMAL, cp_model.FEASIBLE):
        try:
            obj_val = int(solver.objective_value)
            logger.info(f"  objective={obj_val}")
        except Exception:
            pass
        result_assignments = _extract_assignments(solver, assign, active_employees, date_strs, slots, positions)
        _log_assignment_summary(result_assignments, date_strs)
        _check_warnings(result_assignments, date_strs, warnings)
        if warnings:
            for w in warnings:
                logger.warning(f"  [警告] {w}")
        logger.info(f"[SOLVE END] status='{status_name}'  assignments={len(result_assignments)}  "
                    f"warnings={len(warnings)}  time={elapsed:.2f}s")
        return SolveResult(
            status="optimal" if status == cp_model.OPTIMAL else "feasible",
            assignments=result_assignments,
            warnings=warnings,
            errors=errors,
            solve_time_sec=elapsed,
        )
    else:
        logger.error(f"  [INFEASIBLE/UNKNOWN] status={status_name} → フェーズ2へ移行")
        # 実行不可能 → 制約違反の診断メッセージを収集
        errs = _diagnose_infeasible(active_employees, date_strs, req_map)
        for e in errs:
            logger.error(f"  [診断] {e}")

        # フェーズ2開始を通知
        _notify_phase2(period_id or (progress_callback._period_id if progress_callback else None))

        # ベストエフォート生成: 人数・リーダー制約をソフト化して再実行
        logger.info("  [フェーズ2] ベストエフォート求解開始")
        best_assignments, best_warnings = _solve_best_effort(
            active_employees, date_strs, slots, positions,
            req_map, off_map, req_hours, config, shift_constraints
        )
        _check_warnings(best_assignments, date_strs, best_warnings)
        _log_assignment_summary(best_assignments, date_strs)
        if best_warnings:
            for w in best_warnings:
                logger.warning(f"  [警告] {w}")
        logger.info(f"[SOLVE END] status='best_effort'  assignments={len(best_assignments)}  "
                    f"warnings={len(best_warnings)}  errors={len(errs)}  time={time.time()-t0:.2f}s")

        return SolveResult(
            status="feasible",   # ベストエフォートなので feasible 扱い
            assignments=best_assignments,
            warnings=best_warnings,
            errors=errs,         # どこが不足しているかを表示
            solve_time_sec=time.time() - t0,
        )


def _log_assignment_summary(assignments, date_strs):
    """日×スロット×ポジションごとの配置人数をログ出力"""
    from collections import defaultdict
    count = defaultdict(int)
    for a in assignments:
        count[(a.date, a.time_slot.value, a.position.value)] += 1
    logger.info("  [配置サマリー]")
    for ds in date_strs:
        parts = []
        for slot in TimeSlot:
            for pos in Position:
                n = count[(ds, slot.value, pos.value)]
                parts.append(f"{slot.value[0].upper()}/{pos.value[0].upper()}={n}")
        logger.info(f"    {ds}: {' '.join(parts)}")


def _notify_phase2(period_id):
    if period_id is None:
        return
    try:
        from db import repositories as repo
        repo.update_period_gen_status(
            period_id, "generating",
            "phase:2,progress:0,solutions:0,elapsed:0,obj:-1"
        )
    except Exception:
        pass


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
    req_map, off_map, req_hours, config: SolverConfig | None = None,
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
                if emp.employment_type == EmploymentType.FULL_TIME:
                    can_work = not off_map.get((emp.id, ds), False) \
                               and ds not in emp.fixed_unavailable_dates
                elif slot == TimeSlot.BREAKFAST and emp.always_available_breakfast:
                    can_work = ds not in emp.fixed_unavailable_dates
                elif slot == TimeSlot.DINNER and emp.always_available_dinner:
                    can_work = ds not in emp.fixed_unavailable_dates
                else:
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

    # 正社員の朝食＋ディナー両方出勤を物理的に禁止（ハード制約）
    for emp in active_employees:
        if emp.employment_type == EmploymentType.FULL_TIME:
            for ds in date_strs:
                worked_b = sum(assign[emp.id][ds][TimeSlot.BREAKFAST.value][p.value] for p in positions)
                worked_d = sum(assign[emp.id][ds][TimeSlot.DINNER.value][p.value] for p in positions)
                model.add(worked_b + worked_d <= 1)

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

    # アルバイト希望充当（FIX②同様: FT除外）
    PT_PENALTY = max(1, int(100 * config.pt_pref_scale))
    for emp in active_employees:
        if emp.employment_type == EmploymentType.FULL_TIME:
            continue
        for ds in date_strs:
            for slot in slots:
                if req_map.get((emp.id, ds, slot.value)):
                    worked = sum(assign[emp.id][ds][slot.value][pos.value] for pos in positions)
                    not_worked = model.new_bool_var(f"nw_be_{emp.id}_{ds}_{slot.value}")
                    model.add(worked == 0).only_enforce_if(not_worked)
                    model.add(worked >= 1).only_enforce_if(not_worked.negated())
                    penalty_terms.append(PT_PENALTY * not_worked)

    # FIX⑥: ベストエフォートにも P5 スロット分業のソフト誘導を追加
    BE_BREAKFAST_FT_COST = 500_000
    BE_DINNER_PT_COST    = 300_000
    for emp in active_employees:
        for ds in date_strs:
            for pos in positions:
                if emp.employment_type == EmploymentType.FULL_TIME:
                    penalty_terms.append(
                        BE_BREAKFAST_FT_COST * assign[emp.id][ds][TimeSlot.BREAKFAST.value][pos.value]
                    )
                else:
                    penalty_terms.append(
                        BE_DINNER_PT_COST * assign[emp.id][ds][TimeSlot.DINNER.value][pos.value]
                    )

    model.minimize(cp_model.LinearExpr.Sum(penalty_terms))

    solver = cp_model.CpSolver()
    solver.parameters.max_time_in_seconds = 15.0
    solver.parameters.num_search_workers = 1
    solver.parameters.log_search_progress = False
    logger.info(f"  [フェーズ2 CP-SAT 求解開始] max_time={solver.parameters.max_time_in_seconds}s")
    status = solver.solve(model)
    status_name = solver.status_name(status)
    try:
        nb = solver.num_branches() if callable(solver.num_branches) else solver.num_branches
        nc = solver.num_conflicts() if callable(solver.num_conflicts) else solver.num_conflicts
    except Exception:
        nb = nc = "?"
    logger.info(f"  [フェーズ2 CP-SAT 求解完了] status={status_name}  "
                f"branches={nb}  conflicts={nc}")

    warnings: list[str] = []
    if status in (cp_model.OPTIMAL, cp_model.FEASIBLE):
        return _extract_assignments(solver, assign, active_employees, date_strs, slots, positions), warnings
    # フォールバック: 全員不在
    logger.error("  [フェーズ2 FAILED] ベストエフォート生成にも失敗")
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
        dow_labels = ["月", "火", "水", "木", "金", "土", "日"]
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
