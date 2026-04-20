"""DB操作の集約モジュール"""
from __future__ import annotations
from typing import Optional
from db.database import get_connection
from models.employee import Employee, FixedPattern
from models.schedule import ShiftRequest, ShiftAssignment, SchedulePeriod
from utils.constants import EmploymentType, SkillLevel, TimeSlot, Position, PrimaryPosition


# ── 従業員 ──────────────────────────────────────────────────────────────

def get_all_employees(active_only: bool = True) -> list[Employee]:
    conn = get_connection()
    rows = conn.execute(
        "SELECT * FROM employees" + (" WHERE is_active=1" if active_only else "") + " ORDER BY id"
    ).fetchall()
    employees = [_row_to_employee(row, conn) for row in rows]
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
    pt   = emp.primary_timeslot.value if emp.primary_timeslot else None
    both = int(emp.can_work_both_positions)
    if emp.id is None:
        cur = conn.execute(
            "INSERT INTO employees (name, employment_type, hall_skill, kitchen_skill, primary_position, primary_timeslot, can_work_both_positions, is_active) VALUES (?,?,?,?,?,?,?,?)",
            (emp.name, emp.employment_type.value, emp.hall_skill.value, emp.kitchen_skill.value, pp, pt, both, 1)
        )
        emp.id = cur.lastrowid
    else:
        conn.execute(
            "UPDATE employees SET name=?, employment_type=?, hall_skill=?, kitchen_skill=?, primary_position=?, primary_timeslot=?, can_work_both_positions=?, is_active=? WHERE id=?",
            (emp.name, emp.employment_type.value, emp.hall_skill.value, emp.kitchen_skill.value, pp, pt, both, int(emp.is_active), emp.id)
        )
    _save_fixed_patterns(conn, emp)
    _save_fixed_unavailable_dates(conn, emp)
    conn.commit()
    conn.close()
    return emp


def delete_employee(employee_id: int):
    conn = get_connection()
    conn.execute("UPDATE employees SET is_active=0 WHERE id=?", (employee_id,))
    conn.commit()
    conn.close()


def restore_employee(employee_id: int):
    conn = get_connection()
    conn.execute("UPDATE employees SET is_active=1 WHERE id=?", (employee_id,))
    conn.commit()
    conn.close()


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
    pt_val   = row["primary_timeslot"] if "primary_timeslot" in keys and row["primary_timeslot"] else None
    both_val = bool(row["can_work_both_positions"]) if "can_work_both_positions" in keys else False
    return Employee(
        id=row["id"],
        name=row["name"],
        employment_type=EmploymentType(row["employment_type"]),
        hall_skill=SkillLevel(row["hall_skill"]),
        kitchen_skill=SkillLevel(row["kitchen_skill"]),
        primary_position=PrimaryPosition(pp_val) if pp_val else None,
        can_work_both_positions=both_val,
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
            "INSERT OR REPLACE INTO fixed_patterns (employee_id, day_of_week, breakfast, dinner) VALUES (?,?,?,?)",
            (emp.id, p.day_of_week, int(p.breakfast), int(p.dinner))
        )


def _save_fixed_unavailable_dates(conn, emp: Employee):
    conn.execute("DELETE FROM fixed_unavailable_dates WHERE employee_id=?", (emp.id,))
    for d in emp.fixed_unavailable_dates:
        conn.execute(
            "INSERT OR IGNORE INTO fixed_unavailable_dates (employee_id, date) VALUES (?,?)",
            (emp.id, d)
        )


# ── シフト期間 ──────────────────────────────────────────────────────────

def get_all_periods() -> list[SchedulePeriod]:
    conn = get_connection()
    rows = conn.execute("SELECT * FROM schedule_periods ORDER BY start_date DESC").fetchall()
    conn.close()
    return [SchedulePeriod(id=r["id"], start_date=r["start_date"], end_date=r["end_date"], status=r["status"]) for r in rows]


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
        cur = conn.execute(
            "INSERT INTO schedule_periods (start_date, end_date, status) VALUES (?,?,?)",
            (period.start_date, period.end_date, period.status)
        )
        period.id = cur.lastrowid
    else:
        conn.execute(
            "UPDATE schedule_periods SET start_date=?, end_date=?, status=? WHERE id=?",
            (period.start_date, period.end_date, period.status, period.id)
        )
    conn.commit()
    conn.close()
    return period


def delete_period(period_id: int):
    conn = get_connection()
    conn.execute("DELETE FROM schedule_periods WHERE id=?", (period_id,))
    conn.commit()
    conn.close()


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


# ── シフト制約 ──────────────────────────────────────────────────────────

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
