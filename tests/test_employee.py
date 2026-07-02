"""
単体テスト: models/employee.py
品質特性: 機能適合性（ISO/IEC 25010 §4.2.1）
テストレベル: 単体テスト
"""
import pytest
from models.employee import Employee
from utils.constants import EmploymentType, SkillLevel, PrimaryPosition, TimeSlot, Position


def _make_employee(**kwargs) -> Employee:
    defaults = dict(
        id=1,
        name="テスト太郎",
        employment_type=EmploymentType.PART_TIME,
        hall_skill_breakfast=SkillLevel.BEGINNER,
        hall_skill_dinner=SkillLevel.BEGINNER,
        kitchen_skill_breakfast=SkillLevel.BEGINNER,
        kitchen_skill_dinner=SkillLevel.BEGINNER,
    )
    defaults.update(kwargs)
    return Employee(**defaults)


class TestSkillFor:
    """ポジション×時間帯に対応するスキルを返す"""

    def test_hall_breakfast(self):
        emp = _make_employee(hall_skill_breakfast=SkillLevel.LEADER)
        assert emp.skill_for(Position.HALL, TimeSlot.BREAKFAST) == SkillLevel.LEADER

    def test_hall_dinner(self):
        emp = _make_employee(hall_skill_dinner=SkillLevel.VETERAN)
        assert emp.skill_for(Position.HALL, TimeSlot.DINNER) == SkillLevel.VETERAN

    def test_kitchen_breakfast(self):
        emp = _make_employee(kitchen_skill_breakfast=SkillLevel.GENERAL)
        assert emp.skill_for(Position.KITCHEN, TimeSlot.BREAKFAST) == SkillLevel.GENERAL

    def test_kitchen_dinner(self):
        emp = _make_employee(kitchen_skill_dinner=SkillLevel.VETERAN)
        assert emp.skill_for(Position.KITCHEN, TimeSlot.DINNER) == SkillLevel.VETERAN

    def test_string_hall_breakfast(self):
        # Position.HALL の文字列値でも動作する
        emp = _make_employee(hall_skill_breakfast=SkillLevel.LEADER)
        assert emp.skill_for("hall", "breakfast") == SkillLevel.LEADER


class TestIsSkilled:
    """ベテラン以上かどうかの判定"""

    def test_leader_is_skilled(self):
        emp = _make_employee(hall_skill_breakfast=SkillLevel.LEADER)
        assert emp.is_skilled(Position.HALL, TimeSlot.BREAKFAST) is True

    def test_veteran_is_skilled(self):
        emp = _make_employee(hall_skill_breakfast=SkillLevel.VETERAN)
        assert emp.is_skilled(Position.HALL, TimeSlot.BREAKFAST) is True

    def test_general_not_skilled(self):
        emp = _make_employee(hall_skill_breakfast=SkillLevel.GENERAL)
        assert emp.is_skilled(Position.HALL, TimeSlot.BREAKFAST) is False

    def test_beginner_not_skilled(self):
        emp = _make_employee(hall_skill_breakfast=SkillLevel.BEGINNER)
        assert emp.is_skilled(Position.HALL, TimeSlot.BREAKFAST) is False


class TestIsLeader:
    """リーダーかどうかの判定"""

    def test_leader_is_leader(self):
        emp = _make_employee(hall_skill_breakfast=SkillLevel.LEADER)
        assert emp.is_leader(Position.HALL, TimeSlot.BREAKFAST) is True

    def test_veteran_not_leader(self):
        emp = _make_employee(hall_skill_breakfast=SkillLevel.VETERAN)
        assert emp.is_leader(Position.HALL, TimeSlot.BREAKFAST) is False

    def test_general_not_leader(self):
        emp = _make_employee(hall_skill_breakfast=SkillLevel.GENERAL)
        assert emp.is_leader(Position.HALL, TimeSlot.BREAKFAST) is False

    def test_kitchen_dinner_leader(self):
        emp = _make_employee(kitchen_skill_dinner=SkillLevel.LEADER)
        assert emp.is_leader(Position.KITCHEN, TimeSlot.DINNER) is True


class TestStaffCategoryRank:
    """シフト表での表示順カテゴリ"""

    def test_full_time_rank_0(self):
        emp = _make_employee(employment_type=EmploymentType.FULL_TIME)
        assert emp.staff_category_rank() == 0

    def test_part_time_with_dedicated_position_rank_1(self):
        emp = _make_employee(
            primary_position=PrimaryPosition.HALL,
            can_work_both_positions=False,
        )
        assert emp.staff_category_rank() == 1

    def test_part_time_kitchen_dedicated_rank_1(self):
        emp = _make_employee(
            primary_position=PrimaryPosition.KITCHEN,
            can_work_both_positions=False,
        )
        assert emp.staff_category_rank() == 1

    def test_part_time_no_position_rank_2(self):
        emp = _make_employee(primary_position=None)
        assert emp.staff_category_rank() == 2

    def test_part_time_both_positions_rank_2(self):
        emp = _make_employee(
            primary_position=PrimaryPosition.HALL,
            can_work_both_positions=True,
        )
        assert emp.staff_category_rank() == 2
