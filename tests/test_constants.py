"""
単体テスト: utils/constants.py
品質特性: 機能適合性（ISO/IEC 25010 §4.2.1）
テストレベル: 単体テスト
"""
import pytest
from utils.constants import (
    EmploymentType,
    SkillLevel,
    TimeSlot,
    Position,
    PrimaryPosition,
    SHIFT_CONSTRAINTS,
)


class TestSkillLevel:
    def test_rank_order(self):
        assert SkillLevel.LEADER.rank() > SkillLevel.VETERAN.rank()
        assert SkillLevel.VETERAN.rank() > SkillLevel.GENERAL.rank()
        assert SkillLevel.GENERAL.rank() > SkillLevel.BEGINNER.rank()

    def test_leader_rank_is_3(self):
        assert SkillLevel.LEADER.rank() == 3

    def test_beginner_rank_is_0(self):
        assert SkillLevel.BEGINNER.rank() == 0

    def test_labels_are_japanese(self):
        assert SkillLevel.LEADER.label() == "リーダー"
        assert SkillLevel.VETERAN.label() == "ベテラン"
        assert SkillLevel.GENERAL.label() == "メンバー"
        assert SkillLevel.BEGINNER.label() == "ビギナー"

    def test_enum_values(self):
        assert SkillLevel.LEADER.value == "leader"
        assert SkillLevel.BEGINNER.value == "beginner"


class TestTimeSlot:
    def test_breakfast_duration(self):
        assert TimeSlot.BREAKFAST.duration_hours() == 5

    def test_dinner_duration(self):
        assert TimeSlot.DINNER.duration_hours() == 6

    def test_breakfast_hours(self):
        assert TimeSlot.BREAKFAST.start_hour() == 6
        assert TimeSlot.BREAKFAST.end_hour() == 11

    def test_dinner_hours(self):
        assert TimeSlot.DINNER.start_hour() == 17
        assert TimeSlot.DINNER.end_hour() == 23

    def test_labels(self):
        assert "朝食" in TimeSlot.BREAKFAST.label()
        assert "ディナー" in TimeSlot.DINNER.label()

    def test_short_labels(self):
        assert TimeSlot.BREAKFAST.short_label() == "朝食"
        assert TimeSlot.DINNER.short_label() == "ディナー"


class TestEmploymentType:
    def test_labels(self):
        assert EmploymentType.FULL_TIME.label() == "正社員"
        assert "アルバイト" in EmploymentType.PART_TIME.label()

    def test_values(self):
        assert EmploymentType.FULL_TIME.value == "full_time"
        assert EmploymentType.PART_TIME.value == "part_time"


class TestPosition:
    def test_labels(self):
        assert Position.HALL.label() == "ホール"
        assert Position.KITCHEN.label() == "キッチン"

    def test_values(self):
        assert Position.HALL.value == "hall"
        assert Position.KITCHEN.value == "kitchen"


class TestPrimaryPosition:
    def test_labels(self):
        assert PrimaryPosition.HALL.label() == "ホール専任"
        assert PrimaryPosition.KITCHEN.label() == "キッチン専任"

    def test_short_labels(self):
        assert PrimaryPosition.HALL.short_label() == "ホール"
        assert PrimaryPosition.KITCHEN.short_label() == "キッチン"


class TestShiftConstraints:
    """シフト制約定数の整合性チェック"""

    def test_all_four_slots_defined(self):
        assert len(SHIFT_CONSTRAINTS) == 4

    def test_min_lte_max(self):
        for key, c in SHIFT_CONSTRAINTS.items():
            assert c["min"] <= c["max"], f"{key}: min > max"

    def test_min_leader_positive(self):
        for key, c in SHIFT_CONSTRAINTS.items():
            assert c["min_leader"] >= 1, f"{key}: min_leader < 1"

    def test_dinner_kitchen_requires_2_leaders(self):
        from utils.constants import SHIFT_CONSTRAINTS, TimeSlot, Position
        c = SHIFT_CONSTRAINTS[(TimeSlot.DINNER, Position.KITCHEN)]
        assert c["min_leader"] == 2
