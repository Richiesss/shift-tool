"""初期データ投入モジュール。DBが空のときのみ実行される。"""
from __future__ import annotations
import json
import os
from pathlib import Path

_SEED_FILE = Path(__file__).parent / "seed_data.json"

# 挿入順序（外部キー依存順）
_TABLE_ORDER = [
    "employees",
    "fixed_patterns",
    "fixed_unavailable_dates",
    "schedule_periods",
    "shift_requests",
    "shift_assignments",
]


def seed_if_empty(conn) -> bool:
    """employees テーブルが空のときだけシードデータを投入する。"""
    if not _SEED_FILE.exists():
        return False

    count = conn.execute("SELECT COUNT(*) as c FROM employees").fetchone()["c"]
    if count > 0:
        return False

    with open(_SEED_FILE, encoding="utf-8") as f:
        data: dict = json.load(f)

    for table in _TABLE_ORDER:
        rows = data.get(table, [])
        if not rows:
            continue
        cols = list(rows[0].keys())
        # DBスキーマに存在するカラムだけ使う
        cols = _filter_existing_cols(conn, table, cols)
        placeholders = ", ".join(["?" if conn.backend == "sqlite" else "%s"] * len(cols))
        col_list = ", ".join(cols)
        sql = f"INSERT INTO {table} ({col_list}) VALUES ({placeholders})"
        for row in rows:
            values = [row.get(c) for c in cols]
            try:
                conn.execute(sql, values)
            except Exception:
                pass

    # PostgreSQL: SERIAL シーケンスを最大IDに合わせる
    if conn.backend == "postgres":
        for table in ["employees", "schedule_periods", "shift_requests", "shift_assignments"]:
            conn.execute(
                f"SELECT setval(pg_get_serial_sequence('{table}', 'id'), "
                f"COALESCE((SELECT MAX(id) FROM {table}), 1))"
            )

    conn.commit()
    return True


def _filter_existing_cols(conn, table: str, cols: list[str]) -> list[str]:
    """テーブルに実際に存在するカラムだけを返す。"""
    if conn.backend == "postgres":
        result = conn.execute(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_name=%s",
            (table,)
        ).fetchall()
        existing = {r["column_name"] for r in result}
    else:
        result = conn.execute(f"PRAGMA table_info({table})").fetchall()
        existing = {r["name"] for r in result}
    return [c for c in cols if c in existing]
