# CLAUDE.md

## プロジェクト概要

SDU-Shift は飲食店向けのシフト表自動生成ツール。Flask製のWeb版（`web_app.py` の `create_app()` がエントリーポイント）のみで運用する。詳しい機能・画面仕様は README.md を参照。

---

## アーキテクチャ / ディレクトリ構成

| パス | 役割 |
|---|---|
| `web_app.py` | エントリーポイント。`create_app()` でFlaskアプリを生成しBlueprintを登録 |
| `auth.py` | 共有パスワード認証（`login_required`デコレータ） |
| `cache.py` | Flask-Caching インスタンス |
| `db/database.py` | DB接続層。`Connection`クラスがSQLite/PostgreSQLの両方を抽象化 |
| `db/repositories.py` | データアクセス関数群（CRUD・集計クエリ） |
| `db/seeder.py` / `db/seed_data.json` | 初期データ投入 |
| `models/` | ドメインモデル（`Employee`, `ShiftRequest`, `ShiftAssignment`, `SchedulePeriod` などのdataclass） |
| `optimizer/solver.py` | CP-SAT (OR-Tools) によるシフト最適化エンジン本体 |
| `export/` | Excel (`openpyxl`) / PDF (`reportlab`) 出力 |
| `routes/` | Flask Blueprint群（employees, shifts, generate, schedule, customers, settings, export, dashboard, auth, help, feedback） |
| `templates/`, `static/` | テンプレート・静的ファイル |
| `utils/` | 共通定数（`constants.py`）、シフトパターン定義（`shift_patterns.py`）、祝日判定、バージョン、ソルバーログ、GitHub連携（`changelog.py`＝Issues/コミット取得、`github_issues.py`＝Issue作成）など |
| `scripts/seed_test_data.py` | 動作確認用のテストデータ投入スクリプト |
| `scripts/ShiftFormGenerator.gs` | 希望シフト収集用Google Formsと連携するApps Script |

---

## 開発・実行コマンド

### 開発サーバー
```bash
pip install -r requirements.txt
python web_app.py
```
`http://localhost:5000` で起動（`debug=True`）。`DATABASE_URL`未設定時はSQLiteにフォールバック。

### 本番相当
```bash
gunicorn 'web_app:create_app()' --bind 0.0.0.0:$PORT --workers 1 --worker-class gthread --threads 4 --timeout 120 --max-requests 500 --max-requests-jitter 50 --access-logfile - --error-logfile -
```
`--max-requests`はワーカーを一定リクエスト数ごとに再起動させ、メモリ肥大やキャッシュの蓄積をリセットする（`--max-requests-jitter`で再起動タイミングをずらす）。`--access-logfile -`/`--error-logfile -`で標準出力にログを出し、Render/HF Spacesのログビューアで追跡できるようにする。
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

### Cloud Run デプロイ（本番環境）
```bash
gcloud run deploy shift-tool \
  --source . \
  --region us-east1 \
  --min-instances 0 \
  --max-instances 1 \
  --cpu 4 \
  --memory 2Gi \
  --timeout 300 \
  --allow-unauthenticated \
  --set-env-vars SOLVER_WORKERS=4,DISABLE_STATEMENT_TIMEOUT=true \
  --set-secrets SECRET_KEY=shift-tool-secret-key:latest,DATABASE_URL=shift-tool-db-url:latest,APP_PASSWORD=shift-tool-app-password:latest,GITHUB_TOKEN=shift-tool-github-token:latest
```
- GCPプロジェクト: `shift-tool-1d0b52`（リージョン `us-east1`、無料枠対象）
- 公開URL: `https://sdu-shift.duckdns.org`（Cloud Runのドメインマッピング機能で紐付け。DNSはDuckDNSで管理、A/AAAAレコードをGoogle指定のIPに設定）
- 各シークレットはSecret Managerで管理（`shift-tool-secret-key` / `shift-tool-db-url` / `shift-tool-app-password` / `shift-tool-github-token`）。値を更新した場合は `gcloud secrets versions add <name> --data-file=-` で新バージョンを追加した後、上記デプロイコマンドを再実行しないと反映されない（`:latest`はデプロイ時点のバージョンに固定されるため）
- `--max-instances 1`: `cache.py`の`SimpleCache`がプロセスローカルなため、複数インスタンス化によるキャッシュ不整合を避ける暫定策
- `.gcloudignore`で`#!include:.gitignore`のみを指定し、`.git`ディレクトリをあえて除外していない。`utils/changelog.py`の「更新履歴」表示が`git log`コマンドに依存しているため（gcloudがデフォルト生成する`.gcloudignore`は`.git`を自動除外してしまい、更新履歴が空になる）
- DBはSupabase (PostgreSQL)。移行手順は後述の「SQLite から Supabase へのデータ移行」を参照

