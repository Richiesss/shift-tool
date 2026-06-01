"""DB操作の集約モジュール"""
from __future__ import annotations
from typing import Optional
from db.database import get_connection, Connection
from models.employee import Employee, FixedPattern
from models.schedule import ShiftRequest, ShiftAssignment, SchedulePeriod
from utils.constants import EmploymentType, SkillLevel, TimeSlot, Position, PrimaryPosition
from cache import cache


# ── 従業員 ──────────────────────────────────────────────────────────────

@cache.memoize(timeout=180)
def get_all_employees(active_only: bool = True) -> list[Employee]:
    conn = get_connection()
    rows = conn.execute(
        "SELECT * FROM employees" + (" WHERE is_active=1" if active_only else "") + " ORDER BY display_order, id"
    ).fetchall()
    if not rows:
        conn.close()
        return []

    emp_ids = [r["id"] for r in rows]
    ph = ",".join(["?" ] * len(emp_ids))

    # fixed_patterns を一括取得（N+1解消）
    pat_rows = conn.execute(
        f"SELECT * FROM fixed_patterns WHERE employee_id IN ({ph}) ORDER BY employee_id, day_of_week",
        emp_ids
    ).fetchall()
    pat_map: dict[int, list] = {}
    for p in pat_rows:
        pat_map.setdefault(p["employee_id"], []).append(p)

    # fixed_unavailable_dates を一括取得（N+1解消）
    unavail_rows = conn.execute(
        f"SELECT employee_id, date FROM fixed_unavailable_dates WHERE employee_id IN ({ph}) ORDER BY employee_id, date",
        emp_ids
    ).fetchall()
    unavail_map: dict[int, list] = {}
    for u in unavail_rows:
        unavail_map.setdefault(u["employee_id"], []).append(u["date"])

    employees = [_row_to_employee_preloaded(row, pat_map, unavail_map) for row in rows]
    conn.close()
    return employees


def get_employee(employee_id: int) -> Optional[Employee]:
    conn = get_connection()
    row = conn.execute("SELECT * FROM employees WHERE id=?", (employee_id,)).fetchone()
    if not row:
        conn.close()
        return None
    emp = _row_to_employee(row, conn)
    conn.close()
    return emp


def save_employee(emp: Employee) -> Employee:
    conn = get_connection()
    pp   = emp.primary_position.value if emp.primary_position else None
    op   = emp.output_position.value  if emp.output_position  else None
    pt   = emp.primary_timeslot.value if emp.primary_timeslot else None
    both = int(emp.can_work_both_positions)
    opn     = int(emp.can_open)
    cln     = int(emp.can_cleanup)
    avail_b = int(emp.always_available_breakfast)
    avail_d = int(emp.always_available_dinner)
    if emp.id is None:
        # 末尾に追加されるよう MAX(display_order)+1 をセット
        row = conn.execute("SELECT COALESCE(MAX(display_order), -1) AS max_order FROM employees").fetchone()
        next_order = (row["max_order"] if row else -1) + 1
        emp.id = conn.execute_insert(
            "INSERT INTO employees (name, employment_type, hall_skill, kitchen_skill, primary_position, output_position, primary_timeslot, can_work_both_positions, can_open, can_cleanup, always_available_breakfast, always_available_dinner, is_active, display_order) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (emp.name, emp.employment_type.value, emp.hall_skill.value, emp.kitchen_skill.value, pp, op, pt, both, opn, cln, avail_b, avail_d, 1, next_order)
        )
    else:
        conn.execute(
            "UPDATE employees SET name=?, employment_type=?, hall_skill=?, kitchen_skill=?, primary_position=?, output_position=?, primary_timeslot=?, can_work_both_positions=?, can_open=?, can_cleanup=?, always_available_breakfast=?, always_available_dinner=?, is_active=? WHERE id=?",
            (emp.name, emp.employment_type.value, emp.hall_skill.value, emp.kitchen_skill.value, pp, op, pt, both, opn, cln, avail_b, avail_d, int(emp.is_active), emp.id)
        )
    _save_fixed_patterns(conn, emp)
    _save_fixed_unavailable_dates(conn, emp)
    conn.commit()
    conn.close()
    cache.delete_memoized(get_all_employees)
    return emp


