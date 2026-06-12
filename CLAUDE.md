# CLAUDE.md

## プロジェクト概要

SDU-Shift は飲食店向けのシフト表自動生成ツール。同じバックエンド（DB・モデル・最適化エンジン・出力処理）を、

- **デスクトップ版**（PyQt6、`main.py` がエントリーポイント）
- **Web版**（Flask、`web_app.py` の `create_app()` がエントリーポイント）

の2つのインターフェースから利用する構成になっている。詳しい機能・画面仕様は README.md を参照。

---

## アーキテクチャ / ディレクトリ構成

| パス | 役割 |
|---|---|
| `main.py` | デスクトップ版エントリーポイント（PyQt6アプリ起動） |
| `web_app.py` | Web版エントリーポイント。`create_app()` でFlaskアプリを生成しBlueprintを登録 |
| `auth.py` | Web版の共有パスワード認証（`login_required`デコレータ） |
| `cache.py` | Flask-Caching インスタンス |
| `db/database.py` | DB接続層。`Connection`クラスがSQLite/PostgreSQLの両方を抽象化 |
| `db/repositories.py` | データアクセス関数群（CRUD・集計クエリ） |
| `db/seeder.py` / `db/seed_data.json` | 初期データ投入 |
| `models/` | ドメインモデル（`Employee`, `ShiftRequest`, `ShiftAssignment`, `SchedulePeriod` などのdataclass） |
| `optimizer/solver.py` | CP-SAT (OR-Tools) によるシフト最適化エンジン本体 |
| `export/` | Excel (`openpyxl`) / PDF (`reportlab`) 出力 |
| `routes/` | Web版のFlask Blueprint群（employees, shifts, generate, schedule, customers, settings, export, dashboard, auth, help） |
| `ui/` | デスクトップ版のPyQt6画面 |
| `templates/`, `static/`, `assets/` | Web版テンプレート・静的ファイル／アイコン等 |
| `utils/` | 共通定数（`constants.py`）、シフトパターン定義（`shift_patterns.py`）、祝日判定、テーマ、バージョン、ソルバーログなど |
| `scripts/seed_test_data.py` | 動作確認用のテストデータ投入スクリプト |
| `scripts/ShiftFormGenerator.gs` | 希望シフト収集用Google Formsと連携するApps Script |
| `shift_tool.spec` | PyInstallerビルド設定（Windows EXE / macOS .app） |
| `.github/workflows/build-windows.yml` | mainプッシュ時にWindows/macOS向けビルド＆リリースを自動実行 |

---

## 開発・実行コマンド

### デスクトップ版
```bash
pip install -r requirements.txt
python main.py
```
DBは `~/.shift_tool/shift_tool.db`（開発環境）に自動作成される。

### Web版（開発サーバー）
```bash
pip install -r requirements-web.txt
python web_app.py
```
`http://localhost:5000` で起動（`debug=True`）。`DATABASE_URL`未設定時はSQLiteにフォールバック。

### Web版（本番相当）
```bash
gunicorn 'web_app:create_app()' --bind 0.0.0.0:$PORT --workers 1 --worker-class gthread --threads 4 --timeout 120
```
※ Flask-Caching の `SimpleCache` はプロセスローカルのため、`workers` を2以上にすると
ワーカー間でキャッシュ無効化（`delete_memoized`）が伝播せず古いデータが表示される（#37）。
並列度はワーカー数ではなく `--threads` で確保すること。

### テストデータ投入
```bash
python scripts/seed_test_data.py           # データを追加
python scripts/seed_test_data.py --reset   # 全データ削除後に追加
```

### SQLite から Supabase (PostgreSQL) へのデータ移行
```bash
python scripts/migrate_to_supabase.py --pg-url "postgresql://postgres.[username]:[password]@db.[project-ref].supabase.co:5432/postgres"
```
※ `--sqlite-path` で任意の SQLite ファイルパスを指定可能。移行後は自動的に PostgreSQL 側の SERIAL シーケンスが同期される。


### Dockerビルド（HuggingFace Spaces向け）
`Dockerfile` を使用、`app_port: 8080`。

---

## コーディング規約・注意点

- **DB抽象化に従う**: `db/database.py` の `Connection` はSQLiteとPostgreSQL両対応。生のSQLは**SQLite方言**で書く（`?`プレースホルダ、`INTEGER PRIMARY KEY AUTOINCREMENT`など）。`_to_pg()`が自動的にPostgreSQL構文へ変換するため、PostgreSQL固有の構文を直接書かない。
- **環境変数**:
  - `DATABASE_URL` — 設定時はPostgreSQL（Render/Supabase）、未設定時はSQLite（デスクトップ/HF Spaces）
  - `DISABLE_STATEMENT_TIMEOUT` — Supabase等のトランザクションプール経由で接続する場合に `true` に設定し、SET statement_timeout によるエラーを防止する
  - `APP_PASSWORD` — Web版の共有パスワード認証。空文字なら認証自体が無効になる
  - `SECRET_KEY` — Flaskセッション用シークレット
  - `SOLVER_LOG_PATH` — ソルバーのログ出力先（`utils/solver_logger.py`）
- **デプロイ先が2系統ある**: HuggingFace Spaces（Docker、SQLite前提）とGitHub→Render（PostgreSQL）。DB方言に依存する変更は両方で動作確認する。
- **コメント・docstring・コミットメッセージは日本語**で統一する。
- **自動テストスイートは無し**。`scripts/seed_test_data.py` でテストデータを投入し、デスクトップ/Web両方で手動確認する。
- **ソルバー変更時**: `optimizer/solver.py` の優先度は `SolverConfig` と `PRIORITY_SCALE`（低=0.1/中=1.0/高=10.0）で調整する。`utils/solver_logger.py` がスタッフ別の割当根拠・ペナルティ内訳をログ出力するため、制約変更後は必ずログで影響を確認する。

---

## Git ワークフロー

コード変更後は必ずコミット＆プッシュをセットで行う。

```bash
git add <変更ファイル>
git commit -m "..."
git push origin main
git push hf main
```

リモートは2つある：
- `origin` — GitHub (https://github.com/Richiesss/shift-tool.git)
- `hf` — HuggingFace Spaces (本番環境)
