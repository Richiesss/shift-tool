#!/usr/bin/env python3
"""
テストデータ生成スクリプト — 3月前半シフト (3/1〜3/15)

実際の確定シフト表をDBに登録し、確定前の希望シフト（実際のシフトに
近いが一部異なるバリエーション）も合わせて投入します。

使い方:
  cd /home/shift-tool
  python scripts/seed_test_data.py           # データを追加
  python scripts/seed_test_data.py --reset   # 全データ削除後に追加
"""
from __future__ import annotations
import sys, os, random, argparse

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from db.database import initialize_db, get_connection
from db import repositories as repo
from models.employee import Employee
from models.schedule import SchedulePeriod, ShiftRequest, ShiftAssignment
from utils.constants import EmploymentType, SkillLevel, TimeSlot, Position
from utils.shift_patterns import ALL_PATTERNS, PATTERN_MAP, ShiftPattern

random.seed(42)  # 再現性のある乱数

# ═══════════════════════════════════════════════════════════════════════
# 定数
# ═══════════════════════════════════════════════════════════════════════
PERIOD_START = "2024-03-01"
PERIOD_END   = "2024-03-15"

# スロット判定の閾値
BREAKFAST_START, BREAKFAST_END = 6.0, 11.0
DINNER_START,    DINNER_END    = 17.0, 23.0
MIN_OVERLAP_H = 2.0

# ═══════════════════════════════════════════════════════════════════════
# 従業員マスター
# (name, employment_type, hall_skill, kitchen_skill, default_position)
# default_position: 主に担当するポジション ('hall' / 'kitchen')
# ═══════════════════════════════════════════════════════════════════════
EMPLOYEE_MASTER = [
    # ── 正社員（ホール系・管理職） ─────────────────────────────────────
    ("池山",        "full_time",  "leader",  "general",  "hall"),
    ("安本",        "full_time",  "leader",  "general",  "hall"),
    ("﨑本",        "full_time",  "leader",  "general",  "hall"),
    # ── 正社員（キッチン系・管理職） ───────────────────────────────────
    ("岡田",        "full_time",  "general", "leader",   "kitchen"),
    ("鎌田",        "full_time",  "general", "leader",   "kitchen"),
    ("タオリ",      "full_time",  "general", "veteran",  "kitchen"),
    # ── アルバイト（朝食ホール系） ─────────────────────────────────────
    ("林",          "part_time",  "veteran", "beginner", "hall"),
    ("ミツイ",      "part_time",  "veteran", "beginner", "hall"),
    ("細田",        "part_time",  "general", "general",  "hall"),
    ("アッスミタ",  "part_time",  "general", "beginner", "hall"),
    ("マニス",      "part_time",  "general", "beginner", "hall"),
    ("レジーナ",    "part_time",  "general", "beginner", "hall"),
    ("ビルシャナ",  "part_time",  "general", "beginner", "hall"),
    ("藤内",        "part_time",  "general", "beginner", "hall"),
    ("上田",        "part_time",  "general", "beginner", "hall"),
    ("津波古",      "part_time",  "general", "beginner", "hall"),
    ("カテリナ",    "part_time",  "general", "beginner", "hall"),
    ("キム",        "part_time",  "general", "beginner", "hall"),
    ("齊藤",        "part_time",  "general", "beginner", "hall"),
    ("藤畑",        "part_time",  "beginner","beginner", "hall"),
    # ── アルバイト（朝食キッチン系） ───────────────────────────────────
    ("田中",        "part_time",  "beginner","general",  "kitchen"),
    ("島野",        "part_time",  "beginner","veteran",  "kitchen"),  # 朝食キッチン最多担当
    ("チャー",      "part_time",  "beginner","veteran",  "kitchen"),  # 朝食キッチン常連
    ("坂田",        "part_time",  "general", "veteran",  "kitchen"),  # 朝食キッチン常連
    ("北",          "part_time",  "beginner","beginner", "kitchen"),
    # ── アルバイト（ディナーホール系） ─────────────────────────────────
    ("南愛菜",      "part_time",  "general", "beginner", "hall"),
    ("ドリアン",    "part_time",  "general", "beginner", "hall"),
    ("中家",        "part_time",  "general", "beginner", "hall"),
    ("光久",        "part_time",  "general", "beginner", "hall"),
    ("南志穂",      "part_time",  "general", "beginner", "hall"),
    ("岡本",        "part_time",  "general", "general",  "hall"),
    ("廣田",        "part_time",  "beginner","beginner", "hall"),
    ("リット",      "part_time",  "general", "beginner", "hall"),
    ("鈴木",        "part_time",  "general", "beginner", "hall"),
    # ── アルバイト（ディナーキッチン系） ───────────────────────────────
    ("名古",        "part_time",  "beginner","general",  "kitchen"),
    ("坂本",        "part_time",  "beginner","general",  "kitchen"),
    # ── その他 ────────────────────────────────────────────────────────
    ("井上",        "part_time",  "beginner","beginner", "hall"),
    ("アリャー",    "part_time",  "beginner","beginner", "hall"),
]

