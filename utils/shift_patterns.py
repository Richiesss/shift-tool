"""シフトパターン定義

各従業員が選択できるシフトパターン（開始〜終了時刻）を定義する。
CP-SATソルバーは各パターンが朝食(6:00-11:00)/ディナー(17:00-23:00)の
どちらをカバーするかを判定して制約に使用する。
"""
from __future__ import annotations
from dataclasses import dataclass
from typing import Optional

# 営業時間帯の境界
BREAKFAST_START = 6.0    # 6:00
BREAKFAST_END   = 11.0   # 11:00
DINNER_START    = 17.0   # 17:00
DINNER_END      = 23.0   # 23:00
MIN_OVERLAP_HRS = 2.0    # この時間以上重なれば「担当」とみなす


@dataclass(frozen=True)
class ShiftPattern:
    id: str
    label: str
    start: Optional[str]     # "HH:MM"。ダブル/カスタムはNone
    end: Optional[str]       # "HH:MM"
    force_both: bool = False  # Trueなら朝食・ディナー両方カバー（ダブルシフト）

    # ── 内部計算 ──────────────────────────────────────────────────────
    def _to_hour(self, t: str) -> float:
        h, m = map(int, t.split(":"))
        return h + m / 60.0

    def start_hour(self) -> Optional[float]:
        return self._to_hour(self.start) if self.start else None

    def end_hour(self) -> Optional[float]:
        return self._to_hour(self.end) if self.end else None

    # ── スロットカバー判定 ─────────────────────────────────────────────
    def covers_breakfast(self) -> bool:
        """朝食時間帯(6:00-11:00)と2時間以上重なるか"""
        if self.force_both:
            return True
        if not self.start or not self.end:
            return False
        overlap = max(0.0,
                      min(self.end_hour(), BREAKFAST_END) - max(self.start_hour(), BREAKFAST_START))
        return overlap >= MIN_OVERLAP_HRS

    def covers_dinner(self) -> bool:
        """ディナー時間帯(17:00-23:00)と2時間以上重なるか"""
        if self.force_both:
            return True
        if not self.start or not self.end:
            return False
        overlap = max(0.0,
                      min(self.end_hour(), DINNER_END) - max(self.start_hour(), DINNER_START))
        return overlap >= MIN_OVERLAP_HRS

    def duration_hours(self) -> float:
        """実勤務時間（休憩考慮なし）"""
        if self.force_both:
            return 11.0  # 5h + 6h
        if not self.start or not self.end:
            return 0.0
        return max(0.0, self.end_hour() - self.start_hour())

    def time_range_label(self) -> str:
        """表示用の時刻文字列"""
        if self.force_both:
            return "6:00〜11:00 / 17:00〜23:00"
        if self.start and self.end:
            return f"{self.start}〜{self.end}"
        return ""


# ── パターン一覧 ────────────────────────────────────────────────────────
ALL_PATTERNS: list[ShiftPattern] = [
    # 朝食系
    ShiftPattern("b_short", "朝食短番 (6:00-10:00)",     "06:00", "10:00"),
    ShiftPattern("b_std",   "朝食 (6:30-11:30)",          "06:30", "11:30"),
    ShiftPattern("b_long",  "朝食ロング (5:45-14:45)",    "05:45", "14:45"),
    ShiftPattern("b_half",  "午前半日 (6:30-15:30)",      "06:30", "15:30"),
    # 中番（どちらの時間帯もカバーしない）
    ShiftPattern("mid",     "中番 (10:00-15:00)",          "10:00", "15:00"),
    # 通し（ディナーカバー）
    ShiftPattern("d_full1", "通し早番 (13:00-22:30)",     "13:00", "22:30"),
    ShiftPattern("d_full2", "通し遅番 (14:00-23:00)",     "14:00", "23:00"),
    # ディナー系
    ShiftPattern("d_std1",  "ディナー (17:00-22:30)",     "17:00", "22:30"),
    ShiftPattern("d_std2",  "ディナー (17:30-23:00)",     "17:30", "23:00"),
    ShiftPattern("d_std3",  "ディナー (18:00-23:00)",     "18:00", "23:00"),
    ShiftPattern("d_s1",    "ディナー前半 (17:00-21:00)", "17:00", "21:00"),
    ShiftPattern("d_s2",    "ディナー前半 (17:30-21:30)", "17:30", "21:30"),
    ShiftPattern("d_s3",    "ディナー短番 (18:00-22:00)", "18:00", "22:00"),
    # ダブル（朝食＋ディナー）
    ShiftPattern("double",  "ダブル (朝食+ディナー)",      None, None, force_both=True),
    # カスタム（時刻を手入力）
    ShiftPattern("custom",  "カスタム入力...",              None, None),
]

PATTERN_MAP: dict[str, ShiftPattern] = {p.id: p for p in ALL_PATTERNS}


def default_pattern_from_fixed(breakfast: bool, dinner: bool) -> Optional[str]:
    """固定パターン(breakfast/dinner boolean)から既定のパターンIDを返す"""
    if breakfast and dinner:
        return "double"
    if breakfast:
        return "b_std"
    if dinner:
        return "d_std1"
    return None
