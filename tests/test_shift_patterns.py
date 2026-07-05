"""
単体テスト: utils/shift_patterns.py
品質特性: 機能適合性（ISO/IEC 25010 §4.2.1）
テストレベル: 単体テスト
"""
import pytest
from utils.shift_patterns import (
    ShiftPattern,
    ALL_PATTERNS,
    PATTERN_MAP,
    is_long_breakfast_pattern,
    default_pattern_from_fixed,
    BREAKFAST_START,
    BREAKFAST_END,
    DINNER_START,
    DINNER_END,
    MIN_OVERLAP_HRS,
    LONG_BREAKFAST_END_HOUR,
)


class TestShiftPatternToHour:
    def test_whole_hour(self):
        sp = ShiftPattern("t", "テスト", "06:00", "10:00")
        assert sp.start_hour() == 6.0

    def test_half_hour(self):
        sp = ShiftPattern("t", "テスト", "06:30", "11:30")
        assert sp.start_hour() == 6.5

    def test_quarter_hour(self):
        sp = ShiftPattern("t", "テスト", "05:45", "14:45")
        assert sp.start_hour() == pytest.approx(5.75)
        assert sp.end_hour() == pytest.approx(14.75)

    def test_none_returns_none(self):
        sp = ShiftPattern("t", "テスト", None, None)
        assert sp.start_hour() is None
        assert sp.end_hour() is None


class TestCoversBreakfast:
    """朝食時間帯(6:00-11:00)カバー判定"""

    def test_breakfast_standard_covers(self):
        p = PATTERN_MAP["b_std"]   # 6:30-11:30 → 重複4.5h
        assert p.covers_breakfast() is True

    def test_breakfast_short_covers(self):
        p = PATTERN_MAP["b_short"]  # 6:00-10:00 → 重複4h
        assert p.covers_breakfast() is True

    def test_dinner_only_no_cover(self):
        p = PATTERN_MAP["d_std1"]  # 17:00-22:30
        assert p.covers_breakfast() is False

    def test_double_force_both(self):
        p = PATTERN_MAP["double"]
        assert p.covers_breakfast() is True

    def test_custom_no_times_no_cover(self):
        p = PATTERN_MAP["custom"]
        assert p.covers_breakfast() is False

    def test_boundary_exactly_min_overlap(self):
        # 9:00-11:00 → 重複 exactly 2.0h → カバー
        sp = ShiftPattern("t", "テスト", "09:00", "11:00")
        assert sp.covers_breakfast() is True

    def test_below_min_overlap_no_cover(self):
        # 9:01-11:00 → 重複 1.983h < 2.0h → カバーなし
        sp = ShiftPattern("t", "テスト", "09:01", "11:00")
        assert sp.covers_breakfast() is False

    def test_dinner_full_covers_breakfast_if_start_early_enough(self):
        # 通し早番 13:00-22:30 → 朝食との重複なし
        p = PATTERN_MAP["d_full1"]
        assert p.covers_breakfast() is False


class TestCoversDinner:
    """ディナー時間帯(17:00-23:00)カバー判定"""

    def test_dinner_std_covers(self):
        p = PATTERN_MAP["d_std1"]  # 17:00-22:30 → 重複5.5h
        assert p.covers_dinner() is True

    def test_breakfast_no_cover(self):
        p = PATTERN_MAP["b_std"]
        assert p.covers_dinner() is False

    def test_double_force_both(self):
        p = PATTERN_MAP["double"]
        assert p.covers_dinner() is True

    def test_custom_no_times_no_cover(self):
        p = PATTERN_MAP["custom"]
        assert p.covers_dinner() is False

    def test_boundary_exactly_min_overlap(self):
        # 17:00-19:00 → 重複 exactly 2.0h → カバー
        sp = ShiftPattern("t", "テスト", "17:00", "19:00")
        assert sp.covers_dinner() is True

    def test_below_min_overlap_no_cover(self):
        # 17:01-19:00 → 重複 1.983h < 2.0h
        sp = ShiftPattern("t", "テスト", "17:01", "19:00")
        assert sp.covers_dinner() is False

    def test_through_shift_covers_dinner(self):
        # 通し遅番 14:00-23:00
        p = PATTERN_MAP["d_full2"]
        assert p.covers_dinner() is True