# ═══════════════════════════════════════════════════════════════════════
# 確定シフトデータ (CSVから抽出)
# 形式: {name: {date: (start_decimal, end_decimal, note)}}
#   - start/end が None の場合は有給か休み
#   - note == "有給" → 有給休暇
# ═══════════════════════════════════════════════════════════════════════
CONFIRMED_SHIFTS: dict[str, dict[str, tuple]] = {
    "池山": {
        "2024-03-01": (13.0, 22.5, ""),
        "2024-03-03": (13.0, 22.5, ""),
        "2024-03-04": (13.0, 22.5, ""),
        "2024-03-06": (13.0, 22.5, ""),
        "2024-03-08": (13.0, 22.5, ""),
        "2024-03-09": (None, None, "有給"),
        "2024-03-10": (13.0, 22.5, ""),
        "2024-03-11": (13.0, 22.5, ""),
        "2024-03-13": (13.0, 22.5, ""),
        "2024-03-14": (13.0, 22.5, ""),
    },
    "安本": {
        "2024-03-02": (13.0, 22.5, ""),
        "2024-03-03": (13.0, 22.5, ""),
        "2024-03-05": (13.0, 22.5, ""),
        "2024-03-06": (13.0, 22.5, ""),
        "2024-03-08": (13.0, 22.5, ""),
        "2024-03-09": (13.0, 22.5, ""),
        "2024-03-11": (13.0, 22.5, ""),
        "2024-03-12": (13.0, 22.5, ""),
        "2024-03-13": (13.0, 22.5, ""),
        "2024-03-14": (None, None, "有給"),
    },
    "﨑本": {
        "2024-03-01": (13.0, 22.5, ""),
        "2024-03-02": (13.0, 22.5, ""),
        "2024-03-03": (13.0, 22.5, ""),
        "2024-03-04": (13.0, 22.5, ""),
        "2024-03-06": (13.0, 22.5, ""),
        "2024-03-07": (13.0, 22.5, ""),
        "2024-03-08": (13.0, 22.5, ""),
        "2024-03-10": (13.0, 22.5, ""),
        "2024-03-11": (13.0, 22.5, ""),
        "2024-03-14": (13.0, 22.5, ""),
        "2024-03-15": (13.0, 22.5, ""),
    },
    "岡田": {
        "2024-03-02": (14.0, 23.0, ""),
        "2024-03-03": (10.0, 23.0, "休憩4h"),
        "2024-03-04": (14.0, 23.0, ""),
        "2024-03-06": (14.0, 23.0, ""),
        "2024-03-07": (10.0, 19.0, ""),
        "2024-03-08": (14.0, 23.0, ""),
        "2024-03-10": (14.0, 23.0, ""),
        "2024-03-11": (None, None, "有給"),
        "2024-03-12": (14.0, 23.0, ""),
    },
    "鎌田": {
        "2024-03-01": (14.0, 23.0, ""),
        "2024-03-02": (14.0, 23.0, ""),
        "2024-03-05": (14.0, 23.0, ""),
        "2024-03-06": (14.0, 23.0, ""),
        "2024-03-07": (14.0, 23.0, ""),
        "2024-03-09": (14.0, 23.0, ""),
        "2024-03-11": (14.0, 23.0, ""),
        "2024-03-12": (14.0, 23.0, ""),
        "2024-03-13": (12.0, 23.0, ""),
    },
    "タオリ": {
        "2024-03-01": (5.75, 14.75, ""),
        "2024-03-03": (14.0, 23.0, ""),
        "2024-03-04": (14.0, 23.0, ""),
        "2024-03-05": (14.0, 23.0, ""),
        "2024-03-06": (5.75, 14.75, ""),
        "2024-03-09": (14.0, 23.0, ""),
        "2024-03-10": (14.0, 23.0, ""),
        "2024-03-11": (14.0, 23.0, ""),
        "2024-03-12": (5.75, 14.75, ""),
    },
    "林": {
        "2024-03-02": (5.75, 14.75, ""),
        "2024-03-03": (5.75, 11.5, ""),
        "2024-03-05": (5.75, 11.5, ""),
        "2024-03-06": (5.75, 14.75, ""),
        "2024-03-07": (5.75, 14.75, ""),
        "2024-03-08": (5.75, 11.5, ""),
        "2024-03-10": (5.75, 14.75, ""),
        "2024-03-11": (5.75, 14.75, ""),
        "2024-03-13": (5.75, 14.75, ""),
        "2024-03-15": (5.75, 14.75, ""),
    },
    "ミツイ": {
        "2024-03-01": (5.75, 14.75, ""),
        "2024-03-03": (5.75, 14.5, ""),
        "2024-03-04": (5.75, 14.75, ""),
        "2024-03-05": (5.75, 11.5, ""),
        "2024-03-09": (5.75, 14.75, ""),
        "2024-03-10": (5.75, 11.5, ""),
        "2024-03-11": (5.75, 14.75, ""),
        "2024-03-12": (5.75, 14.75, ""),
    },
    "細田": {
        "2024-03-01": (5.75, 11.5, ""),
        "2024-03-02": (5.75, 11.5, ""),
        "2024-03-04": (5.75, 11.5, ""),
        "2024-03-05": (5.75, 14.75, ""),
        "2024-03-08": (5.75, 14.75, ""),
        "2024-03-09": (5.75, 11.5, ""),
        "2024-03-10": (5.75, 11.5, ""),
        "2024-03-11": (5.75, 11.5, ""),
        "2024-03-13": (17.0, 23.0, ""),   # ディナーにも入る
    },
    "アッスミタ": {
        "2024-03-01": (6.0, 11.5, ""),
        "2024-03-04": (6.0, 11.5, ""),
        "2024-03-08": (6.0, 11.5, ""),
        "2024-03-13": (6.0, 11.5, ""),
    },
    "マニス": {
        "2024-03-02": (6.0, 11.5, ""),
        "2024-03-05": (6.5, 11.5, ""),
        "2024-03-09": (6.0, 11.5, ""),
    },
    "レジーナ": {
        "2024-03-06": (6.0, 11.5, ""),
        "2024-03-11": (6.0, 11.5, ""),
    },
    "ビルシャナ": {
        "2024-03-07": (6.0, 11.5, ""),
        "2024-03-12": (6.0, 11.5, ""),
    },
    "藤内": {
        "2024-03-01": (5.75, 11.5, ""),
        "2024-03-04": (5.75, 11.5, ""),
        "2024-03-07": (5.75, 11.5, ""),
        "2024-03-08": (5.75, 11.5, ""),
    },
    "上田": {
        "2024-03-03": (5.75, 11.5, ""),
        "2024-03-10": (5.75, 11.5, ""),
    },
    "津波古": {
        "2024-03-02": (6.0, 11.5, ""),
        "2024-03-11": (6.0, 11.5, ""),
    },
    "カテリナ": {
        "2024-03-01": (6.0, 11.5, "朝初"),
        "2024-03-02": (6.0, 11.5, ""),
        "2024-03-08": (6.0, 11.5, ""),
        "2024-03-10": (6.0, 11.5, ""),
    },
    "キム": {
        "2024-03-09": (18.0, 22.5, "夜初"),
        "2024-03-10": (18.0, 22.5, ""),
    },
    "齊藤": {
        "2024-03-03": (6.0, 11.5, ""),
        "2024-03-06": (6.0, 11.5, ""),
        "2024-03-07": (6.0, 11.5, ""),
    },
    "藤畑": {
        "2024-03-05": (6.0, 9.0, ""),
        "2024-03-10": (6.0, 9.0, ""),
    },
    "田中": {
        "2024-03-09": (6.5, 10.0, ""),
        "2024-03-14": (6.5, 10.0, ""),
    },
    "島野": {
        "2024-03-04": (6.0, 10.0, ""),
        "2024-03-05": (6.0, 10.0, ""),
        "2024-03-07": (6.0, 10.0, ""),
        "2024-03-11": (6.5, 10.0, ""),
        "2024-03-12": (6.0, 10.0, ""),
    },
    "チャー": {
        "2024-03-02": (7.0, 11.0, ""),
        "2024-03-07": (7.0, 11.0, ""),
        "2024-03-09": (6.5, 10.0, ""),
        "2024-03-12": (18.0, 22.5, ""),   # たまにディナーも
    },
    "坂田": {
        "2024-03-04": (6.0, 10.5, ""),
        "2024-03-06": (6.5, 10.0, ""),
        "2024-03-07": (6.0, 10.0, ""),
        "2024-03-12": (6.5, 10.5, ""),
        "2024-03-15": (17.5, 22.0, ""),
    },
    "北": {
        "2024-03-03": (6.0, 10.0, ""),
        "2024-03-08": (None, None, "有給"),
        "2024-03-11": (6.0, 9.0, ""),
        "2024-03-14": (6.0, 10.0, ""),
    },
    "南愛菜": {
        "2024-03-01": (18.5, 23.0, "ホール兼任"),
        "2024-03-09": (18.5, 23.0, "ホール兼任"),
        "2024-03-10": (18.0, 23.0, "ホール兼任"),
    },
    "ドリアン": {
        "2024-03-01": (17.0, 22.5, ""),
        "2024-03-03": (17.0, 22.5, ""),
        "2024-03-05": (17.0, 22.5, ""),
        "2024-03-10": (17.0, 22.5, ""),
        "2024-03-11": (17.0, 22.5, ""),
    },
    "中家": {
        "2024-03-02": (17.0, 23.0, "夜初"),
        "2024-03-04": (17.0, 23.0, ""),
        "2024-03-11": (17.0, 23.0, ""),
    },
    "光久": {
        "2024-03-11": (17.5, 22.5, "ホール兼任"),
    },
    "南志穂": {
        "2024-03-06": (17.5, 23.0, ""),
    },
    "岡本": {
        "2024-03-01": (17.5, 22.0, ""),
        "2024-03-03": (18.0, 22.0, ""),
        "2024-03-04": (18.0, 22.0, ""),
        "2024-03-05": (18.0, 23.0, ""),
        "2024-03-06": (17.5, 21.0, ""),
        "2024-03-08": (17.5, 21.0, ""),
        "2024-03-09": (18.75, 22.0, "キッチン兼任"),
        "2024-03-11": (18.0, 23.0, ""),
    },
    "廣田": {
        "2024-03-02": (18.0, 22.5, ""),
        "2024-03-06": (18.5, 22.5, ""),
    },
    "リット": {
        "2024-03-06": (18.0, 23.0, ""),
        "2024-03-08": (18.5, 23.0, ""),
    },
    "鈴木": {
        "2024-03-01": (6.5, 11.5, ""),
        "2024-03-15": (18.0, 23.0, ""),
    },
    "名古": {
        "2024-03-03": (17.5, 23.0, "キッチン"),
        "2024-03-07": (18.0, 23.0, ""),
        "2024-03-11": (18.0, 23.0, ""),
        "2024-03-13": (17.0, 23.0, ""),
        "2024-03-14": (18.0, 23.0, ""),
    },
    "坂本": {
        "2024-03-06": (18.0, 21.5, "キッチン兼任"),
        "2024-03-09": (18.0, 21.5, ""),
    },
    "井上": {},  # シフトなし
    "アリャー": {
        "2024-03-12": (18.0, 21.0, ""),
    },
}

