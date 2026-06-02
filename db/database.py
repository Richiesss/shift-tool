import os
import re
import sys
import threading
from pathlib import Path

DATABASE_URL = os.environ.get("DATABASE_URL", "")

# SQLite fallback path (desktop / dev)
if getattr(sys, "frozen", False):
    if sys.platform == "darwin":
        _DB_PATH = (Path.home() / "Library" / "Application Support"
                    / "SDU-Shift" / "shift_tool.db")
    else:
        _DB_PATH = Path(os.environ.get("APPDATA", str(Path.home()))) / "SDU-Shift" / "shift_tool.db"
else:
    _DB_PATH = Path.home() / ".shift_tool" / "shift_tool.db"

# PostgreSQL connection pool (reuse connections across requests)
_pg_pool = None
_pg_pool_lock = threading.Lock()


def _get_pg_pool():
    global _pg_pool
    if _pg_pool is not None and not _pg_pool.closed:
        return _pg_pool
    with _pg_pool_lock:
        if _pg_pool is None or _pg_pool.closed:
            import psycopg2.pool
            url = DATABASE_URL
            if url.startswith("postgres://"):
                url = "postgresql://" + url[len("postgres://"):]
            _pg_pool = psycopg2.pool.ThreadedConnectionPool(1, 5, url)
    return _pg_pool


def _to_pg(sql: str) -> str:
    sql = sql.replace("?", "%s")
    sql = re.sub(r"INTEGER\s+PRIMARY\s+KEY\s+AUTOINCREMENT", "SERIAL PRIMARY KEY",
                 sql, flags=re.IGNORECASE)
    sql = sql.replace("DEFAULT (datetime('now','localtime'))", "DEFAULT CURRENT_TIMESTAMP")
    return sql


class _Row:
    """psycopg2 RealDictRow を sqlite3.Row と同じ interface でラップ"""
    __slots__ = ("_d",)
    def __init__(self, d: dict): self._d = d
    def __getitem__(self, k): return self._d[k]
    def keys(self): return self._d.keys()
    def __contains__(self, k): return k in self._d


class _PgCursor:
    def __init__(self, cur): self._cur = cur
    def fetchone(self):
        r = self._cur.fetchone()
        return _Row(dict(r)) if r is not None else None
    def fetchall(self):
        return [_Row(dict(r)) for r in self._cur.fetchall()]


class Connection:
    def __init__(self):
        if DATABASE_URL:
            import psycopg2.extras
            self._pool = _get_pg_pool()
            self._conn = self._pool.getconn()
            self._factory = psycopg2.extras.RealDictCursor
            self.backend = "postgres"
        else:
            import sqlite3
            _DB_PATH.parent.mkdir(parents=True, exist_ok=True)
            self._conn = sqlite3.connect(str(_DB_PATH), timeout=30)
            self._conn.row_factory = sqlite3.Row
            self._conn.execute("PRAGMA foreign_keys = ON")
            self._conn.execute("PRAGMA journal_mode = WAL")
            self._pool = None
            self.backend = "sqlite"
        self.lastrowid = None

    def _pg_execute(self, sql: str, params):
        import psycopg2, time
        last_exc = None
        for attempt in range(3):
            try:
                cur = self._conn.cursor(cursor_factory=self._factory)
                cur.execute(sql, params)
                return cur
            except (psycopg2.OperationalError, psycopg2.InterfaceError) as e:
                last_exc = e
                # stale/dead connection — get a fresh one from pool
                try:
                    self._pool.putconn(self._conn, close=True)
                except Exception:
                    pass
                try:
                    self._conn = self._pool.getconn()
                except Exception:
                    pass
                if attempt < 2:
                    time.sleep(0.5 * (attempt + 1))
        raise last_exc

    def execute(self, sql: str, params=()):
        if self.backend == "postgres":
            cur = self._pg_execute(_to_pg(sql), list(params) if params else None)
            self.lastrowid = None
            return _PgCursor(cur)
        else:
            cur = self._conn.execute(sql, params)
            self.lastrowid = cur.lastrowid
            return cur

    def execute_insert(self, sql: str, params=()) -> int:
        """INSERT して新規 id を返す（両 backend 対応）"""
        if self.backend == "postgres":
            adapted = _to_pg(sql).rstrip("; ") + " RETURNING id"
            cur = self._pg_execute(adapted, list(params) if params else None)
            row = cur.fetchone()
            return row["id"] if row else None
        else:
            cur = self._conn.execute(sql, params)
            return cur.lastrowid

    def commit(self): self._conn.commit()

    def close(self):
        if getattr(self, "_flask_managed", False):
            return  # Flask teardown が責任を持つ
        if self.backend == "postgres" and self._pool:
            self._pool.putconn(self._conn)
        else:
            self._conn.close()


def get_connection() -> Connection:
    """Flask リクエスト中は接続を g で共有し、プール貸し借りを削減する"""
    try:
        from flask import g, has_request_context
        if has_request_context():
            if not hasattr(g, "_db_conn"):
                conn = Connection()
                conn._flask_managed = True
                g._db_conn = conn
            return g._db_conn
    except (ImportError, RuntimeError):
        pass
    return Connection()


