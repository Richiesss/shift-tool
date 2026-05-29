"""初期データ投入モジュール。DBが空のときのみ実行される。"""
from __future__ import annotations
import json
from pathlib import Path

_SEED_FILE = Path(__file__).parent / "seed_data.json"

_TABLE_ORDER = [
    "employees",
    "fixed_patterns",
    "fixed_unavailable_dates",
    "schedule_periods",
    "shift_requests",
    "shift_assignments",
]


def seed_if_empty(conn) -> bool:
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

        schema = _get_schema(conn, table)  # {col: (not_null, default)}
        cols = [c for c in rows[0].keys() if c in schema]

        ph = "%s" if conn.backend == "postgres" else "?"
        placeholders = ", ".join([ph] * len(cols))
        col_list = ", ".join(cols)
        sql = f"INSERT INTO {table} ({col_list}) VALUES ({placeholders})"

        for row in rows:
            values = []
            for c in cols:
                v = row.get(c)
                if v is None:
                    not_null, default = schema[c]
                    if not_null:
                        # NOT NULL カラムの null は DEFAULT 値で補完
                        v = _parse_default(default)
                values.append(v)
            try:
                conn.execute(sql, values)
            except Exception as e:
                # PostgreSQL: 1件失敗でトランザクションがアボートするため rollback
                if conn.backend == "postgres":
                    try:
                        conn._conn.rollback()
                    except Exception:
                        pass
                print(f"[seeder] skipped row in {table}: {e}")

    # PostgreSQL: SERIAL シーケンスを最大IDに合わせる
    if conn.backend == "postgres":
        for table in ["employees", "schedule_periods", "shift_requests", "shift_assignments"]:
            try:
                conn.execute(
                    f"SELECT setval(pg_get_serial_sequence('{table}', 'id'), "
                    f"COALESCE((SELECT MAX(id) FROM {table}), 1))"
                )
            except Exception:
                if conn.backend == "postgres":
                    conn._conn.rollback()

    conn.commit()
    return True


def _get_schema(conn, table: str) -> dict:
    """カラム名 → (not_null: bool, default: str|None) のマップを返す"""
    if conn.backend == "postgres":
        rows = conn.execute(
            "SELECT column_name, is_nullable, column_default "
            "FROM information_schema.columns WHERE table_name=%s",
            (table,)
        ).fetchall()
        return {
            r["column_name"]: (r["is_nullable"] == "NO", r["column_default"])
            for r in rows
        }
    else:
        rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
        return {
            r["name"]: (bool(r["notnull"]), r["dflt_value"])
            for r in rows
        }


def _parse_default(default) -> object:
    """DEFAULT 値文字列を Python 値に変換"""
    if default is None:
        return None
    s = str(default).strip().strip("'\"")
    if s.lstrip("-").isdigit():
        return int(s)
    if s.lower() in ("true", "false"):
        return s.lower() == "true"
    return s or None