# ═══════════════════════════════════════════════════════════════════════
# ユーティリティ関数
# ═══════════════════════════════════════════════════════════════════════

def dec_to_hhmm(dec: float) -> str:
    """小数時刻 (5.75) → 'HH:MM' ('05:45')"""
    h = int(dec)
    m = round((dec - h) * 60)
    if m == 60:
        h += 1
        m = 0
    return f"{h:02d}:{m:02d}"


def overlaps_slot(s: float, e: float, slot_s: float, slot_e: float) -> bool:
    """シフトがスロットと MIN_OVERLAP_H 時間以上重なるか"""
    overlap = max(0.0, min(e, slot_e) - max(s, slot_s))
    return overlap >= MIN_OVERLAP_H


def get_timeslots(s: float, e: float) -> list[str]:
    """(start, end) が朝食・ディナーのどちらをカバーするか返す"""
    slots = []
    if overlaps_slot(s, e, BREAKFAST_START, BREAKFAST_END):
        slots.append(TimeSlot.BREAKFAST.value)
    if overlaps_slot(s, e, DINNER_START, DINNER_END):
        slots.append(TimeSlot.DINNER.value)
    return slots


def find_pattern_id(s: float, e: float) -> tuple[str, str | None, str | None]:
    """
    (start, end) に最も近いパターンIDを返す。
    閾値内に該当なければ "custom" を返す。
    """
    THRESHOLD = 0.3  # 18分以内なら一致とみなす
    best, best_diff = None, float("inf")
    for p in ALL_PATTERNS:
        if p.id in ("custom", "double") or not p.start or not p.end:
            continue
        diff = abs(p.start_hour() - s) + abs(p.end_hour() - e)
        if diff < best_diff:
            best_diff = diff
            best = p
    if best and best_diff <= THRESHOLD:
        return best.id, None, None
    return "custom", dec_to_hhmm(s), dec_to_hhmm(e)


