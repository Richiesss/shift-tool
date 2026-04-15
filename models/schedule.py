from dataclasses import dataclass, field
from datetime import date
from typing import Optional
from utils.constants import TimeSlot, Position


@dataclass
class ShiftRequest:
    employee_id: int
    date: str          # YYYY-MM-DD
    breakfast: bool = False
    dinner: bool = False
    note: str = ""


@dataclass
class ShiftAssignment:
    employee_id: int
    date: str          # YYYY-MM-DD
    time_slot: TimeSlot
    position: Position


@dataclass
class SchedulePeriod:
    id: Optional[int]
    start_date: str    # YYYY-MM-DD
    end_date: str      # YYYY-MM-DD
    status: str = "draft"
    assignments: list[ShiftAssignment] = field(default_factory=list)

    def date_range(self) -> list[date]:
        from datetime import timedelta
        start = date.fromisoformat(self.start_date)
        end = date.fromisoformat(self.end_date)
        result = []
        cur = start
        while cur <= end:
            result.append(cur)
            cur += timedelta(days=1)
        return result
