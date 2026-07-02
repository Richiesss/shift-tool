import os
import re
import threading
from pathlib import Path

# DATABASE_URL は環境変数を動的に反映させるため、__getattr__ で取得可能にします
def __getattr__(name):
    if name == "DATABASE_URL":
        return os.environ.get("DATABASE_URL", "")
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

# Global flag to dynamically disable statement timeout if it is not supported by the pooler
_disable_statement_timeout = os.environ.get("DISABLE_STATEMENT_TIMEOUT", "").lower() in ("true", "1", "yes")

# SQLite fallback path (dev / HF Spaces)
DB_PATH = Path.home() / ".shift_tool" / "shift_tool.db"

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
            url = os.environ.get("DATABASE_URL", "")
            if url.startswith("postgres://"):
                url = "postgresql://" + url[len("postgres://"):]
            # keepalives: プール内で再利用される接続が裏側で切断されていた場合に
            # 早期に検知し、OperationalError として再接続できるようにする
            # （statement_timeout は Neon 等のプール接続だと起動パラメータとして
            #   拒否されるため、Connection 側で SET により都度設定する）
            _pg_pool = psycopg2.pool.ThreadedConnectionPool(
                1, 5, url,
                keepalives=1,
                keepalives_idle=30,
                keepalives_interval=10,
                keepalives_count=3,
            )
    return _pg_pool


def _to_pg(sql: str) -> str:
    sql = sql.replace("?", "%s")
    sql = re.sub(r"INTEGER\s+PRIMARY\s+KEY\s+AUTOINCREMENT", "SERIAL PRIMARY KEY",
                 sql, flags=re.IGNORECASE)
    sql = sql.replace("DEFAULT (datetime('now','localtime'))", "DEFAULT CURRENT_TIMESTAMP")
    # SQLite の日付関数 → PostgreSQL 相当
    sql = re.sub(
        r"strftime\('%w',\s*(\w+)\)",
        r"EXTRACT(DOW FROM \1::date)::int",
        sql,
    )
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
    @property
    def rowcount(self):
        return self._cur.rowcount


