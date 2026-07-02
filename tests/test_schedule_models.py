"""
単体テスト: models/schedule.py (ShiftRequest, SchedulePeriod)
品質特性: 機能適合性（ISO/IEC 25010 §4.2.1）
テストレベル: 単体テスト
"""
import pytest
from datetime import date
from models.schedule import ShiftRequest, ShiftAssignment, SchedulePeriod
from utils.constants import TimeSlot, Position


class TestShiftRequestSlotCoverage:
    """breakfast / dinner プロパティ（スロットカバー判定）"""

    def test_b_std_covers_breakfast_only(self):
        req = ShiftRequest(employee_id=1, date="2025-01-01", pattern_id="b_std")
        assert req.breakfast is True
        assert req.dinner is False

    def test_d_std1_covers_dinner_only(self):
        req = ShiftRequest(employee_id=1, date="2025-01-01", pattern_id="d_std1")
        assert req.breakfast is False
        assert req.dinner is True

    def test_double_covers_both(self):
        req = ShiftRequest(employee_id=1, date="2025-01-01", pattern_id="double")
        assert req.breakfast is True
        assert req.dinner is True

    def test_no_pattern_covers_none(self):
        req = ShiftRequest(employee_id=1, date="2025-01-01", pattern_id=None)
        assert req.breakfast is False
        assert req.dinner is False

    def test_am_only_covers_breakfast(self):
        req = ShiftRequest(employee_id=1, date="2025-01-01", pattern_id="am_only")
        assert req.breakfast is True
        assert req.dinner is False

    def test_pm_only_covers_dinner(self):
        req = ShiftRequest(employee_id=1, date="2025-01-01", pattern_id="pm_only")
        assert req.breakfast is False
        assert req.dinner is True

    def test_custom_breakfast_hours(self):
        req = ShiftRequest(
            employee_id=1, date="2025-01-01",
            pattern_id="custom",
            custom_start="06:00", custom_end="11:00",
        )
        assert req.breakfast is True
        assert req.dinner is False

    def test_custom_dinner_hours(self):
        req = ShiftRequest(
            employee_id=1, date="2025-01-01",
            pattern_id="custom",
            custom_start="17:00", custom_end="23:00",
        )
        assert req.breakfast is False
        assert req.dinner is True

    def test_custom_missing_times_covers_none(self):
        req = ShiftRequest(
            employee_id=1, date="2025-01-01",
            pattern_id="custom",
        )
        assert req.breakfast is False
        assert req.dinner is False

    def test_unknown_pattern_id_covers_none(self):
        req = ShiftRequest(employee_id=1, date="2025-01-01", pattern_id="nonexistent")
        assert req.breakfast is False
        assert req.dinner is False


class TestShiftRequestHasShift:
    def test_with_pattern_has_shift(self):
        req = ShiftRequest(employee_id=1, date="2025-01-01", pattern_id="b_std")
        assert req.has_shift is True

    def test_without_pattern_no_shift(self):
        req = ShiftRequest(employee_id=1, date="2025-01-01", pattern_id=None)
        assert req.has_shift is False

    def test_empty_string_pattern_no_shift(self):
        req = ShiftRequest(employee_id=1, date="2025-01-01", pattern_id="")
        assert req.has_shift is False


class TestShiftRequestDisplayTime:
    def test_custom_displays_times(self):
        req = ShiftRequest(
            employee_id=1, date="2025-01-01",
            pattern_id="custom",
            custom_start="09:00", custom_end="15:00",
        )
        assert req.display_time() == "09:00〜15:00"

    def test_custom_missing_start(self):
        req = ShiftRequest(
            employee_id=1, date="2025-01-01",
            pattern_id="custom",
            custom_end="15:00",
        )
        assert "?" in req.display_time()

    def test_known_pattern_returns_range(self):
        req = ShiftRequest(employee_id=1, date="2025-01-01", pattern_id="b_std")
        assert req.display_time() == "06:30〜11:30"

    def test_no_pattern_returns_empty(self):
        req = ShiftRequest(employee_id=1, date="2025-01-01", pattern_id=None)
        assert req.display_time() == ""


class TestSchedulePeriodDateRange:
    """date_range() の日付リスト生成"""

    def test_single_day(self):
        period = SchedulePeriod(id=1, start_date="2025-01-01", end_date="2025-01-01")
        dates = period.date_range()
        assert len(dates) == 1
        assert dates[0] == date(2025, 1, 1)

    def test_three_days(self):
        period = SchedulePeriod(id=1, start_date="2025-01-01", end_date="2025-01-03")
        dates = period.date_range()
        assert len(dates) == 3
        assert dates[0] == date(2025, 1, 1)
        assert dates[-1] == date(2025, 1, 3)

    def test_cross_month(self):
        # 1/30〜2/3 → 5日間
        period = SchedulePeriod(id=1, start_date="2025-01-30", end_date="2025-02-03")
        dates = period.date_range()
        assert len(dates) == 5
        assert dates[0] == date(2025, 1, 30)
        assert dates[-1] == date(2025, 2, 3)

    def test_full_month_january(self):
        period = SchedulePeriod(id=1, start_date="2025-01-01", end_date="2025-01-31")
        assert len(period.date_range()) == 31

    def test_result_is_sorted_ascending(self):
        period = SchedulePeriod(id=1, start_date="2025-03-01", end_date="2025-03-07")
        dates = period.date_range()
        assert dates == sorted(dates)

    def test_returns_date_objects(self):
        period = SchedulePeriod(id=1, start_date="2025-06-01", end_date="2025-06-02")
        for d in period.date_range():
            assert isinstance(d, date)