def delete_employee(employee_id: int):
    conn = get_connection()
    conn.execute("UPDATE employees SET is_active=0 WHERE id=?", (employee_id,))
    conn.commit()
    conn.close()
    cache.delete_memoized(get_all_employees)


def restore_employee(employee_id: int):
    conn = get_connection()
    conn.execute("UPDATE employees SET is_active=1 WHERE id=?", (employee_id,))
    conn.commit()
    conn.close()
    cache.delete_memoized(get_all_employees)


def reorder_employees(ordered_ids: list[int]):
    """ドラッグ&ドロップ後の並び順を保存する"""
    conn = get_connection()
    for i, emp_id in enumerate(ordered_ids):
        conn.execute("UPDATE employees SET display_order=? WHERE id=?", (i, emp_id))
    conn.commit()
    conn.close()
    cache.delete_memoized(get_all_employees)


def _row_to_employee_preloaded(row, pat_map: dict, unavail_map: dict) -> Employee:
    eid = row["id"]
    keys = row.keys()
    pp_val   = row["primary_position"]   if "primary_position"   in keys and row["primary_position"] else None
    op_val   = row["output_position"]    if "output_position"    in keys and row["output_position"]  else None
    pt_val   = row["primary_timeslot"]   if "primary_timeslot"   in keys and row["primary_timeslot"] else None
    return Employee(
        id=eid, name=row["name"],
        employment_type=EmploymentType(row["employment_type"]),
        hall_skill=SkillLevel(row["hall_skill"]),
        kitchen_skill=SkillLevel(row["kitchen_skill"]),
        primary_position=PrimaryPosition(pp_val) if pp_val else None,
        output_position=PrimaryPosition(op_val) if op_val else None,
        primary_timeslot=TimeSlot(pt_val) if pt_val else None,
        can_work_both_positions=bool(row["can_work_both_positions"]) if "can_work_both_positions" in keys else False,
        can_open=bool(row["can_open"]) if "can_open" in keys else False,
        can_cleanup=bool(row["can_cleanup"]) if "can_cleanup" in keys else False,
        always_available_breakfast=bool(row["always_available_breakfast"]) if "always_available_breakfast" in keys else False,
        always_available_dinner=bool(row["always_available_dinner"]) if "always_available_dinner" in keys else False,
        is_active=bool(row["is_active"]),
        fixed_patterns=[
            FixedPattern(p["day_of_week"], bool(p["breakfast"]), bool(p["dinner"]))
            for p in pat_map.get(eid, [])
        ],
        fixed_unavailable_dates=unavail_map.get(eid, []),
    )


def _row_to_employee(row, conn) -> Employee:
    patterns = conn.execute(
        "SELECT * FROM fixed_patterns WHERE employee_id=? ORDER BY day_of_week",
        (row["id"],)
    ).fetchall()
    unavail = conn.execute(
        "SELECT date FROM fixed_unavailable_dates WHERE employee_id=? ORDER BY date",
        (row["id"],)
    ).fetchall()
    keys = row.keys()
    pp_val   = row["primary_position"] if "primary_position" in keys and row["primary_position"] else None
    op_val   = row["output_position"]  if "output_position"  in keys and row["output_position"]  else None
    pt_val   = row["primary_timeslot"] if "primary_timeslot" in keys and row["primary_timeslot"] else None
    both_val = bool(row["can_work_both_positions"]) if "can_work_both_positions" in keys else False
    open_val    = bool(row["can_open"])    if "can_open"    in keys else False
    cleanup_val = bool(row["can_cleanup"]) if "can_cleanup" in keys else False
    avail_b_val = bool(row["always_available_breakfast"]) if "always_available_breakfast" in keys else False
    avail_d_val = bool(row["always_available_dinner"])    if "always_available_dinner"    in keys else False
    return Employee(
        id=row["id"],
        name=row["name"],
        employment_type=EmploymentType(row["employment_type"]),
        hall_skill=SkillLevel(row["hall_skill"]),
        kitchen_skill=SkillLevel(row["kitchen_skill"]),
        primary_position=PrimaryPosition(pp_val) if pp_val else None,
        output_position=PrimaryPosition(op_val) if op_val else None,
        can_work_both_positions=both_val,
        can_open=open_val,
        can_cleanup=cleanup_val,
        always_available_breakfast=avail_b_val,
        always_available_dinner=avail_d_val,
        primary_timeslot=TimeSlot(pt_val) if pt_val else None,
        is_active=bool(row["is_active"]),
        fixed_patterns=[
            FixedPattern(p["day_of_week"], bool(p["breakfast"]), bool(p["dinner"]))
            for p in patterns
        ],
        fixed_unavailable_dates=[u["date"] for u in unavail],
    )