class Connection:
    def __init__(self):
        db_url = os.environ.get("DATABASE_URL", "")
        if db_url:
            import psycopg2.extras
            self._pool = _get_pg_pool()
            self._conn = self._pool.getconn()
            self._factory = psycopg2.extras.RealDictCursor
            self.backend = "postgres"
            self._set_statement_timeout()
        else:
            import sqlite3
            DB_PATH.parent.mkdir(parents=True, exist_ok=True)
            self._conn = sqlite3.connect(str(DB_PATH), timeout=30)
            self._conn.row_factory = sqlite3.Row
            self._conn.execute("PRAGMA foreign_keys = ON")
            self._conn.execute("PRAGMA journal_mode = WAL")
            self._pool = None
            self.backend = "sqlite"
        self.lastrowid = None
        # 直近の commit/rollback 以降に文を実行済みか（接続断リトライの安全判定用）
        self._tx_dirty = False

    def _set_statement_timeout(self):
        """クエリが何らかの理由でハング/ロック待ちになっても
        gunicorn の worker timeout（120秒）より先にDB側で打ち切る"""
        global _disable_statement_timeout
        if _disable_statement_timeout:
            return
        try:
            cur = self._conn.cursor()
            cur.execute("SET statement_timeout = 15000")
            cur.close()
            # SET はトランザクション内で発行されるため、ここで確定させておく
            # （直後に autocommit を切り替える initialize_db 等が
            #   "set_session cannot be used inside a transaction" で
            #   失敗するのを防ぐ）
            self._conn.commit()
        except Exception:
            try:
                self._conn.rollback()
            except Exception:
                pass
            # 一度失敗したら、以降はタイムアウト設定をスキップする（トランザクションプール対策）
            _disable_statement_timeout = True

    def _pg_execute(self, sql: str, params):
        import psycopg2
        last_err = None
        for _ in range(3):
            try:
                cur = self._conn.cursor(cursor_factory=self._factory)
                cur.execute(sql, params)
                self._tx_dirty = True
                return cur
            except psycopg2.errors.QueryCanceled:
                # statement_timeout によるクエリキャンセル（SQLSTATE 57014）。
                # QueryCanceled は OperationalError のサブクラスだが、接続自体は
                # 健全なので再接続リトライはせず、トランザクションをロールバック
                # してそのまま再送出する（再試行しても同じクエリは再度タイムアウトする）
                try:
                    self._conn.rollback()
                except Exception:
                    pass
                self._tx_dirty = False
                raise
            except (psycopg2.OperationalError, psycopg2.InterfaceError) as e:
                # 接続切断（Neonのアイドルタイムアウト等で「connection already
                # closed」になるケースを含む）— プールから破棄して新しい接続を
                # 取得する
                last_err = e
                was_dirty = self._tx_dirty
                try:
                    self._pool.putconn(self._conn, close=True)
                except Exception:
                    pass
                self._conn = self._pool.getconn()
                self._tx_dirty = False
                self._set_statement_timeout()
                if was_dirty:
                    # トランザクション内で既に文を実行済み（未コミットの変更がある）
                    # 状態での接続断は、新しい接続でリトライすると先行する文の
                    # 効果が失われたまま現在の文だけが実行されてしまう。
                    # リトライせず呼び出し元に伝播し、トランザクション全体を
                    # やり直してもらう。
                    raise
            except psycopg2.Error:
                # クエリエラー（statement_timeout含む）でトランザクションが
                # 中断状態のままプールに戻らないようロールバックしてから再送出
                try:
                    self._conn.rollback()
                except Exception:
                    pass
                self._tx_dirty = False
                raise
        raise last_err

    def rollback(self):
        try:
            self._conn.rollback()
        except Exception:
            pass
        self._tx_dirty = False

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

    def commit(self):
        self._conn.commit()
        self._tx_dirty = False

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
        hall_skill_breakfast    TEXT NOT NULL DEFAULT 'beginner'
                      CHECK(hall_skill_breakfast IN ('leader','veteran','general','beginner')),
        hall_skill_dinner       TEXT NOT NULL DEFAULT 'beginner'
                      CHECK(hall_skill_dinner IN ('leader','veteran','general','beginner')),
        kitchen_skill_breakfast TEXT NOT NULL DEFAULT 'beginner'
                      CHECK(kitchen_skill_breakfast IN ('leader','veteran','general','beginner')),
        kitchen_skill_dinner    TEXT NOT NULL DEFAULT 'beginner'
                      CHECK(kitchen_skill_dinner IN ('leader','veteran','general','beginner')),
        is_active       INTEGER NOT NULL DEFAULT 1
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
    """CREATE TABLE IF NOT EXISTS staff_notes (
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
    """CREATE TABLE IF NOT EXISTS assignment_log (
        id          INTEGER PRIMARY KEY AUTOINCREMENT,
        period_id   INTEGER NOT NULL,
        employee_id INTEGER NOT NULL,
        date        TEXT NOT NULL,
        time_slot   TEXT NOT NULL,
        position    TEXT,
        action      TEXT NOT NULL,
        changed_at  TEXT NOT NULL DEFAULT (datetime('now','localtime'))
    )""",
    """CREATE TABLE IF NOT EXISTS cell_notes (
        id          INTEGER PRIMARY KEY AUTOINCREMENT,
        period_id   INTEGER NOT NULL REFERENCES schedule_periods(id) ON DELETE CASCADE,
        employee_id INTEGER NOT NULL,
        date        TEXT NOT NULL,
        note        TEXT NOT NULL DEFAULT '',
        UNIQUE(period_id, employee_id, date)
    )""",
    """CREATE TABLE IF NOT EXISTS customer_count_history (
        date      TEXT PRIMARY KEY,
        breakfast INTEGER NOT NULL DEFAULT 0,
        dinner    INTEGER NOT NULL DEFAULT 0
    )""",
    """CREATE TABLE IF NOT EXISTS weather_cache (
        date          TEXT PRIMARY KEY,
        temp_max      REAL,
        temp_min      REAL,
        precipitation REAL,
        weather_code  INTEGER
    )""",
    """CREATE TABLE IF NOT EXISTS google_tokens (
        key             TEXT PRIMARY KEY,
        credentials_json TEXT NOT NULL
    )""",
    """CREATE TABLE IF NOT EXISTS push_subscriptions (
        id         INTEGER PRIMARY KEY AUTOINCREMENT,
        endpoint   TEXT NOT NULL UNIQUE,
        p256dh     TEXT NOT NULL,
        auth       TEXT NOT NULL,
        created_at TEXT
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
    ("employees",         "hourly_wage",                "INTEGER NOT NULL DEFAULT 0"),
    ("employees",         "has_social_insurance",       "INTEGER NOT NULL DEFAULT 0"),
    ("employees",         "hall_skill_breakfast",       "TEXT NOT NULL DEFAULT 'beginner'"),
    ("employees",         "hall_skill_dinner",          "TEXT NOT NULL DEFAULT 'beginner'"),
    ("employees",         "kitchen_skill_breakfast",    "TEXT NOT NULL DEFAULT 'beginner'"),
    ("employees",         "kitchen_skill_dinner",       "TEXT NOT NULL DEFAULT 'beginner'"),
    ("reservation_counts", "is_special_day",            "INTEGER NOT NULL DEFAULT 0"),
    ("schedule_periods",  "google_form_id",             "TEXT"),
    ("employees",         "si_allow_short_shift",       "INTEGER NOT NULL DEFAULT 0"),
    ("schedule_periods",  "submission_deadline",        "TEXT DEFAULT NULL"),
]

