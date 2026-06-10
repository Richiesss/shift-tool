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
    hall_skill_breakfast: SkillLevel = SkillLevel.BEGINNER
    hall_skill_dinner: SkillLevel = SkillLevel.BEGINNER
    kitchen_skill_breakfast: SkillLevel = SkillLevel.BEGINNER
    kitchen_skill_dinner: SkillLevel = SkillLevel.BEGINNER
    primary_position: Optional[PrimaryPosition] = None   # None = 両方対応
    output_position: Optional[PrimaryPosition] = None   # PDF/Excel出力時の所属欄（None→primary_positionで代替）
    can_work_both_positions: bool = False                 # True = 兼務可
    can_open: bool = False                                # True = 朝食開店準備（5:45〜6:30）対応可
    can_cleanup: bool = False                             # True = 朝食片付け（10:00〜11:30）対応可
    always_available_breakfast: bool = False              # True = 朝食に希望提出不要・毎日出勤可
    always_available_dinner: bool = False                 # True = ディナーに希望提出不要・毎日出勤可
    primary_timeslot: Optional[TimeSlot] = None          # None = どちらでも可
    hourly_wage: int = 0
    has_social_insurance: bool = False
    fixed_patterns: list[FixedPattern] = field(default_factory=list)
    fixed_unavailable_dates: list[str] = field(default_factory=list)  # YYYY-MM-DD
    is_active: bool = True

    def skill_for(self, position: str, time_slot: TimeSlot) -> SkillLevel:
        from utils.constants import Position
        is_hall = position == Position.HALL or position == "hall"
        is_breakfast = time_slot == TimeSlot.BREAKFAST or time_slot == "breakfast"
        if is_hall:
            return self.hall_skill_breakfast if is_breakfast else self.hall_skill_dinner
        return self.kitchen_skill_breakfast if is_breakfast else self.kitchen_skill_dinner

    def is_skilled(self, position: str, time_slot: TimeSlot) -> bool:
        """ベテラン以上かどうか"""
        return self.skill_for(position, time_slot).rank() >= SkillLevel.VETERAN.rank()

    def is_leader(self, position: str, time_slot: TimeSlot) -> bool:
        """リーダーかどうか"""
        return self.skill_for(position, time_slot) == SkillLevel.LEADER

    def has_fixed_pattern(self) -> bool:
        return any(p.breakfast or p.dinner for p in self.fixed_patterns)

    def get_pattern(self, day_of_week: int) -> Optional[FixedPattern]:
        for p in self.fixed_patterns:
            if p.day_of_week == day_of_week:
                return p
        return None

    def staff_category_rank(self) -> int:
        """シフト表での表示順カテゴリ: 0=社員, 1=専任スタッフ, 2=ホール・キッチン兼任スタッフ"""
        if self.employment_type == EmploymentType.FULL_TIME:
            return 0
        if self.primary_position is not None and not self.can_work_both_positions:
            return 1
        return 2