def _save_fixed_patterns(conn, emp: Employee):
    conn.execute("DELETE FROM fixed_patterns WHERE employee_id=?", (emp.id,))
    for p in emp.fixed_patterns:
        conn.execute(
            "INSERT INTO fixed_patterns (employee_id, day_of_week, breakfast, dinner) VALUES (?,?,?,?)",
            (emp.id, p.day_of_week, int(p.breakfast), int(p.dinner))
        )


def _save_fixed_unavailable_dates(conn, emp: Employee):
    conn.execute("DELETE FROM fixed_unavailable_dates WHERE employee_id=?", (emp.id,))
    for d in emp.fixed_unavailable_dates:
        conn.execute(
            "INSERT INTO fixed_unavailable_dates (employee_id, date) VALUES (?,?)",
            (emp.id, d)
        )


# ── シフト期間 ──────────────────────────────────────────────────────────

def update_period_gen_status(period_id: int, status: str, message: str = ""):
    """app_context / キャッシュ不要な低レベル DB 更新（コールバックスレッドから安全に呼べる）"""
    from db.database import Connection
    conn = Connection()
    # 生成完了・失敗時はともに needs_regen をリセット（failedは新しいシフトがない）
    if status in ("done", "failed"):
        conn.execute(
            "UPDATE schedule_periods SET gen_status=?, gen_message=?, needs_regen=0 WHERE id=?",
            (status, message, period_id)
        )
    else:
        conn.execute(
            "UPDATE schedule_periods SET gen_status=?, gen_message=? WHERE id=?",
            (status, message, period_id)
        )
    conn.commit()
    conn.close()
    try:
        cache.delete_memoized(get_period, period_id)
        cache.delete_memoized(get_all_periods)
    except Exception:
        pass


def set_period_needs_regen(period_id: int, value: bool):
    conn = get_connection()
    conn.execute("UPDATE schedule_periods SET needs_regen=? WHERE id=?", (int(value), period_id))
    conn.commit()
    conn.close()


def get_period_gen_status(period_id: int) -> dict:
    conn = get_connection()
    row = conn.execute(
        "SELECT gen_status, gen_message, needs_regen FROM schedule_periods WHERE id=?", (period_id,)
    ).fetchone()
    conn.close()
    if not row:
        return {"status": "unknown", "message": "", "needs_regen": False}
    keys = row.keys()
    return {
        "status":      row["gen_status"]   if "gen_status"   in keys else "idle",
        "message":     row["gen_message"]  if "gen_message"  in keys else "",
        "needs_regen": bool(row["needs_regen"]) if "needs_regen" in keys else False,
    }

@cache.memoize(timeout=180)
def get_all_periods() -> list[SchedulePeriod]:
    conn = get_connection()
    rows = conn.execute("SELECT * FROM schedule_periods ORDER BY start_date DESC").fetchall()
    conn.close()
    return [SchedulePeriod(id=r["id"], start_date=r["start_date"], end_date=r["end_date"], status=r["status"]) for r in rows]


@cache.memoize(timeout=180)
def get_period(period_id: int) -> Optional[SchedulePeriod]:
    conn = get_connection()
    row = conn.execute("SELECT * FROM schedule_periods WHERE id=?", (period_id,)).fetchone()
    conn.close()
    if not row:
        return None
    return SchedulePeriod(id=row["id"], start_date=row["start_date"], end_date=row["end_date"], status=row["status"])