# ── DDL helpers ──────────────────────────────────────────────────────────────

_CREATE_TABLES = [
    """CREATE TABLE IF NOT EXISTS employees (
        id              INTEGER PRIMARY KEY AUTOINCREMENT,
        name            TEXT NOT NULL,
        employment_type TEXT NOT NULL CHECK(employment_type IN ('full_time','part_time')),
        hall_skill      TEXT NOT NULL DEFAULT 'beginner'
                      CHECK(hall_skill IN ('leader','veteran','general','beginner')),
        kitchen_skill   TEXT NOT NULL DEFAULT 'beginner'
                      CHECK(kitchen_skill IN ('leader','veteran','general','beginner')),
        is_active       INTEGER NOT NULL DEFAULT 1
    )""",
    """CREATE TABLE IF NOT EXISTS fixed_patterns (
        id          INTEGER PRIMARY KEY AUTOINCREMENT,
        employee_id INTEGER NOT NULL REFERENCES employees(id) ON DELETE CASCADE,
        day_of_week INTEGER NOT NULL CHECK(day_of_week BETWEEN 0 AND 6),
        breakfast   INTEGER NOT NULL DEFAULT 0,
        dinner      INTEGER NOT NULL DEFAULT 0,
        UNIQUE(employee_id, day_of_week)
    )""",
    """CREATE TABLE IF NOT EXISTS fixed_unavailable_dates (
        id          INTEGER PRIMARY KEY AUTOINCREMENT,
        employee_id INTEGER NOT NULL REFERENCES employees(id) ON DELETE CASCADE,
        date        TEXT NOT NULL,
        UNIQUE(employee_id, date)
    )""",
    """CREATE TABLE IF NOT EXISTS schedule_periods (
        id          INTEGER PRIMARY KEY AUTOINCREMENT,
        start_date  TEXT NOT NULL,
        end_date    TEXT NOT NULL,
        status      TEXT NOT NULL DEFAULT 'draft'
                      CHECK(status IN ('draft','confirmed')),
        created_at  TEXT NOT NULL DEFAULT (datetime('now','localtime')),
        UNIQUE(start_date)
    )""",
    """CREATE TABLE IF NOT EXISTS shift_requests (
        id          INTEGER PRIMARY KEY AUTOINCREMENT,
        period_id   INTEGER NOT NULL REFERENCES schedule_periods(id) ON DELETE CASCADE,
        employee_id INTEGER NOT NULL REFERENCES employees(id) ON DELETE CASCADE,
        date        TEXT NOT NULL,
        breakfast   INTEGER NOT NULL DEFAULT 0,
        dinner      INTEGER NOT NULL DEFAULT 0,
        note        TEXT DEFAULT '',
        UNIQUE(period_id, employee_id, date)
    )""",
    """CREATE TABLE IF NOT EXISTS shift_assignments (
        id          INTEGER PRIMARY KEY AUTOINCREMENT,
        period_id   INTEGER NOT NULL REFERENCES schedule_periods(id) ON DELETE CASCADE,
        employee_id INTEGER NOT NULL REFERENCES employees(id) ON DELETE CASCADE,
        date        TEXT NOT NULL,
        time_slot   TEXT NOT NULL CHECK(time_slot IN ('breakfast','dinner')),
        position    TEXT NOT NULL CHECK(position IN ('hall','kitchen')),
        UNIQUE(period_id, employee_id, date, time_slot)
    )""",
    """CREATE TABLE IF NOT EXISTS shift_constraints (
        slot        TEXT NOT NULL,
        position    TEXT NOT NULL,
        min_staff   INTEGER NOT NULL DEFAULT 2,
        max_staff   INTEGER NOT NULL DEFAULT 4,
        min_leader  INTEGER NOT NULL DEFAULT 1,
        PRIMARY KEY (slot, position)
    )""",
    """CREATE TABLE IF NOT EXISTS schedule_notes (
        id        INTEGER PRIMARY KEY AUTOINCREMENT,
        period_id INTEGER NOT NULL REFERENCES schedule_periods(id) ON DELETE CASCADE,
        date      TEXT NOT NULL,
        note      TEXT NOT NULL DEFAULT '',
        UNIQUE(period_id, date)
    )""",
    """CREATE TABLE IF NOT EXISTS reservation_counts (
        id        INTEGER PRIMARY KEY AUTOINCREMENT,
        period_id INTEGER NOT NULL REFERENCES schedule_periods(id) ON DELETE CASCADE,
        date      TEXT NOT NULL,
        breakfast INTEGER NOT NULL DEFAULT 0,
        dinner    INTEGER NOT NULL DEFAULT 0,
        UNIQUE(period_id, date)
    )""",
    """CREATE TABLE IF NOT EXISTS app_settings (
        key   TEXT PRIMARY KEY,
        value TEXT NOT NULL DEFAULT ''
    )""",
    """CREATE TABLE IF NOT EXISTS breakfast_band_constraints (
        band       TEXT NOT NULL,
        position   TEXT NOT NULL,
        min_staff  INTEGER NOT NULL DEFAULT 0,
        max_staff  INTEGER NOT NULL DEFAULT 10,
        min_leader INTEGER NOT NULL DEFAULT 0,
        PRIMARY KEY (band, position)
    )""",
]

