from dataclasses import dataclass, field
from datetime import date
from typing import Optional
from utils.constants import TimeSlot, Position


@dataclass
class ShiftRequest:
    employee_id: int
    date: str               # YYYY-MM-DD
    pattern_id: Optional[str] = None    # ShiftPattern.id ("b_std", "d_std1", "custom" …)
    custom_start: Optional[str] = None  # "HH:MM"  ※ pattern_id=="custom" のときのみ使用
    custom_end: Optional[str] = None    # "HH:MM"
    note: str = ""

    # ── 後方互換プロパティ（ソルバー・スケジュール表示が参照） ──────────
    @property
    def breakfast(self) -> bool:
        """朝食時間帯(6:00-11:00)をカバーするか"""
        return self._slot_coverage()[0]

    @property
    def dinner(self) -> bool:
        """ディナー時間帯(17:00-23:00)をカバーするか"""
        return self._slot_coverage()[1]

    def _slot_coverage(self) -> tuple[bool, bool]:
        from utils.shift_patterns import PATTERN_MAP, ShiftPattern
        if not self.pattern_id:
            return False, False
        if self.pattern_id == "custom":
            if self.custom_start and self.custom_end:
                sp = ShiftPattern("_c", "カスタム", self.custom_start, self.custom_end)
                return sp.covers_breakfast(), sp.covers_dinner()
            return False, False
        p = PATTERN_MAP.get(self.pattern_id)
        return (p.covers_breakfast(), p.covers_dinner()) if p else (False, False)

    @property
    def has_shift(self) -> bool:
        """何らかのシフトが入力されているか"""
        return bool(self.pattern_id)

    def display_time(self) -> str:
        """表示用の時刻文字列"""
        from utils.shift_patterns import PATTERN_MAP
        if self.pattern_id == "custom":
            return f"{self.custom_start or '?'}〜{self.custom_end or '?'}"
        if self.pattern_id:
            p = PATTERN_MAP.get(self.pattern_id)
            return p.time_range_label() if p else ""
        return ""


@dataclass
class ShiftAssignment:
    employee_id: int
    date: str          # YYYY-MM-DD
    time_slot: TimeSlot
    position: Position
    is_reinforcement: bool = False  # 応援要員として追加された場合 True
    reinf_start: Optional[str] = None  # 応援要員の勤務開始時刻 "HH:MM"
    reinf_end: Optional[str] = None    # 応援要員の勤務終了時刻 "HH:MM"


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