def save_period(period: SchedulePeriod) -> SchedulePeriod:
    conn = get_connection()
    if period.id is None:
        period.id = conn.execute_insert(
            "INSERT INTO schedule_periods (start_date, end_date, status) VALUES (?,?,?)",
            (period.start_date, period.end_date, period.status)
        )
    else:
        conn.execute(
            "UPDATE schedule_periods SET start_date=?, end_date=?, status=? WHERE id=?",
            (period.start_date, period.end_date, period.status, period.id)
        )
    conn.commit()
    conn.close()
    cache.delete_memoized(get_all_periods)
    cache.delete_memoized(get_period, period.id)
    return period


def delete_period(period_id: int):
    conn = get_connection()
    conn.execute("DELETE FROM schedule_periods WHERE id=?", (period_id,))
    conn.commit()
    conn.close()
    cache.delete_memoized(get_all_periods)
    cache.delete_memoized(get_period, period_id)


# ── 希望シフト ──────────────────────────────────────────────────────────

def get_shift_requests(period_id: int) -> list[ShiftRequest]:
    conn = get_connection()
    rows = conn.execute(
        "SELECT * FROM shift_requests WHERE period_id=? ORDER BY employee_id, date",
        (period_id,)
    ).fetchall()
    conn.close()
    result = []
    for r in rows:
        keys = r.keys()
        # pattern_id カラムが存在する場合は新形式、なければ旧breakfast/dinnerから推定
        if "pattern_id" in keys and r["pattern_id"]:
            result.append(ShiftRequest(
                employee_id=r["employee_id"],
                date=r["date"],
                pattern_id=r["pattern_id"],
                custom_start=r["custom_start"] if "custom_start" in keys else None,
                custom_end=r["custom_end"] if "custom_end" in keys else None,
                note=r["note"] or "",
            ))
        else:
            # 旧データ（breakfast/dinner boolean）からパターンを推定
            from utils.shift_patterns import default_pattern_from_fixed
            b = bool(r["breakfast"]) if "breakfast" in keys else False
            d = bool(r["dinner"]) if "dinner" in keys else False
            result.append(ShiftRequest(
                employee_id=r["employee_id"],
                date=r["date"],
                pattern_id=default_pattern_from_fixed(b, d),
                note=r["note"] or "",
            ))
    return result


def save_shift_requests(period_id: int, requests: list[ShiftRequest]):
    conn = get_connection()
    for req in requests:
        conn.execute(
            """INSERT INTO shift_requests
                   (period_id, employee_id, date, breakfast, dinner, pattern_id, custom_start, custom_end, note)
               VALUES (?,?,?,?,?,?,?,?,?)
               ON CONFLICT(period_id, employee_id, date)
               DO UPDATE SET
                   breakfast=excluded.breakfast,
                   dinner=excluded.dinner,
                   pattern_id=excluded.pattern_id,
                   custom_start=excluded.custom_start,
                   custom_end=excluded.custom_end,
                   note=excluded.note""",
            (period_id, req.employee_id, req.date,
             int(req.breakfast), int(req.dinner),
             req.pattern_id, req.custom_start, req.custom_end,
             req.note)
        )
    conn.commit()
    conn.close()


# ── シフトアサイン ──────────────────────────────────────────────────────

def get_assignments(period_id: int) -> list[ShiftAssignment]:
    conn = get_connection()
    rows = conn.execute(
        "SELECT * FROM shift_assignments WHERE period_id=? ORDER BY date, time_slot, employee_id",
        (period_id,)
    ).fetchall()
    conn.close()
    result = []
    for r in rows:
        keys = r.keys()
        is_r = bool(r["is_reinforcement"]) if "is_reinforcement" in keys else False
        result.append(ShiftAssignment(
            employee_id=r["employee_id"], date=r["date"],
            time_slot=TimeSlot(r["time_slot"]), position=Position(r["position"]),
            is_reinforcement=is_r,
            reinf_start=r["reinf_start"] if "reinf_start" in keys else None,
            reinf_end=r["reinf_end"]   if "reinf_end"   in keys else None,
        ))
    return result


def save_assignments(period_id: int, assignments: list[ShiftAssignment]):
    conn = get_connection()
    conn.execute("DELETE FROM shift_assignments WHERE period_id=?", (period_id,))
    for a in assignments:
        conn.execute(
            "INSERT INTO shift_assignments "
            "(period_id, employee_id, date, time_slot, position, is_reinforcement, reinf_start, reinf_end) "
            "VALUES (?,?,?,?,?,?,?,?)",
            (period_id, a.employee_id, a.date, a.time_slot.value,
             a.position.value, int(a.is_reinforcement), a.reinf_start, a.reinf_end)
        )
    conn.commit()
    conn.close()