class TestDurationHours:
    """実勤務時間計算"""

    def test_double_returns_11(self):
        assert PATTERN_MAP["double"].duration_hours() == 11.0

    def test_custom_no_times_returns_zero(self):
        assert PATTERN_MAP["custom"].duration_hours() == 0.0

    def test_b_std_5hours(self):
        p = PATTERN_MAP["b_std"]  # 6:30-11:30
        assert p.duration_hours() == pytest.approx(5.0)

    def test_d_std1_5point5hours(self):
        p = PATTERN_MAP["d_std1"]  # 17:00-22:30
        assert p.duration_hours() == pytest.approx(5.5)

    def test_b_long_9hours(self):
        p = PATTERN_MAP["b_long"]  # 5:45-14:45
        assert p.duration_hours() == pytest.approx(9.0)


class TestTimeRangeLabel:
    """表示用時刻文字列"""

    def test_double(self):
        assert PATTERN_MAP["double"].time_range_label() == "6:00〜11:00 / 17:00〜23:00"

    def test_b_std(self):
        assert PATTERN_MAP["b_std"].time_range_label() == "06:30〜11:30"

    def test_custom_no_times(self):
        assert PATTERN_MAP["custom"].time_range_label() == ""


class TestAllPatternsConsistency:
    """ALL_PATTERNSの整合性チェック"""

    def test_all_ids_unique(self):
        ids = [p.id for p in ALL_PATTERNS]
        assert len(ids) == len(set(ids))

    def test_pattern_map_matches_all_patterns(self):
        for p in ALL_PATTERNS:
            assert PATTERN_MAP[p.id] is p

    def test_non_custom_patterns_have_label(self):
        for p in ALL_PATTERNS:
            assert p.label, f"{p.id} has empty label"


class TestIsLongBreakfastPattern:
    """朝食ロング勤務判定"""

    def test_b_long_is_long(self):
        # 5:45-14:45 → 朝食カバー・終了14.75 >= 14.0
        assert is_long_breakfast_pattern("b_long") is True

    def test_b_half_is_long(self):
        # 6:00-15:00 → 終了15.0 >= 14.0
        assert is_long_breakfast_pattern("b_half") is True

    def test_b_open_not_long(self):
        # 6:00-11:30 → 終了11.5 < 14.0
        assert is_long_breakfast_pattern("b_open") is False

    def test_b_std_not_long(self):
        # 6:30-11:30 → 終了11.5 < 14.0
        assert is_long_breakfast_pattern("b_std") is False

    def test_b_short_not_long(self):
        # 6:00-10:00 → 終了10.0 < 14.0
        assert is_long_breakfast_pattern("b_short") is False

    def test_dinner_not_long(self):
        assert is_long_breakfast_pattern("d_std1") is False

    def test_none_pattern_id(self):
        assert is_long_breakfast_pattern(None) is False

    def test_custom_long(self):
        assert is_long_breakfast_pattern("custom", "06:00", "15:00") is True

    def test_custom_short(self):
        assert is_long_breakfast_pattern("custom", "06:00", "11:00") is False

    def test_custom_missing_times(self):
        assert is_long_breakfast_pattern("custom") is False

    def test_custom_dinner_not_long(self):
        assert is_long_breakfast_pattern("custom", "17:00", "23:00") is False


class TestDefaultPatternFromFixed:
    """固定フラグからデフォルトパターンID変換"""

    def test_both_true_returns_double(self):
        assert default_pattern_from_fixed(True, True) == "double"

    def test_breakfast_only_returns_b_std(self):
        assert default_pattern_from_fixed(True, False) == "b_std"

    def test_dinner_only_returns_d_std1(self):
        assert default_pattern_from_fixed(False, True) == "d_std1"

    def test_both_false_returns_none(self):
        assert default_pattern_from_fixed(False, False) is None
