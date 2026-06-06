from datetime import date as _date
from flask import Blueprint, render_template
from db import repositories as repo
from utils.constants import TimeSlot, Position

bp = Blueprint("dashboard", __name__)


@bp.get("/")
def index():
    today = _date.today()
    today_str = today.isoformat()

    periods = repo.get_all_periods()
    latest = periods[0] if periods else None

    staff_count = len(repo.get_all_employees(active_only=True))

    period_info = None
    today_counts = {}
    shortage_count = 0
    request_count = 0

    if latest:
        assignments = repo.get_assignments(latest.id)
        dates = latest.date_range()

        # 今日の出勤人数（スロット別）
        for a in assignments:
            if a.date == today_str:
                key = a.time_slot.value
                today_counts[key] = today_counts.get(key, 0) + 1

        # 人員不足日数
        constraints = repo.get_shift_constraints()
        from collections import defaultdict
        count_map = defaultdict(int)
        for a in assignments:
            count_map[(a.date, a.time_slot.value, a.position.value)] += 1
        shortage_dates = set()
        for d in dates:
            ds = d.isoformat()
            for slot in TimeSlot:
                for pos in Position:
                    c = constraints.get((slot, pos), {})
                    mn = c.get("min", 0)
                    if mn > 0 and count_map[(ds, slot.value, pos.value)] < mn:
                        shortage_dates.add(ds)
        shortage_count = len(shortage_dates)

        # シフト希望提出数
        requests = repo.get_shift_requests(latest.id)
        request_count = len({r.employee_id for r in requests})

        # 残り日数（期間終了まで）
        from datetime import datetime
        end = _date.fromisoformat(latest.end_date)
        days_left = (end - today).days

        period_info = {
            "period": latest,
            "days_left": days_left,
            "shortage_count": shortage_count,
            "request_count": request_count,
            "today_breakfast": today_counts.get("breakfast", 0),
            "today_dinner": today_counts.get("dinner", 0),
            "in_range": latest.start_date <= today_str <= latest.end_date,
        }

    return render_template(
        "dashboard/index.html",
        today=today,
        staff_count=staff_count,
        period_info=period_info,
        periods=periods,
    )