# ═══════════════════════════════════════════════════════════════════════
# 希望シフトのバリエーション生成（確定前を模擬）
# ═══════════════════════════════════════════════════════════════════════
# 確定シフトと異なる「希望」を生成するためのルール:
#   ① 確定シフトと同じ日 → 同じパターンで希望
#   ② 一部の日は「余分に空きを入れる」（希望したが入れなかった日）
#   ③ 正社員は希望＝確定（ほぼ固定）

def _random_extra_dates(name: str, confirmed_dates: set, all_dates: list) -> set:
    """確定されなかった「余分の希望日」をランダムで追加（アルバイトのみ）"""
    candidates = [d for d in all_dates if d not in confirmed_dates]
    n_extra = random.randint(0, min(3, len(candidates)))
    return set(random.sample(candidates, n_extra))


def build_requests(
    emp: Employee,
    confirmed: dict[str, tuple],
    all_dates: list[str],
    employment_type: str,
) -> list[ShiftRequest]:
    """ShiftRequest のリストを生成（確定前の希望シフト）"""
    requests = []
    confirmed_dates = set(confirmed.keys())

    # アルバイト: 余分希望日を追加
    if employment_type == "part_time":
        extra = _random_extra_dates(emp.name, confirmed_dates, all_dates)
    else:
        extra = set()

    for date_str in all_dates:
        if date_str in confirmed:
            s, e, note = confirmed[date_str]
            if note == "有給":
                # 有給は希望に "有給" ノートを入れる
                req = ShiftRequest(
                    employee_id=emp.id,
                    date=date_str,
                    pattern_id=None,
                    note="有給",
                )
            elif s is None:
                continue  # 休みはリクエスト不要
            else:
                pid, cs, ce = find_pattern_id(s, e)
                req = ShiftRequest(
                    employee_id=emp.id,
                    date=date_str,
                    pattern_id=pid,
                    custom_start=cs,
                    custom_end=ce,
                    note=note,
                )
            requests.append(req)

        elif date_str in extra:
            # 余分希望: 主に使うパターン帯を再現
            # 確定シフトのパターンから代表的な時間帯を推定
            if confirmed:
                sample = [v for v in confirmed.values() if v[0] is not None]
                if sample:
                    ss, se, _ = random.choice(sample)
                    pid, cs, ce = find_pattern_id(ss, se)
                    requests.append(ShiftRequest(
                        employee_id=emp.id,
                        date=date_str,
                        pattern_id=pid,
                        custom_start=cs,
                        custom_end=ce,
                        note="",
                    ))

    return requests


