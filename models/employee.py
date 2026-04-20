from dataclasses import dataclass, field
from typing import Optional
from utils.constants import EmploymentType, SkillLevel, PrimaryPosition, TimeSlot


@dataclass
class FixedPattern:
    day_of_week: int  # 0=月, 6=日
    breakfast: bool = False
    dinner: bool = False


@dataclass
class Employee:
    id: Optional[int]
    name: str
    employment_type: EmploymentType
    hall_skill: SkillLevel = SkillLevel.BEGINNER
    kitchen_skill: SkillLevel = SkillLevel.BEGINNER
    primary_position: Optional[PrimaryPosition] = None   # None = 未設定（旧データ互換）
    can_work_both_positions: bool = False                 # True = 兼務可（主所属外でも対応可）
    primary_timeslot: Optional[TimeSlot] = None          # None = どちらでも可
    fixed_patterns: list[FixedPattern] = field(default_factory=list)
    fixed_unavailable_dates: list[str] = field(default_factory=list)  # YYYY-MM-DD
    is_active: bool = True

    def skill_for(self, position: str) -> SkillLevel:
        from utils.constants import Position
        if position == Position.HALL or position == "hall":
            return self.hall_skill
        return self.kitchen_skill

    def is_skilled(self, position: str) -> bool:
        """ベテラン以上かどうか"""
        return self.skill_for(position).rank() >= SkillLevel.VETERAN.rank()

    def is_leader(self, position: str) -> bool:
        """リーダーかどうか"""
        return self.skill_for(position) == SkillLevel.LEADER

    def has_fixed_pattern(self) -> bool:
        return any(p.breakfast or p.dinner for p in self.fixed_patterns)

    def get_pattern(self, day_of_week: int) -> Optional[FixedPattern]:
        for p in self.fixed_patterns:
            if p.day_of_week == day_of_week:
                return p
        return None
