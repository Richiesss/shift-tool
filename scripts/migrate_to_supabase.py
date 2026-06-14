#!/usr/bin/env python
# -*- coding: utf-8 -*-

"""
SDU-Shift SQLite から Supabase (PostgreSQL) へのデータ移行スクリプト。

使い方:
    python scripts/migrate_to_supabase.py --sqlite-path /path/to/shift_tool.db --pg-url "postgresql://..."
"""

import os
import sys
import argparse
import sqlite3
import psycopg2
from pathlib import Path

# プロジェクトのルートディレクトリを検索パスに追加
project_root = Path(__file__).resolve().parents[1]
sys.path.append(str(project_root))

from db.database import initialize_db


def get_default_sqlite_path():
    """OSごとのデフォルトの SQLite ファイルパスを取得する"""
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "SDU-Shift" / "shift_tool.db"
    elif sys.platform == "win32":
        return Path(os.environ.get("APPDATA", str(Path.home()))) / "SDU-Shift" / "shift_tool.db"
    else:
        return Path.home() / ".shift_tool" / "shift_tool.db"


def main():
    parser = argparse.ArgumentParser(description="SDU-Shift SQLite to Supabase (PostgreSQL) Data Migrator")
    parser.add_argument(
        "--sqlite-path",
        type=str,
        default=str(get_default_sqlite_path()),
        help="移行元の SQLite データベースファイルへのパス"
    )
    parser.add_argument(
        "--pg-url",
        type=str,
        required=True,
        help="移行先の Supabase/PostgreSQL 接続文字列 (DATABASE_URL)"
    )
    parser.add_argument(
        "--yes",
        action="store_true",
        help="確認プロンプトをスキップする"
    )

    args = parser.parse_args()

    sqlite_path = Path(args.sqlite_path)
    pg_url = args.pg_url

    # 1. 接続確認と事前チェック
    if not sqlite_path.exists():
        print(f"Error: 移行元の SQLite ファイルが見つかりません: {sqlite_path}")
        sys.exit(1)

    print("=== SDU-Shift Supabase 移行ツール ===")
    print(f"移行元 (SQLite): {sqlite_path}")
    # パスワードなどをマスクして表示
    try:
        from urllib.parse import urlparse
        parsed = urlparse(pg_url)
        masked_pg_url = f"{parsed.scheme}://{parsed.username}:******@{parsed.hostname}:{parsed.port}{parsed.path}"
        print(f"移行先 (PostgreSQL): {masked_pg_url}")
    except Exception:
        print("移行先 (PostgreSQL): [接続文字列の解析に失敗しました]")

    if not args.yes:
        confirm = input("\n移行先の PostgreSQL データベースは初期化され、既存のデータはすべて削除されます。続行しますか？ (y/N): ")
        if confirm.lower() != 'y':
            print("移行をキャンセルしました。")
            sys.exit(0)

    # 2. PostgreSQL 側のテーブル初期化
    print("\n[1/4] PostgreSQL 側のデータベーススキーマを初期化中...")
    original_db_url = os.environ.get("DATABASE_URL")
    try:
        os.environ["DATABASE_URL"] = pg_url
        initialize_db()
        print("-> スキーマの初期化およびマイグレーションが完了しました。")
    except Exception as e:
        print(f"Error: PostgreSQL の初期化に失敗しました: {e}")
        sys.exit(1)
    finally:
        if original_db_url is not None:
            os.environ["DATABASE_URL"] = original_db_url
        else:
            os.environ.pop("DATABASE_URL", None)

    # 3. データの移行処理
    print("\n[2/4] データを移行中...")
    
    # 接続の確立
    try:
        sqlite_conn = sqlite3.connect(str(sqlite_path))
        sqlite_conn.row_factory = sqlite3.Row
        sqlite_cur = sqlite_conn.cursor()
    except Exception as e:
        print(f"Error: SQLite への接続に失敗しました: {e}")
        sys.exit(1)

    try:
        # psycopg2 の接続を確立
        # トランザクションプーラー経由でも確実に動作するように autocommit=False にする
        pg_conn = psycopg2.connect(pg_url)
        pg_cur = pg_conn.cursor()
    except Exception as e:
        print(f"Error: PostgreSQL への接続に失敗しました: {e}")
        sqlite_conn.close()
        sys.exit(1)

    # 移行対象のテーブル（外部キーの依存関係順にソート）
    # 親テーブルを先に移行し、子テーブルを後に移行する
    tables_to_migrate = [
        "employees",
        "schedule_periods",
        "shift_requests",
        "shift_assignments",
        "shift_constraints",
        "schedule_notes",
        "staff_notes",
        "reservation_counts",
        "app_settings",
        "breakfast_band_constraints",
        "assignment_log",
        "cell_notes",
        "customer_count_history",
        "weather_cache",
        "google_tokens"
    ]

    try:
        # PostgreSQL 側の既存データを CASCADE で TRUNCATE する
        print("-> PostgreSQL 側の既存データを削除中...")
        truncate_query = f"TRUNCATE TABLE {', '.join(tables_to_migrate)} CASCADE;"
        pg_cur.execute(truncate_query)
        pg_conn.commit()
        print("-> 既存データのクリーンアップが完了しました。")
    except Exception as e:
        pg_conn.rollback()
        print(f"Warning: テーブルの TRUNCATE に失敗しました。個別にDELETEを試みます: {e}")
        # 個別に DELETE を逆順で実行
        try:
            for table in reversed(tables_to_migrate):
                pg_cur.execute(f"DELETE FROM {table};")
            pg_conn.commit()
            print("-> 個別の DELETE によるクリーンアップが完了しました。")
        except Exception as delete_err:
            pg_conn.rollback()
            print(f"Error: データベースのクリアに失敗しました: {delete_err}")
            sqlite_conn.close()
            pg_conn.close()
            sys.exit(1)

    # 各テーブルの移行
    for table in tables_to_migrate:
        try:
            # SQLite から全データを取得
            sqlite_cur.execute(f"SELECT * FROM {table}")
            rows = sqlite_cur.fetchall()
            
            if not rows:
                print(f"-> {table}: 移行データなし (0件)")
                continue

            # カラム名の取得
            colnames = list(rows[0].keys())
            
            # PostgreSQL へのインサートクエリを作成
            # %s をプレースホルダとして使用する
            placeholders = ", ".join(["%s"] * len(colnames))
            columns_str = ", ".join(colnames)
            insert_query = f"INSERT INTO {table} ({columns_str}) VALUES ({placeholders})"
            
            # データ挿入
            inserted_count = 0
            for row in rows:
                values = [row[col] for col in colnames]
                pg_cur.execute(insert_query, values)
                inserted_count += 1
            
            pg_conn.commit()
            print(f"-> {table}: {inserted_count} 件のデータを移行しました。")

        except Exception as e:
            pg_conn.rollback()
            print(f"Error: {table} の移行中にエラーが発生しました: {e}")
            sqlite_conn.close()
            pg_conn.close()
            sys.exit(1)

    # 4. PostgreSQL のシーケンス（SEQUENCE）のリセット
    print("\n[3/4] PostgreSQL のシーケンス (ID 自動採番) を同期中...")
    # SERIAL / PRIMARY KEY AUTOINCREMENT で定義されたテーブル
    sequence_tables = [
        "employees",
        "schedule_periods",
        "shift_requests",
        "shift_assignments",
        "schedule_notes",
        "staff_notes",
        "reservation_counts",
        "assignment_log",
        "cell_notes"
    ]

    for table in sequence_tables:
        try:
            # シーケンスの最大値に基づいてシーケンスを更新
            # これをやらないと、新規登録時に重複キーエラーが発生する
            seq_query = f"""
                SELECT setval(
                    pg_get_serial_sequence('{table}', 'id'),
                    coalesce(max(id), 1),
                    max(id) IS NOT NULL
                ) FROM {table};
            """
            pg_cur.execute(seq_query)
            pg_conn.commit()
            print(f"-> {table} のシーケンスを同期しました。")
        except Exception as e:
            pg_conn.rollback()
            print(f"Warning: {table} のシーケンス同期中にエラーが発生しました: {e}")

    # 接続のクローズ
    sqlite_conn.close()
    pg_conn.close()

    print("\n[4/4] 移行処理が正常に完了しました！ 🎉")
    print("Supabase のダッシュボードでデータが正しく移行されていることを確認してください。")
    print("Web版を起動する際は、環境変数 DATABASE_URL に Supabase の接続文字列を設定してください。")


if __name__ == "__main__":
    main()