def add_assignment(period_id: int, assignment: ShiftAssignment):
    conn = get_connection()
    conn.execute(
        """INSERT INTO shift_assignments
               (period_id, employee_id, date, time_slot, position, is_reinforcement, reinf_start, reinf_end)
           VALUES (?,?,?,?,?,?,?,?)
           ON CONFLICT(period_id, employee_id, date, time_slot) DO UPDATE SET
               position=excluded.position,
               is_reinforcement=excluded.is_reinforcement,
               reinf_start=excluded.reinf_start,
               reinf_end=excluded.reinf_end""",
        (period_id, assignment.employee_id, assignment.date,
         assignment.time_slot.value, assignment.position.value,
         int(assignment.is_reinforcement), assignment.reinf_start, assignment.reinf_end)
    )
    conn.commit()
    conn.close()


def remove_assignment(period_id: int, employee_id: int, date: str, time_slot: TimeSlot):
    conn = get_connection()
    conn.execute(
        "DELETE FROM shift_assignments WHERE period_id=? AND employee_id=? AND date=? AND time_slot=?",
        (period_id, employee_id, date, time_slot.value)
    )
    conn.commit()
    conn.close()


# ── 備考 ────────────────────────────────────────────────────────────────

# ── 予約客数 ─────────────────────────────────────────────────────────────

def get_reservation_counts(period_id: int) -> dict[str, dict[str, int]]:
    """期間の予約客数を {date_str: {"breakfast": int, "dinner": int}} で返す"""
    conn = get_connection()
    rows = conn.execute(
        "SELECT date, breakfast, dinner FROM reservation_counts WHERE period_id=? ORDER BY date",
        (period_id,)
    ).fetchall()
    conn.close()
    return {r["date"]: {"breakfast": r["breakfast"], "dinner": r["dinner"]} for r in rows}


def save_reservation_count(period_id: int, date_str: str, breakfast: int, dinner: int):
    """予約客数を保存"""
    conn = get_connection()
    conn.execute(
        """INSERT INTO reservation_counts (period_id, date, breakfast, dinner) VALUES (?,?,?,?)
           ON CONFLICT(period_id, date) DO UPDATE SET
               breakfast=excluded.breakfast, dinner=excluded.dinner""",
        (period_id, date_str, breakfast, dinner)
    )
    conn.commit()
    conn.close()


# ── アプリ設定 ────────────────────────────────────────────────────────────

@cache.memoize(timeout=300)
def get_app_setting(key: str, default: str = "") -> str:
    conn = get_connection()
    row = conn.execute("SELECT value FROM app_settings WHERE key=?", (key,)).fetchone()
    conn.close()
    return row["value"] if row else default


def save_app_setting(key: str, value: str):
    conn = get_connection()
    conn.execute(
        "INSERT INTO app_settings (key, value) VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (key, value)
    )
    conn.commit()
    conn.close()
    cache.delete_memoized(get_app_setting, key)
    cache.delete_memoized(get_all_app_settings)  # get_all_app_settings も無効化


@cache.memoize(timeout=300)
def get_all_app_settings() -> dict[str, str]:
    conn = get_connection()
    rows = conn.execute("SELECT key, value FROM app_settings").fetchall()
    conn.close()
    return {r["key"]: r["value"] for r in rows}


def save_all_app_settings(settings: dict[str, str]):
    conn = get_connection()
    for key, value in settings.items():
        conn.execute(
            "INSERT INTO app_settings (key, value) VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, value)
        )
    conn.commit()
    conn.close()
    cache.delete_memoized(get_all_app_settings)
    for key in settings:
        cache.delete_memoized(get_app_setting, key)


