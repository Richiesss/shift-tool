import sys
import sqlite3
import shutil
from pathlib import Path

# DB 保存先
#   macOS frozen : ~/Library/Application Support/SDU-Shift/shift_tool.db
#                  （App Translocation の影響を受けない常時書込可能な場所）
#   Windows frozen: EXEと同じフォルダの shift_tool.db
#   開発時        : ~/.shift_tool/shift_tool.db
if getattr(sys, "frozen", False):
    if sys.platform == "darwin":
        DB_PATH = (Path.home() / "Library" / "Application Support"
                   / "SDU-Shift" / "shift_tool.db")
    else:
        DB_PATH = Path(sys.executable).parent / "shift_tool.db"
else:
    DB_PATH = Path.home() / ".shift_tool" / "shift_tool.db"


def _seed_if_needed() -> None:
    """初回起動時: バンドル内のシードDBを DB_PATH にコピーする。"""
    if DB_PATH.exists():
        return
    if not hasattr(sys, "_MEIPASS"):
        return
    seed = Path(sys._MEIPASS) / "shift_tool.db"
    if seed.exists():
        DB_PATH.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(seed, DB_PATH)
        print(f"[DB] シードDBをコピー: {DB_PATH}", flush=True)


def get_connection() -> sqlite3.Connection:
    _seed_if_needed()
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def initialize_db():
    conn = get_connection()
    cur = conn.cursor()

    cur.executescript("""
        CREATE TABLE IF NOT EXISTS employees (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            name            TEXT NOT NULL,
            employment_type TEXT NOT NULL CHECK(employment_type IN ('full_time','part_time')),
            hall_skill      TEXT NOT NULL DEFAULT 'beginner'
                          CHECK(hall_skill IN ('leader','veteran','general','beginner')),
            kitchen_skill   TEXT NOT NULL DEFAULT 'beginner'
                          CHECK(kitchen_skill IN ('leader','veteran','general','beginner')),
            is_active       INTEGER NOT NULL DEFAULT 1
        );

        CREATE TABLE IF NOT EXISTS fixed_patterns (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            employee_id INTEGER NOT NULL REFERENCES employees(id) ON DELETE CASCADE,
            day_of_week INTEGER NOT NULL CHECK(day_of_week BETWEEN 0 AND 6),
            breakfast   INTEGER NOT NULL DEFAULT 0,
            dinner      INTEGER NOT NULL DEFAULT 0,
            UNIQUE(employee_id, day_of_week)
        );

        CREATE TABLE IF NOT EXISTS fixed_unavailable_dates (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            employee_id INTEGER NOT NULL REFERENCES employees(id) ON DELETE CASCADE,
            date        TEXT NOT NULL,
            UNIQUE(employee_id, date)
        );

        CREATE TABLE IF NOT EXISTS schedule_periods (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            start_date  TEXT NOT NULL,
            end_date    TEXT NOT NULL,
            status      TEXT NOT NULL DEFAULT 'draft'
                          CHECK(status IN ('draft','confirmed')),
            created_at  TEXT NOT NULL DEFAULT (datetime('now','localtime')),
            UNIQUE(start_date)
        );

        CREATE TABLE IF NOT EXISTS shift_requests (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            period_id   INTEGER NOT NULL REFERENCES schedule_periods(id) ON DELETE CASCADE,
            employee_id INTEGER NOT NULL REFERENCES employees(id) ON DELETE CASCADE,
            date        TEXT NOT NULL,
            breakfast   INTEGER NOT NULL DEFAULT 0,
            dinner      INTEGER NOT NULL DEFAULT 0,
            note        TEXT DEFAULT '',
            UNIQUE(period_id, employee_id, date)
        );

        CREATE TABLE IF NOT EXISTS shift_assignments (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            period_id   INTEGER NOT NULL REFERENCES schedule_periods(id) ON DELETE CASCADE,
            employee_id INTEGER NOT NULL REFERENCES employees(id) ON DELETE CASCADE,
            date        TEXT NOT NULL,
            time_slot   TEXT NOT NULL CHECK(time_slot IN ('breakfast','dinner')),
            position    TEXT NOT NULL CHECK(position IN ('hall','kitchen')),
            UNIQUE(period_id, employee_id, date, time_slot)
        );
    """)

    # shift_requests テーブルにパターン関連カラムを追加（マイグレーション）
    for col, definition in [
        ("pattern_id",   "TEXT"),
        ("custom_start", "TEXT"),
        ("custom_end",   "TEXT"),
    ]:
        try:
            cur.execute(f"ALTER TABLE shift_requests ADD COLUMN {col} {definition}")
        except Exception:
            pass  # 既に存在する場合は無視

    conn.commit()
    conn.close()