_INDEXES = [
    "CREATE INDEX IF NOT EXISTS idx_shift_assignments_period ON shift_assignments(period_id)",
    "CREATE INDEX IF NOT EXISTS idx_shift_requests_period    ON shift_requests(period_id)",
    "CREATE INDEX IF NOT EXISTS idx_reservation_period       ON reservation_counts(period_id)",
    "CREATE INDEX IF NOT EXISTS idx_schedule_notes_period    ON schedule_notes(period_id)",
    "CREATE INDEX IF NOT EXISTS idx_staff_notes_period       ON staff_notes(period_id)",
    "CREATE INDEX IF NOT EXISTS idx_assignment_log_period     ON assignment_log(period_id)",
    "CREATE INDEX IF NOT EXISTS idx_cell_notes_period          ON cell_notes(period_id)",
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

    # 廃止された固定シフトパターン/固定不可日テーブルを既存DBから削除
    for table in ("fixed_patterns", "fixed_unavailable_dates"):
        try:
            conn.execute(f"DROP TABLE IF EXISTS {table}")
        except Exception:
            pass

    added_cols = set()
    for table, col, definition in _MIGRATIONS:
        try:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {col} {definition}")
            added_cols.add((table, col))
        except Exception:
            pass

    if conn.backend == "postgres":
        conn._conn.autocommit = False

    # 朝食/ディナー別習熟度フィールド新設時、旧 hall_skill/kitchen_skill の値を初期値としてコピー
    # （未設定のままだと自動生成が直後に infeasible になるため）
    if ("employees", "hall_skill_breakfast") in added_cols:
        try:
            conn.execute(
                "UPDATE employees SET hall_skill_breakfast = hall_skill,"
                " hall_skill_dinner = hall_skill,"
                " kitchen_skill_breakfast = kitchen_skill,"
                " kitchen_skill_dinner = kitchen_skill"
            )
        except Exception:
            pass

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

    # 既存データの can_open/can_cleanup 不整合を修正:
    # ディナー専任スタッフは朝食の開店準備・片付けを行わないため、
    # 修正（0ff7a0f）以前に保存された残存フラグを解消する
    try:
        conn.execute(
            "UPDATE employees SET can_open = 0, can_cleanup = 0"
            " WHERE primary_timeslot = 'dinner'"
            " AND (can_open = 1 OR can_cleanup = 1)"
        )
    except Exception:
        pass

    # 既存データの custom_start/custom_end 不整合を修正:
    # パターン変更で「カスタム」以外になったのに古い custom_start/custom_end が残っているレコードをクリア
    try:
        conn.execute(
            "UPDATE shift_requests SET custom_start=NULL, custom_end=NULL"
            " WHERE pattern_id != 'custom'"
            " AND (custom_start IS NOT NULL OR custom_end IS NOT NULL)"
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
        ("base_hourly_wage",           "1000"),
        ("early_morning_allowance",    "0"),
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