def get_breakfast_band_constraints() -> dict[tuple[str, str], dict]:
    """
    朝食バンド制約を {(band, position): {"min": int, "max": int, "min_leader": int}} で返す。
    band: 'open' | 'cleanup'
    """
    conn = get_connection()
    rows = conn.execute("SELECT * FROM breakfast_band_constraints").fetchall()
    conn.close()
    return {
        (r["band"], r["position"]): {
            "min": r["min_staff"], "max": r["max_staff"], "min_leader": r["min_leader"]
        }
        for r in rows
    }


def save_breakfast_band_constraints(constraints: dict[tuple[str, str], dict]):
    conn = get_connection()
    for (band, pos), vals in constraints.items():
        conn.execute(
            """INSERT INTO breakfast_band_constraints (band, position, min_staff, max_staff, min_leader)
               VALUES (?,?,?,?,?)
               ON CONFLICT(band, position) DO UPDATE SET
                   min_staff=excluded.min_staff,
                   max_staff=excluded.max_staff,
                   min_leader=excluded.min_leader""",
            (band, pos, vals["min"], vals["max"], vals["min_leader"])
        )
    conn.commit()
    conn.close()


def get_employee_pattern_history(emp_id: int) -> list[tuple[str, int]]:
    """従業員の過去の希望パターン頻度を [(pattern_id, count), ...] で返す（降順）"""
    conn = get_connection()
    rows = conn.execute(
        """SELECT pattern_id, COUNT(*) as cnt FROM shift_requests
           WHERE employee_id=? AND pattern_id IS NOT NULL AND pattern_id != ''
             AND pattern_id != 'custom'
           GROUP BY pattern_id ORDER BY cnt DESC LIMIT 5""",
        (emp_id,)
    ).fetchall()
    conn.close()
    return [(r["pattern_id"], r["cnt"]) for r in rows]


def get_schedule_notes(period_id: int) -> dict[str, str]:
    """期間の備考を {date_str: note} で返す"""
    conn = get_connection()
    rows = conn.execute(
        "SELECT date, note FROM schedule_notes WHERE period_id=? ORDER BY date",
        (period_id,)
    ).fetchall()
    conn.close()
    return {r["date"]: r["note"] for r in rows if r["note"]}


def save_schedule_note(period_id: int, date_str: str, note: str):
    """備考を保存。空文字の場合は削除"""
    conn = get_connection()
    if note.strip():
        conn.execute(
            """INSERT INTO schedule_notes (period_id, date, note) VALUES (?,?,?)
               ON CONFLICT(period_id, date) DO UPDATE SET note=excluded.note""",
            (period_id, date_str, note.strip())
        )
    else:
        conn.execute(
            "DELETE FROM schedule_notes WHERE period_id=? AND date=?",
            (period_id, date_str)
        )
    conn.commit()
    conn.close()


# ── シフト制約 ──────────────────────────────────────────────────────────

@cache.memoize(timeout=300)
def get_shift_constraints() -> dict:
    """
    DB から制約を読み込んで {(TimeSlot, Position): {"min": int, "max": int, "min_leader": int}} を返す。
    テーブルが空の場合は定数フォールバック。
    """
    from utils.constants import SHIFT_CONSTRAINTS as _FALLBACK
    conn = get_connection()
    rows = conn.execute("SELECT * FROM shift_constraints").fetchall()
    conn.close()
    if not rows:
        return _FALLBACK
    result = {}
    for r in rows:
        slot = TimeSlot(r["slot"])
        pos  = Position(r["position"])
        result[(slot, pos)] = {
            "min":        r["min_staff"],
            "max":        r["max_staff"],
            "min_leader": r["min_leader"],
        }
    return result


def save_shift_constraints(constraints: dict):
    """
    {(TimeSlot, Position): {"min": int, "max": int, "min_leader": int}} を DB に保存。
    """
    conn = get_connection()
    for (slot, pos), vals in constraints.items():
        conn.execute(
            """INSERT INTO shift_constraints (slot, position, min_staff, max_staff, min_leader)
               VALUES (?,?,?,?,?)
               ON CONFLICT(slot, position) DO UPDATE SET
                   min_staff=excluded.min_staff,
                   max_staff=excluded.max_staff,
                   min_leader=excluded.min_leader""",
            (slot.value, pos.value, vals["min"], vals["max"], vals["min_leader"])
        )
    conn.commit()
    conn.close()
    cache.delete_memoized(get_shift_constraints)
