"""
単体テスト: utils/period_fmt.py
品質特性: 機能適合性（ISO/IEC 25010 §4.2.1）
テストレベル: 単体テスト
"""
import pytest
from utils.period_fmt import fmt


class TestFmt:
    """期間ラベルの日本語フォーマット"""

    def test_same_month(self):
        assert fmt("2025-01-01", "2025-01-31") == "2025年1月（1日〜31日）"

    def test_same_month_mid_period(self):
        assert fmt("2025-06-10", "2025-06-20") == "2025年6月（10日〜20日）"

    def test_same_day_single(self):
        assert fmt("2025-06-01", "2025-06-01") == "2025年6月（1日〜1日）"

    def test_cross_month_same_year(self):
        assert fmt("2025-01-27", "2025-02-10") == "2025年1/27〜2/10"

    def test_cross_month_start_of_month_to_next(self):
        assert fmt("2025-03-31", "2025-04-01") == "2025年3/31〜4/1"

    def test_cross_year(self):
        assert fmt("2024-12-28", "2025-01-10") == "2024/12/28〜2025/1/10"

    def test_cross_year_new_year(self):
        assert fmt("2023-12-01", "2024-01-31") == "2023/12/1〜2024/1/31"

    def test_february_to_march(self):
        assert fmt("2025-02-15", "2025-03-15") == "2025年2/15〜3/15"

    def test_returns_string(self):
        result = fmt("2025-04-01", "2025-04-30")
        assert isinstance(result, str)