---

## コーディング規約・注意点

- **DB抽象化に従う**: `db/database.py` の `Connection` はSQLiteとPostgreSQL両対応。生のSQLは**SQLite方言**で書く（`?`プレースホルダ、`INTEGER PRIMARY KEY AUTOINCREMENT`など）。`_to_pg()`が自動的にPostgreSQL構文へ変換するため、PostgreSQL固有の構文を直接書かない。
- **環境変数**:
  - `DATABASE_URL` — 設定時はPostgreSQL（Render/Supabase）、未設定時はSQLite（HF Spaces）
  - `DISABLE_STATEMENT_TIMEOUT` — Supabase等のトランザクションプール経由で接続する場合に `true` に設定し、SET statement_timeout によるエラーを防止する
  - `APP_PASSWORD` — Web版の共有パスワード認証。空文字なら認証自体が無効になる
  - `SECRET_KEY` — Flaskセッション用シークレット
  - `SOLVER_LOG_PATH` — ソルバーのログ出力先（`utils/solver_logger.py`）
  - `GITHUB_TOKEN` — ヘルプ画面「既知の不具合」取得（`utils/changelog.py`）と「フィードバック」フォームからのIssue作成（`utils/github_issues.py`）に使用。Issue作成にはrepo権限を持つトークンが必須（未設定だとフィードバック送信が常に失敗する）。未設定でも一覧取得は動くが匿名レート制限（60回/時間）が適用される
- **本番環境は2026-07-04時点でCloud Runに移行済み**（`https://sdu-shift.duckdns.org`、DBはSupabase）。レスポンス速度改善のためHF Spacesから移行した。HF Spacesは当面Pause状態で残し、問題があればすぐ切り戻せるようにしている（完全停止・Persistent Storage解約は運用が安定してから）。`render.yaml`はRenderへのデプロイ設定として残っているが未使用（サービス自体が存在しない）。DBはPostgreSQL/SQLite両対応のまま残しているため、DB方言に依存する変更を行う場合は注意すること。
- **コメント・docstring・コミットメッセージは日本語**で統一する。
- **自動テストスイートは無し**。`scripts/seed_test_data.py` でテストデータを投入し、Web版で手動確認する。
- **ソルバー変更時**: `optimizer/solver.py` の優先度は `SolverConfig` と `PRIORITY_SCALE`（低=0.1/中=1.0/高=10.0）で調整する。`utils/solver_logger.py` がスタッフ別の割当根拠・ペナルティ内訳をログ出力するため、制約変更後は必ずログで影響を確認する。

---

## Git ワークフロー

コード変更後は必ずコミット＆プッシュをセットで行う。

```bash
git add <変更ファイル>
git commit -m "..."
git push origin main
# HF へのデプロイは下記「HuggingFace Spaces デプロイ」を参照
```

リモートは2つある：
- `origin` — GitHub (https://github.com/Richiesss/shift-tool.git)
- `hf` — HuggingFace Spaces (本番環境)

### HuggingFace Spaces デプロイ

HF は Git LFS / バイナリファイルを拒否するため、**`hf-deploy` ブランチ**を経由してデプロイする。

```bash
# hf-deploy ブランチに移動して最新 main から差分を cherry-pick
git checkout hf-deploy
git cherry-pick <コミットハッシュ>   # バイナリ追加コミットは除外
git push hf hf-deploy:main

# 完了後 main に戻る
git checkout main
```

`static/help/*.jpg` は GitHub (origin) 側に Git LFS で管理し、テンプレートは
`https://raw.githubusercontent.com/Richiesss/shift-tool/main/static/help/%23N.jpg`
の GitHub raw URL で参照する（HF 側にバイナリを含めない）。