_MIGRATIONS = [
    ("shift_requests",    "pattern_id",                 "TEXT"),
    ("shift_requests",    "custom_start",               "TEXT"),
    ("shift_requests",    "custom_end",                 "TEXT"),
    ("employees",         "primary_position",           "TEXT DEFAULT NULL"),
    ("employees",         "primary_timeslot",           "TEXT DEFAULT NULL"),
    ("employees",         "can_work_both_positions",    "INTEGER NOT NULL DEFAULT 0"),
    ("shift_assignments", "is_reinforcement",           "INTEGER NOT NULL DEFAULT 0"),
    ("shift_assignments", "reinf_start",                "TEXT DEFAULT NULL"),
    ("shift_assignments", "reinf_end",                  "TEXT DEFAULT NULL"),
    ("employees",         "can_open",                   "INTEGER NOT NULL DEFAULT 0"),
    ("employees",         "can_cleanup",                "INTEGER NOT NULL DEFAULT 0"),
    ("employees",         "always_available_breakfast", "INTEGER NOT NULL DEFAULT 0"),
    ("employees",         "always_available_dinner",    "INTEGER NOT NULL DEFAULT 0"),
    ("schedule_periods",  "gen_status",                 "TEXT NOT NULL DEFAULT 'idle'"),
    ("schedule_periods",  "gen_message",                "TEXT NOT NULL DEFAULT ''"),
    ("employees",         "display_order",              "INTEGER NOT NULL DEFAULT 0"),
    ("schedule_periods",  "needs_regen",                "INTEGER NOT NULL DEFAULT 0"),
    ("employees",         "output_position",            "TEXT"),
]

_INDEXES = [
    "CREATE INDEX IF NOT EXISTS idx_shift_assignments_period ON shift_assignments(period_id)",
    "CREATE INDEX IF NOT EXISTS idx_shift_requests_period    ON shift_requests(period_id)",
    "CREATE INDEX IF NOT EXISTS idx_fixed_patterns_emp       ON fixed_patterns(employee_id)",
    "CREATE INDEX IF NOT EXISTS idx_fixed_unavail_emp        ON fixed_unavailable_dates(employee_id)",
    "CREATE INDEX IF NOT EXISTS idx_reservation_period       ON reservation_counts(period_id)",
    "CREATE INDEX IF NOT EXISTS idx_schedule_notes_period    ON schedule_notes(period_id)",
]


def initialize_db():
    conn = get_connection()

    # DDL は autocommit で実行（失敗した ALTER TABLE が後続を巻き込まないよう）
    if conn.backend == "postgres":
        conn._conn.autocommit = True

    for ddl in _CREATE_TABLES:
        conn.execute(ddl)

    for idx in _INDEXES:
        conn.execute(idx)

    for table, col, definition in _MIGRATIONS:
        try:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {col} {definition}")
        except Exception:
            pass

    if conn.backend == "postgres":
        conn._conn.autocommit = False

    # 既存データの output_position 不整合を修正:
    # primary_position が設定されているのに output_position が異なる or NULL の従業員を修正
    try:
        conn.execute(
            "UPDATE employees SET output_position = primary_position"
            " WHERE primary_position IS NOT NULL"
            " AND (output_position IS NULL OR output_position != primary_position)"
        )
    except Exception:
        pass

    for slot, pos, mn, mx, ml in [
        ("breakfast", "hall",    3, 4, 1),
        ("breakfast", "kitchen", 3, 3, 1),
        ("dinner",    "hall",    2, 3, 1),
        ("dinner",    "kitchen", 3, 3, 2),
    ]:
        conn.execute(
            "INSERT INTO shift_constraints (slot, position, min_staff, max_staff, min_leader)"
            " VALUES (?,?,?,?,?) ON CONFLICT DO NOTHING",
            (slot, pos, mn, mx, ml)
        )

    for key, val in [
        ("reserv_threshold_breakfast", "100"),
        ("reserv_extra_breakfast",     "1"),
        ("reserv_threshold_dinner",    "25"),
        ("reserv_extra_dinner",        "1"),
    ]:
        conn.execute(
            "INSERT INTO app_settings (key, value) VALUES (?,?) ON CONFLICT DO NOTHING",
            (key, val)
        )

    for band, pos, mn, mx, ml in [
        ("open",    "hall",    2, 4, 0),
        ("open",    "kitchen", 0, 2, 0),
        ("cleanup", "hall",    1, 2, 1),
        ("cleanup", "kitchen", 0, 1, 0),
    ]:
        conn.execute(
            "INSERT INTO breakfast_band_constraints (band, position, min_staff, max_staff, min_leader)"
            " VALUES (?,?,?,?,?) ON CONFLICT DO NOTHING",
            (band, pos, mn, mx, ml)
        )

    conn.commit()

    # DBが空なら初期データを投入
    from db.seeder import seed_if_empty
    seed_if_empty(conn)

    conn.close()
