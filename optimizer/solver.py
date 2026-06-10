"""CP-SATによるシフト最適化エンジン"""
from __future__ import annotations
from dataclasses import dataclass
from datetime import date
from ortools.sat.python import cp_model
from models.employee import Employee
from models.schedule import ShiftRequest, ShiftAssignment, SchedulePeriod
from utils.constants import (
    TimeSlot, Position, SkillLevel, EmploymentType, PrimaryPosition,
)
from utils.solver_logger import logger
from utils.reservation import tiered_extra, effective_min_max
from utils.shift_patterns import is_long_breakfast_pattern


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


class Phase2ProgressCallback(cp_model.CpSolverSolutionCallback):
    """フェーズ2用: 解発見のたびにログとDBステータスを更新する"""

    def __init__(self, period_id: int | None, max_time: float = 45.0):
        super().__init__()
        self._period_id = period_id
        self._max_time  = max_time
        self._n = 0
        self._last_update = 0.0

    def on_solution_callback(self):
        self._n += 1
        elapsed = self.WallTime()
        if elapsed - self._last_update < 2.0:
            return
        self._last_update = elapsed
        pct = min(90, int(elapsed / self._max_time * 100))
        try:
            obj = int(self.ObjectiveValue())
        except Exception:
            obj = -1
        logger.info(
            f"  [フェーズ2] 解{self._n}件 elapsed={elapsed:.1f}s "
            f"progress={pct}% obj={obj}"
        )
        if self._period_id is not None:
            try:
                from db import repositories as repo
                repo.update_period_gen_status(
                    self._period_id, "generating",
                    f"phase:2,progress:{pct},solutions:{self._n},elapsed:{elapsed:.1f},obj:{obj}"
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
      2. 正社員両掛け持ち（朝食＋ディナー）はハード制約で禁止
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
    # 社員のフレックス出勤（朝のみ可→ディナー不可、晩のみ可→朝食不可）
    slot_block_map: dict[tuple[int, str], TimeSlot] = {}
    # 朝食「ロング勤務」（〜15:30頃まで）希望の (employee_id, date) 集合
    long_breakfast_set: set[tuple[int, str]] = set()
    for r in requests:
        if r.pattern_id in ("off_request", "paid_leave"):
            off_map[(r.employee_id, r.date)] = True
        elif r.pattern_id == "am_only":
            slot_block_map[(r.employee_id, r.date)] = TimeSlot.DINNER
        elif r.pattern_id == "pm_only":
            slot_block_map[(r.employee_id, r.date)] = TimeSlot.BREAKFAST
        else:
            if r.breakfast:
                req_map[(r.employee_id, r.date, TimeSlot.BREAKFAST.value)] = True
            if r.dinner:
                req_map[(r.employee_id, r.date, TimeSlot.DINNER.value)] = True
        if is_long_breakfast_pattern(r.pattern_id, r.custom_start, r.custom_end):
            long_breakfast_set.add((r.employee_id, r.date))

    active_employees = [e for e in employees if e.is_active]

    # ── ログ: 入力サマリー ────────────────────────────────────────────────
    logger.info("=" * 60)
    logger.info(f"[SOLVE START] period_id={period.id}  {period.start_date} 〜 {period.end_date}")
    logger.info(f"  日数={len(date_strs)}  有効従業員={len(active_employees)}  希望レコード={len(requests)}")
    logger.info(f"  config: cost={config.cost_scale} pt_pref={config.pt_pref_scale} "
                f"balance={config.balance_scale}")
    ft_count = sum(1 for e in active_employees if e.employment_type == EmploymentType.FULL_TIME)
    pt_count = len(active_employees) - ft_count
    always_b = sum(1 for e in active_employees if e.always_available_breakfast)
    always_d = sum(1 for e in active_employees if e.always_available_dinner)
    logger.info(f"  正社員={ft_count}  アルバイト={pt_count}  "
                f"常時出勤可(朝)={always_b}  常時出勤可(夜)={always_d}")
    req_counts = {"breakfast": sum(1 for k in req_map if k[2] == TimeSlot.BREAKFAST.value),
                  "dinner":    sum(1 for k in req_map if k[2] == TimeSlot.DINNER.value)}
    logger.info(f"  req_map: 朝食希望={req_counts['breakfast']}件  ディナー希望={req_counts['dinner']}件")

    # ── ログ: 全従業員DB情報ダンプ ───────────────────────────────────────────
    logger.info("-" * 60)
    logger.info("[従業員DBダンプ] 本番DBから読み込んだ全従業員情報")
    logger.info(f"  {'ID':>4} {'名前':<12} {'雇用':>5} {'H/B':>6} {'H/D':>6} {'K/B':>6} {'K/D':>6} "
                f"{'主担当':>8} {'両P':>3} {'おまB':>4} {'おまD':>4} {'社保':>3} {'時給':>5}")
    logger.info(f"  {'-'*4} {'-'*12} {'-'*5} {'-'*6} {'-'*6} {'-'*6} {'-'*6} "
                f"{'-'*8} {'-'*3} {'-'*4} {'-'*4} {'-'*3} {'-'*5}")
    for e in active_employees:
        hb = e.hall_skill_breakfast.value if hasattr(e, 'hall_skill_breakfast') else '?'
        hd = e.hall_skill_dinner.value if hasattr(e, 'hall_skill_dinner') else '?'
        kb = e.kitchen_skill_breakfast.value if hasattr(e, 'kitchen_skill_breakfast') else '?'
        kd = e.kitchen_skill_dinner.value if hasattr(e, 'kitchen_skill_dinner') else '?'
        pp = e.primary_position.value if e.primary_position else '-'
        both = '○' if e.can_work_both_positions else '×'
        omab = '○' if e.always_available_breakfast else '×'
        omad = '○' if e.always_available_dinner else '×'
        si = '○' if e.has_social_insurance else '×'
        wage = getattr(e, 'hourly_wage', 0) or 0
        emp_type = 'FT' if e.employment_type == EmploymentType.FULL_TIME else 'PT'
        logger.info(f"  {e.id:>4} {e.name:<12} {emp_type:>5} {hb:>6} {hd:>6} {kb:>6} {kd:>6} "
                    f"{pp:>8} {both:>3} {omab:>4} {omad:>4} {si:>3} {wage:>5}")
    logger.info("-" * 60)

    # DB から制約・予約客数・アプリ設定を読み込む
    from db import repositories as repo
    shift_constraints      = repo.get_shift_constraints()
    band_constraints       = repo.get_breakfast_band_constraints()
    reservation_counts     = repo.get_reservation_counts(period.id)
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

    try:
        max_consecutive_days = int(repo.get_app_setting("max_consecutive_days", "6"))
        min_days_off         = int(repo.get_app_setting("min_days_off", "5"))
    except Exception:
        max_consecutive_days = 6
        min_days_off = 5

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
                if slot == TimeSlot.BREAKFAST:
                    base_min += tiered_extra(rc.get("breakfast", 0), reserv_tiers_b)
                elif slot == TimeSlot.DINNER:
                    base_min += tiered_extra(rc.get("dinner", 0), reserv_tiers_d)
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
                leaders = [e for e in avail if e.is_leader(pos.value, slot)]
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
                    # （朝のみ可/晩のみ可の日は該当しない時間帯への配置を禁止）
                    can_work = not off_map.get((emp.id, ds), False) \
                               and ds not in emp.fixed_unavailable_dates \
                               and slot_block_map.get((emp.id, ds)) != slot
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
                # 予約超過時の増員
                if slot == TimeSlot.BREAKFAST:
                    extra = tiered_extra(b_count, reserv_tiers_b)
                elif slot == TimeSlot.DINNER:
                    extra = tiered_extra(d_count, reserv_tiers_d)
                else:
                    extra = 0
                base_min, base_max = effective_min_max(constraint, extra)
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
                    if emp.is_leader(pos.value, slot)
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
                        and slot_block_map.get((emp.id, ds)) != TimeSlot.BREAKFAST
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
                # FIX⑥: FT社員もリーダーチェックに含める（can_open_req は FT 含む）
                ldr_open_vars = [
                    assign[emp.id][ds][TimeSlot.BREAKFAST.value][pos.value]
                    for emp in can_open_req if emp.is_leader(pos.value, TimeSlot.BREAKFAST)
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
                        and slot_block_map.get((emp.id, ds)) != TimeSlot.BREAKFAST
                        and ds not in emp.fixed_unavailable_dates)
                ]
                if min_cln > 0 and cln_vars:
                    model.add(sum(cln_vars) >= min(min_cln, len(cln_vars)))
                if min_cln_ldr > 0:
                    # FIX②: FT社員のリーダーも含める（cln_vars と同じ可用性条件を適用）
                    ldr_cln_vars = [
                        assign[emp.id][ds][TimeSlot.BREAKFAST.value][pos.value]
                        for emp in cleanup_pool if emp.is_leader(pos.value, TimeSlot.BREAKFAST)
                        if req_map.get((emp.id, ds, TimeSlot.BREAKFAST.value))
                        or emp.always_available_breakfast
                        or (emp.employment_type == EmploymentType.FULL_TIME
                            and not off_map.get((emp.id, ds), False)
                            and slot_block_map.get((emp.id, ds)) != TimeSlot.BREAKFAST
                            and ds not in emp.fixed_unavailable_dates)
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
                        for emp in active_employees if emp.is_leader(pos.value, TimeSlot.BREAKFAST)
                    ]
                    if ldr_b_vars:
                        model.add(sum(ldr_b_vars) >= min(min_cln_ldr, len(ldr_b_vars)))

    # 5d. 朝食キッチンに「ロング勤務」（〜15:30頃まで）対象者を最低1名配置（ハード制約 / Issue #6）
    for ds in date_strs:
        long_candidates = [emp for emp in active_employees if (emp.id, ds) in long_breakfast_set]
        if long_candidates:
            long_vars = [
                assign[emp.id][ds][TimeSlot.BREAKFAST.value][Position.KITCHEN.value]
                for emp in long_candidates
            ]
            model.add(sum(long_vars) >= 1)
        else:
            warnings.append(
                f"{ds} 朝食 キッチン: ロング勤務（〜15:30頃まで）希望者がいません"
            )

    # 6. 正社員の両時間帯掛け持ちを物理的に禁止（ハード制約）
    # 朝食＋ディナーの合計を 1 以下に制限
    for emp in active_employees:
        if emp.employment_type == EmploymentType.FULL_TIME:
            for ds in date_strs:
                worked_b = sum(assign[emp.id][ds][TimeSlot.BREAKFAST.value][p.value] for p in positions)
                worked_d = sum(assign[emp.id][ds][TimeSlot.DINNER.value][p.value] for p in positions)
                model.add(worked_b + worked_d <= 1)

    # 6b. 日をまたいだ朝食⇄ディナーの連続勤務を禁止（休息時間確保のハード制約 / Issue #8）
    # 「前日ディナー→当日朝食」（休息 約7時間で不足）「前日朝食→当日ディナー」の
    # いずれも、同一従業員に連続させない
    for emp in active_employees:
        for ds, ds_next in zip(date_strs, date_strs[1:]):
            worked_d_prev = sum(assign[emp.id][ds][TimeSlot.DINNER.value][p.value] for p in positions)
            worked_b_prev = sum(assign[emp.id][ds][TimeSlot.BREAKFAST.value][p.value] for p in positions)
            worked_b_next = sum(assign[emp.id][ds_next][TimeSlot.BREAKFAST.value][p.value] for p in positions)
            worked_d_next = sum(assign[emp.id][ds_next][TimeSlot.DINNER.value][p.value] for p in positions)
            model.add(worked_d_prev + worked_b_next <= 1)
            model.add(worked_b_prev + worked_d_next <= 1)

    # ── 出勤日変数 worked_day[emp][date]（朝食 or ディナーいずれかに割当があれば1） ──
    # Issue #4・#5 で共用
    worked_day: dict[int, dict[str, object]] = {}
    for emp in active_employees:
        worked_day[emp.id] = {}
        for ds in date_strs:
            wd = model.new_bool_var(f"wd_{emp.id}_{ds}")
            worked_b = sum(assign[emp.id][ds][TimeSlot.BREAKFAST.value][p.value] for p in positions)
            worked_d = sum(assign[emp.id][ds][TimeSlot.DINNER.value][p.value] for p in positions)
            model.add(wd == worked_b + worked_d)
            worked_day[emp.id][ds] = wd

    # 6c. 連続勤務日数を制限（7日間スライディングウィンドウ・ハード制約 / Issue #5）
    CONSECUTIVE_WINDOW = 7
    for emp in active_employees:
        for i in range(len(date_strs) - CONSECUTIVE_WINDOW + 1):
            window = date_strs[i:i + CONSECUTIVE_WINDOW]
            model.add(sum(worked_day[emp.id][d] for d in window) <= max_consecutive_days)

    # 6d. 正社員は期間内（半月=2週間）で希望休含め最低日数の休みを取得（ハード制約 / Issue #4）
    if len(date_strs) > min_days_off:
        max_work_days = len(date_strs) - min_days_off
        for emp in active_employees:
            if emp.employment_type == EmploymentType.FULL_TIME:
                model.add(sum(worked_day[emp.id][d] for d in date_strs) <= max_work_days)

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

    # 社会保険加入アルバイトは、出勤する場合は必ず実働8時間勤務になるようハード制約（Issue #7）
    # duration_hours() は休憩考慮なしの総拘束時間を返す。
    # 実働8h + 法定休憩1h = 総拘束9h 以上のパターンのみ割当可とする。
    # 例: 5:45〜14:45（b_long = 9h） → 実働8h → 割当可
    #     6:30〜11:30（b_std  = 5h） → 実働4h → 割当不可
    SOCIAL_INSURANCE_MIN_HOURS = 9.0   # 実働8h + 休憩1h
    for emp in active_employees:
        if emp.employment_type == EmploymentType.FULL_TIME or not emp.has_social_insurance:
            continue
        for ds in date_strs:
            for slot in slots:
                dur = req_hours.get((emp.id, ds, slot.value))
                if dur is None or dur < SOCIAL_INSURANCE_MIN_HOURS:
                    for pos in positions:
                        model.add(assign[emp.id][ds][slot.value][pos.value] == 0)

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

    # P3: アルバイトの希望充当（FIX②: FT社員は req_map を持たないため除外）
    # 社員は off_map で管理済みのため、ここではアルバイトのみ対象
    #
    # 明示的希望 vs おまかせの扱い:
    # - 明示的希望: 「この日に入りたい」という意思表示 → 強いペナルティで必ず優先
    # - おまかせ: 「どの日でも可」という意思表示 → 特定の日への希望ではないためペナルティ免除
    #             余剰スロットを埋める役割に徹し、明示的希望者を押しのけない
    #
    # ※ 旧重み(100)は SI_PREF_PENALTY(50000)の1/500 しかなく、
    #   「明示的希望者を割当なくしてもSI優先の方が安い」状態になっていた。
    #   200,000 に引き上げてP1コスト+SI_PREFの合計を上回らせ、明示的希望を最優先する。
    EXPLICIT_PREF_PENALTY = max(1, int(200_000 * config.pt_pref_scale))
    for emp in active_employees:
        if emp.employment_type == EmploymentType.FULL_TIME:
            continue  # 社員は希望提出しない（off_map で管理）
        for ds in date_strs:
            for slot in slots:
                if not req_map.get((emp.id, ds, slot.value)):
                    continue
                # おまかせフラグが立っているスタッフは特定の日への希望ではないためペナルティ免除
                is_omakase = (slot == TimeSlot.BREAKFAST and emp.always_available_breakfast) or \
                             (slot == TimeSlot.DINNER and emp.always_available_dinner)
                if is_omakase:
                    continue
                worked = sum(assign[emp.id][ds][slot.value][pos.value] for pos in positions)
                not_worked = model.new_bool_var(f"nw_{emp.id}_{ds}_{slot.value}")
                model.add(worked == 0).only_enforce_if(not_worked)
                model.add(worked >= 1).only_enforce_if(not_worked.negated())
                penalty_terms.append(EXPLICIT_PREF_PENALTY * not_worked)

    # P3b: primary_timeslot 専任制約は P5 のハード制約でカバー済みのため削除（FIX⑦）

    # P3c: 社会保険加入アルバイト優先配置
    # 未加入PTが割り当てられたときにペナルティを与え、加入済みPTを優先する
    # ※ 社会保険加入PTは Issue #7 のハード制約で出勤時必ず8時間勤務になる。
    #   そのため P1（人件費＝実勤務時間最小化、weight=1000×時間×10×cost_scale）の下では
    #   非加入PT（5〜6時間勤務）を選ぶ方が最大 (8-5)*10000=30000 ほど目的関数上「安く」なり、
    #   従来の重み300ではこの差を覆せず優先配置が機能していなかった。
    #   コスト差を確実に上回りつつ、人員/リーダー不足ペナルティ(800000〜1000000)は
    #   上回らない値として設定する。
    SI_PREF_PENALTY = 50_000
    for emp in active_employees:
        if emp.employment_type == EmploymentType.FULL_TIME:
            continue
        if emp.has_social_insurance:
            continue  # 加入済みはペナルティなし
        for ds in date_strs:
            for slot in slots:
                for pos in positions:
                    penalty_terms.append(SI_PREF_PENALTY * assign[emp.id][ds][slot.value][pos.value])

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
                if slot == TimeSlot.BREAKFAST:
                    base_min += tiered_extra(rc.get("breakfast", 0), reserv_tiers_b)
                elif slot == TimeSlot.DINNER:
                    base_min += tiered_extra(rc.get("dinner", 0), reserv_tiers_d)

                # FIX③④: 両ポジション対応の従業員を二重計上しないよう、
                # 専任（primary_position が設定されている）のみをカウント。
                # 両対応の従業員はソルバーが柔軟に配置するためソフト誘導に任せる。
                pt_avail = 0
                for emp in active_employees:
                    if emp.employment_type == EmploymentType.FULL_TIME:
                        continue
                    # 両ポジション対応の PT は二重計上回避のためスキップ
                    if emp.primary_position is None or emp.can_work_both_positions:
                        continue
                    if emp.primary_position.value != pos.value:
                        continue
                    if slot == TimeSlot.BREAKFAST and emp.always_available_breakfast:
                        slot_ok = ds not in emp.fixed_unavailable_dates
                    elif slot == TimeSlot.DINNER and emp.always_available_dinner:
                        slot_ok = ds not in emp.fixed_unavailable_dates
                    else:
                        slot_ok = req_map.get((emp.id, ds, slot.value), False)
                    if slot_ok:
                        pt_avail += 1

                # FT も専任のみカウント（両対応 FT の二重計上回避）
                ft_avail = 0
                for emp in active_employees:
                    if emp.employment_type != EmploymentType.FULL_TIME:
                        continue
                    if off_map.get((emp.id, ds), False) or ds in emp.fixed_unavailable_dates:
                        continue
                    if slot_block_map.get((emp.id, ds)) == slot:
                        continue
                    if emp.primary_position is None or emp.can_work_both_positions:
                        continue  # 両対応 FT は二重計上回避のためスキップ
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
        _log_employee_analysis(result_assignments, active_employees, date_strs, slots, positions,
                               req_map, req_hours, shift_constraints)
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
        errs = _diagnose_infeasible(active_employees, date_strs, req_map, off_map)
        for e in errs:
            logger.error(f"  [診断] {e}")

        # フェーズ2開始を通知
        _notify_phase2(period_id or (progress_callback._period_id if progress_callback else None))

        # ベストエフォート生成: 人数・リーダー制約をソフト化して再実行
        logger.info("  [フェーズ2] ベストエフォート求解開始")
        best_assignments, best_warnings = _solve_best_effort(
            active_employees, date_strs, slots, positions,
            req_map, off_map, req_hours, config,
            slot_block_map=slot_block_map,
            shift_constraints=shift_constraints,
            reservation_counts=reservation_counts,
            reserv_tiers_b=reserv_tiers_b, reserv_tiers_d=reserv_tiers_d,
            long_breakfast_set=long_breakfast_set,
            period_id=period_id or (progress_callback._period_id if progress_callback else None),
            max_consecutive_days=max_consecutive_days,
            min_days_off=min_days_off,
        )
        _log_employee_analysis(best_assignments, active_employees, date_strs, slots, positions,
                               req_map, req_hours, shift_constraints)
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