# ═══════════════════════════════════════════════════════════════════════
# 確定アサイン生成
# ═══════════════════════════════════════════════════════════════════════

def build_assignments(
    emp: Employee,
    confirmed: dict[str, tuple],
    default_pos: str,
    period_id: int,
) -> list[ShiftAssignment]:
    """ShiftAssignment のリストを生成（確定シフト）"""
    assignments = []
    for date_str, (s, e, note) in confirmed.items():
        if s is None:
            continue  # 有給・休み
        slots = get_timeslots(s, e)
        # 備考にポジション情報がある場合は優先
        pos_str = default_pos
        note_lower = note.lower()
        if "ホール" in note:
            pos_str = "hall"
        elif "キッチン" in note:
            pos_str = "kitchen"

        for slot_str in slots:
            assignments.append(ShiftAssignment(
                employee_id=emp.id,
                date=date_str,
                time_slot=TimeSlot(slot_str),
                position=Position(pos_str),
            ))
    return assignments


# ═══════════════════════════════════════════════════════════════════════
# メイン処理
# ═══════════════════════════════════════════════════════════════════════

def reset_db():
    """全データを削除（テーブル構造は維持）"""
    conn = get_connection()
    conn.execute("DELETE FROM shift_assignments")
    conn.execute("DELETE FROM shift_requests")
    conn.execute("DELETE FROM schedule_periods")
    conn.execute("DELETE FROM employees")
    conn.commit()
    conn.close()
    print("✓ 既存データをリセットしました")