def _log_employee_analysis(assignments, active_employees, date_strs, slots, positions,
                            req_map, req_hours, shift_constraints):
    """スタッフ別の割当状況・ペナルティ根拠・リーダー/SI診断を詳細ログ出力"""
    from collections import defaultdict
    SOCIAL_INSURANCE_MIN_HOURS = 9.0   # 実働8h + 休憩1h（duration_hours は休憩考慮なし総拘束時間）
    SI_PREF_PENALTY = 50_000
    DEFAULT_SLOT_HOURS = {TimeSlot.BREAKFAST: 5.0, TimeSlot.DINNER: 6.0}

    assigned_b = defaultdict(int)
    assigned_d = defaultdict(int)
    for a in assignments:
        if a.time_slot == TimeSlot.BREAKFAST:
            assigned_b[a.employee_id] += 1
        else:
            assigned_d[a.employee_id] += 1

    # ── リーダー要件チェック ────────────────────────────────────────────
    logger.info("  [リーダー要件チェック]")
    any_leader_issue = False
    for slot in slots:
        for pos in positions:
            c = shift_constraints.get((slot, pos), {})
            min_ldr = c.get("min_leader", 0)
            if min_ldr <= 0:
                continue
            candidates = [e for e in active_employees if e.is_leader(pos.value, slot)]
            names = ", ".join(e.name for e in candidates[:5]) or "なし"
            status = "OK" if candidates else "❌ リーダー候補ゼロ→毎日INFEASIBLE"
            logger.info(f"    {slot.value}/{pos.value}: 必要{min_ldr}名  候補{len(candidates)}名({names})  {status}")
            if not candidates:
                any_leader_issue = True
    if any_leader_issue:
        logger.warning("  ⚠️  リーダースキル設定済スタッフが1名もいません。"
                       "スタッフ編集画面でスキルレベルを「リーダー」に設定してください。")

    # ── PT スタッフ別詳細 ───────────────────────────────────────────────
    pt_emps = [e for e in active_employees if e.employment_type != EmploymentType.FULL_TIME]
    if not pt_emps:
        return

    logger.info("  [PT別割当・ペナルティ内訳]")
    hdr = f"    {'名前':<14} {'SI':2} {'リーダー能力':<18} {'希望朝':>5} {'希望夜':>5} {'8h適':>4} {'割当朝':>5} {'割当夜':>5} {'SI未加入ペナ':>10}  備考"
    logger.info(hdr)

    si_assigned = 0; si_req_8h = 0; si_emps_count = 0
    non_si_assigned = 0; non_si_emps_count = 0

    for emp in sorted(pt_emps, key=lambda e: (not e.has_social_insurance, e.name)):
        # 希望提出日数
        req_b_days = [ds for ds in date_strs if req_map.get((emp.id, ds, TimeSlot.BREAKFAST.value))]
        req_d_days = [ds for ds in date_strs if req_map.get((emp.id, ds, TimeSlot.DINNER.value))]
        # SI加入者: 8h互換パターン数（割当可能な日数）
        h8_count = 0
        h8_blocked = []
        if emp.has_social_insurance:
            for ds in date_strs:
                for slot in slots:
                    if req_map.get((emp.id, ds, slot.value)):
                        h = req_hours.get((emp.id, ds, slot.value), DEFAULT_SLOT_HOURS[slot])
                        if h >= SOCIAL_INSURANCE_MIN_HOURS:
                            h8_count += 1
                        else:
                            h8_blocked.append(f"{ds}({h}h)")
        # リーダー能力
        ldr = []
        for slot in slots:
            for pos in positions:
                if emp.is_leader(pos.value, slot):
                    ldr.append(f"{pos.value[0].upper()}/{slot.value[0].upper()}")
        ldr_str = ",".join(ldr) if ldr else "-"
        # 割当数
        ab = assigned_b[emp.id]; ad = assigned_d[emp.id]
        total_assigned = ab + ad
        # SI未加入ペナルティ額（実際に割当された分）
        si_pen = SI_PREF_PENALTY * total_assigned if not emp.has_social_insurance else 0
        omakase_b = emp.always_available_breakfast and emp.employment_type != EmploymentType.FULL_TIME
        omakase_d = emp.always_available_dinner and emp.employment_type != EmploymentType.FULL_TIME
        omakase_str = ("朝" if omakase_b else "") + ("夜" if omakase_d else "")
        si_str = "○" if emp.has_social_insurance else "×"
        h8_str = str(h8_count) if emp.has_social_insurance else "-"
        si_pen_str = f"{si_pen:,}" if si_pen else "-"
        # 備考
        notes = []
        if omakase_str:
            notes.append(f"おまかせ({omakase_str})→明示希望者優先")
        if emp.has_social_insurance:
            if not req_b_days and not req_d_days:
                notes.append("希望未提出→全日ブロック")
            elif h8_count == 0 and (req_b_days or req_d_days):
                notes.append("8h希望ゼロ→全日ブロック")
            elif h8_blocked:
                notes.append(f"8h非該当{len(h8_blocked)}日ブロック")
            si_assigned += total_assigned
            si_req_8h += h8_count
            si_emps_count += 1
        else:
            non_si_assigned += total_assigned
            non_si_emps_count += 1
        notes_str = "; ".join(notes)
        logger.info(f"    {emp.name:<14} {si_str:2} {ldr_str:<18} {len(req_b_days):>5} {len(req_d_days):>5} "
                    f"{h8_str:>4} {ab:>5} {ad:>5} {si_pen_str:>10}  {notes_str}")

    # ── SI 集計サマリー ──────────────────────────────────────────────────
    logger.info(f"  [SI割当集計]")
    logger.info(f"    社保加入PT {si_emps_count}名: 8h適合希望={si_req_8h}件  割当合計={si_assigned}回")
    logger.info(f"    社保未加入PT {non_si_emps_count}名:                   割当合計={non_si_assigned}回  "
                f"(ペナルティ計{SI_PREF_PENALTY * non_si_assigned:,})")
    if si_emps_count and non_si_emps_count:
        avg_si = si_assigned / si_emps_count
        avg_non = non_si_assigned / non_si_emps_count
        logger.info(f"    → 加入者平均{avg_si:.1f}回 / 未加入者平均{avg_non:.1f}回")
        if si_req_8h == 0 and si_emps_count > 0:
            logger.warning("    ⚠️  社保加入PT全員が8h希望を提出していません。"
                           "8時間のシフトパターンを提出するか、おまかせ+固定パターンで8hを設定してください。")


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
    slot_block_map: dict | None = None,
    shift_constraints: dict | None = None,
    reservation_counts: dict | None = None,
    reserv_tiers_b: list[tuple[int, int]] | None = None,
    reserv_tiers_d: list[tuple[int, int]] | None = None,
    long_breakfast_set: set[tuple[int, str]] | None = None,
    period_id: int | None = None,
    max_consecutive_days: int = 6,
    min_days_off: int = 5,
) -> tuple[list[ShiftAssignment], list[str]]:
    """人数・リーダー制約をソフト化してベストエフォートのシフトを生成する"""
    import time
    if config is None:
        config = SolverConfig()
    if shift_constraints is None:
        from db import repositories as repo
        shift_constraints = repo.get_shift_constraints()
    if slot_block_map is None:
        slot_block_map = {}
    if reserv_tiers_b is None:
        reserv_tiers_b = []
    if reserv_tiers_d is None:
        reserv_tiers_d = []
    if long_breakfast_set is None:
        long_breakfast_set = set()
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
                               and ds not in emp.fixed_unavailable_dates \
                               and slot_block_map.get((emp.id, ds)) != slot
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

    # 社会保険加入アルバイトは、出勤する場合は必ず実働8時間勤務になるようハード制約（Issue #7）
    # 総拘束9h以上（実働8h + 休憩1h）のパターンのみ割当可
    SOCIAL_INSURANCE_MIN_HOURS = 9.0
    for emp in active_employees:
        if emp.employment_type == EmploymentType.FULL_TIME or not emp.has_social_insurance:
            continue
        for ds in date_strs:
            for slot in slots:
                dur = req_hours.get((emp.id, ds, slot.value))
                if dur is None or dur < SOCIAL_INSURANCE_MIN_HOURS:
                    for pos in positions:
                        model.add(assign[emp.id][ds][slot.value][pos.value] == 0)

    # 日をまたいだ朝食⇄ディナーの連続勤務を禁止（休息時間確保のハード制約 / Issue #8）
    for emp in active_employees:
        for ds, ds_next in zip(date_strs, date_strs[1:]):
            worked_d_prev = sum(assign[emp.id][ds][TimeSlot.DINNER.value][p.value] for p in positions)
            worked_b_prev = sum(assign[emp.id][ds][TimeSlot.BREAKFAST.value][p.value] for p in positions)
            worked_b_next = sum(assign[emp.id][ds_next][TimeSlot.BREAKFAST.value][p.value] for p in positions)
            worked_d_next = sum(assign[emp.id][ds_next][TimeSlot.DINNER.value][p.value] for p in positions)
            model.add(worked_d_prev + worked_b_next <= 1)
            model.add(worked_b_prev + worked_d_next <= 1)

    # ── 出勤日変数 worked_day[emp][date]（朝食 or ディナーいずれかに割当があれば1） ──
    # Issue #4・#5 で共用
    worked_day: dict[int, dict[str, object]] = {}
    for emp in active_employees:
        worked_day[emp.id] = {}
        for ds in date_strs:
            wd = model.new_bool_var(f"be_wd_{emp.id}_{ds}")
            worked_b = sum(assign[emp.id][ds][TimeSlot.BREAKFAST.value][p.value] for p in positions)
            worked_d = sum(assign[emp.id][ds][TimeSlot.DINNER.value][p.value] for p in positions)
            model.add(wd == worked_b + worked_d)
            worked_day[emp.id][ds] = wd

    # 連続勤務日数を制限（7日間スライディングウィンドウ・ハード制約 / Issue #5）
    CONSECUTIVE_WINDOW = 7
    for emp in active_employees:
        for i in range(len(date_strs) - CONSECUTIVE_WINDOW + 1):
            window = date_strs[i:i + CONSECUTIVE_WINDOW]
            model.add(sum(worked_day[emp.id][d] for d in window) <= max_consecutive_days)

    # 正社員は期間内（半月=2週間）で希望休含め最低日数の休みを取得（ハード制約 / Issue #4）
    if len(date_strs) > min_days_off:
        max_work_days = len(date_strs) - min_days_off
        for emp in active_employees:
            if emp.employment_type == EmploymentType.FULL_TIME:
                model.add(sum(worked_day[emp.id][d] for d in date_strs) <= max_work_days)

    penalty_terms = []
    STAFF_PENALTY = 1_000_000   # 人数不足ペナルティ（非常に高い）
    LEADER_PENALTY = 800_000    # リーダー不足ペナルティ

    rc_map = reservation_counts or {}
    for ds in date_strs:
        rc = rc_map.get(ds, {})
        b_count = rc.get("breakfast", 0)
        d_count = rc.get("dinner", 0)
        for slot in slots:
            for pos in positions:
                constraint = shift_constraints.get((slot, pos))
                if not constraint:
                    continue
                if slot == TimeSlot.BREAKFAST:
                    extra = tiered_extra(b_count, reserv_tiers_b)
                elif slot == TimeSlot.DINNER:
                    extra = tiered_extra(d_count, reserv_tiers_d)
                else:
                    extra = 0
                min_req, max_req = effective_min_max(constraint, extra)
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
                        for emp in active_employees if emp.is_leader(pos.value, slot)
                    ]
                    lshortfall = model.new_int_var(0, min_leader, f"lsf_{ds}_{slot.value}_{pos.value}")
                    model.add(sum(leader_vars) + lshortfall >= min_leader)
                    penalty_terms.append(LEADER_PENALTY * lshortfall)

    # 朝食キッチンのロング勤務必須（ソフト化版 / Issue #6）
    LONG_BREAKFAST_PENALTY = 700_000
    for ds in date_strs:
        long_candidates = [emp for emp in active_employees if (emp.id, ds) in long_breakfast_set]
        if long_candidates:
            long_vars = [
                assign[emp.id][ds][TimeSlot.BREAKFAST.value][Position.KITCHEN.value]
                for emp in long_candidates
            ]
            lb_shortfall = model.new_int_var(0, 1, f"lbsf_{ds}")
            model.add(sum(long_vars) + lb_shortfall >= 1)
            penalty_terms.append(LONG_BREAKFAST_PENALTY * lb_shortfall)

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
    # おまかせスタッフはペナルティ免除（フェーズ1と同じ方針）
    EXPLICIT_PREF_PENALTY = max(1, int(200_000 * config.pt_pref_scale))
    for emp in active_employees:
        if emp.employment_type == EmploymentType.FULL_TIME:
            continue
        for ds in date_strs:
            for slot in slots:
                if not req_map.get((emp.id, ds, slot.value)):
                    continue
                is_omakase = (slot == TimeSlot.BREAKFAST and emp.always_available_breakfast) or \
                             (slot == TimeSlot.DINNER and emp.always_available_dinner)
                if is_omakase:
                    continue
                worked = sum(assign[emp.id][ds][slot.value][pos.value] for pos in positions)
                not_worked = model.new_bool_var(f"nw_be_{emp.id}_{ds}_{slot.value}")
                model.add(worked == 0).only_enforce_if(not_worked)
                model.add(worked >= 1).only_enforce_if(not_worked.negated())
                penalty_terms.append(EXPLICIT_PREF_PENALTY * not_worked)

    # 社会保険加入アルバイト優先（ベストエフォートフェーズでも同様。weight は本フェーズと統一）
    SI_PREF_PENALTY = 50_000
    for emp in active_employees:
        if emp.employment_type == EmploymentType.FULL_TIME or emp.has_social_insurance:
            continue
        for ds in date_strs:
            for slot in slots:
                for pos in positions:
                    penalty_terms.append(SI_PREF_PENALTY * assign[emp.id][ds][slot.value][pos.value])

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

    MAX_TIME_P2 = 45.0
    solver = cp_model.CpSolver()
    solver.parameters.max_time_in_seconds = MAX_TIME_P2
    solver.parameters.num_search_workers = 1
    solver.parameters.log_search_progress = False
    cb = Phase2ProgressCallback(period_id, max_time=MAX_TIME_P2)
    logger.info(f"  [フェーズ2 CP-SAT 求解開始] max_time={MAX_TIME_P2}s")
    status = solver.solve(model, cb)
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
    off_map: dict | None = None,
) -> list[str]:
    """実行不可能の原因を診断してエラーメッセージを返す"""
    errors = []
    from db import repositories as repo
    shift_constraints = repo.get_shift_constraints()
    if off_map is None:
        off_map = {}

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

                available = []
                for e in employees:
                    # ポジション互換チェック
                    if not (e.can_work_both_positions or e.primary_position is None
                            or e.primary_position.value == pos.value):
                        continue
                    if e.employment_type == EmploymentType.FULL_TIME:
                        # 正社員: off_map と固定休日のみで可否判断
                        if off_map.get((e.id, ds), False) or ds in e.fixed_unavailable_dates:
                            continue
                        available.append(e)
                    else:
                        # アルバイト: req_map で可否判断
                        if req_map.get((e.id, ds, slot.value), False):
                            available.append(e)

                leaders = [e for e in available if e.is_leader(pos.value, slot)]
                min_req = constraint["min"]
                min_leader = constraint.get("min_leader", 0)

                if len(available) < min_req:
                    errors.append(
                        f"❌ {label} {slot.short_label()} {pos.label()}: "
                        f"出勤可能者が{len(available)}名（必要: {min_req}名以上）"
                    )
                elif len(leaders) < min_leader:
                    lnames = ", ".join(e.name for e in leaders) or "なし"
                    errors.append(
                        f"❌ {label} {slot.short_label()} {pos.label()}: "
                        f"リーダーが{len(leaders)}名（必要: {min_leader}名以上、候補: {lnames}）"
                    )

    if not errors:
        errors.append("❌ スケジュールを生成できません。制約の組み合わせを確認してください。")
    return errors