def main():
    parser = argparse.ArgumentParser(description="テストデータ生成")
    parser.add_argument("--reset", action="store_true", help="既存データを全削除してから追加")
    args = parser.parse_args()

    initialize_db()
    if args.reset:
        reset_db()

    # ── 従業員を登録 ──────────────────────────────────────────────────
    print("従業員を登録中...")
    emp_map: dict[str, Employee] = {}

    for name, emp_type, hall_sk, kit_sk, _ in EMPLOYEE_MASTER:
        emp = Employee(
            id=None,
            name=name,
            employment_type=EmploymentType(emp_type),
            hall_skill_breakfast=SkillLevel(hall_sk),
            hall_skill_dinner=SkillLevel(hall_sk),
            kitchen_skill_breakfast=SkillLevel(kit_sk),
            kitchen_skill_dinner=SkillLevel(kit_sk),
        )
        saved = repo.save_employee(emp)
        emp_map[name] = saved

    print(f"  → {len(emp_map)} 名を登録")

    # ── 期間を登録 ──────────────────────────────────────────────────
    period = repo.save_period(SchedulePeriod(
        id=None,
        start_date=PERIOD_START,
        end_date=PERIOD_END,
        status="draft",
    ))
    print(f"  → 期間 {PERIOD_START}〜{PERIOD_END} (id={period.id}) を登録")

    from datetime import date, timedelta
    start_d = date.fromisoformat(PERIOD_START)
    end_d   = date.fromisoformat(PERIOD_END)
    all_dates = []
    cur = start_d
    while cur <= end_d:
        all_dates.append(cur.isoformat())
        cur += timedelta(days=1)

    # ── 希望シフトを登録 ──────────────────────────────────────────────
    print("希望シフト(確定前)を登録中...")
    pos_map = {name: pos for name, _, _, _, pos in EMPLOYEE_MASTER}
    total_req = 0
    for name, emp in emp_map.items():
        confirmed = CONFIRMED_SHIFTS.get(name, {})
        emp_type  = next(t for n, t, _, _, _ in EMPLOYEE_MASTER if n == name)
        requests  = build_requests(emp, confirmed, all_dates, emp_type)
        if requests:
            repo.save_shift_requests(period.id, requests)
            total_req += len(requests)
    print(f"  → {total_req} 件の希望シフトを登録")

    # ── 確定シフトアサインを登録 ──────────────────────────────────────
    print("確定シフト(アサイン)を登録中...")
    all_assignments: list[ShiftAssignment] = []
    for name, emp in emp_map.items():
        confirmed = CONFIRMED_SHIFTS.get(name, {})
        default_pos = pos_map.get(name, "hall")
        assignments = build_assignments(emp, confirmed, default_pos, period.id)
        all_assignments.extend(assignments)
    repo.save_assignments(period.id, all_assignments)
    print(f"  → {len(all_assignments)} 件のアサインを登録")

    # ── 制約チェックサマリ ────────────────────────────────────────────
    print("\n=== 日別制約チェック (朝食/ディナー) ===")
    from collections import defaultdict
    from utils.constants import SHIFT_CONSTRAINTS
    assign_map = defaultdict(int)
    for a in all_assignments:
        assign_map[(a.date, a.time_slot.value, a.position.value)] += 1

    violations = 0
    for ds in all_dates:
        d = date.fromisoformat(ds)
        issues = []
        for (slot, pos), c in SHIFT_CONSTRAINTS.items():
            cnt = assign_map[(ds, slot.value, pos.value)]
            if cnt < c["min"]:
                issues.append(
                    f"  ❌ {slot.short_label()}{pos.label()}: {cnt}名 < min{c['min']}"
                )
                violations += 1
        if issues:
            dow = ["月","火","水","木","金","土","日"][d.weekday()]
            print(f"  {d.month}/{d.day}({dow})")
            for msg in issues:
                print(msg)

    if violations == 0:
        print("  全日程で人員制約クリア ✓")
    else:
        print(f"\n  ⚠ {violations}件の制約違反（実際のシフト表を忠実に再現した結果）")
        print("  ※ テスト用データのため、一部日程でスタッフが少ない場合があります")

    print("\n✅ テストデータの生成が完了しました")
    print(f"   期間ID: {period.id}")
    print(f"   従業員: {len(emp_map)} 名")
    print(f"   希望シフト: {total_req} 件")
    print(f"   確定アサイン: {len(all_assignments)} 件")
    print("\nアプリを起動して「シフト表 確認・編集」画面で確認できます。")


if __name__ == "__main__":
    main()
