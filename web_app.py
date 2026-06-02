"""Flask Web Application Entry Point"""
import sys
import os
sys.path.insert(0, os.path.dirname(__file__))

from datetime import timedelta
from flask import Flask, g, session, redirect, url_for, request
from flask_compress import Compress
from cache import cache
from db.database import initialize_db
from auth import APP_PASSWORD, SESSION_VERSION


def create_app():
    app = Flask(__name__, template_folder="templates", static_folder="static")
    app.secret_key = os.environ.get("SECRET_KEY", "dev-secret-key-change-in-prod")
    Compress(app)
    cache.init_app(app, config={"CACHE_TYPE": "SimpleCache", "CACHE_DEFAULT_TIMEOUT": 180})

    # クライアント側セッション（Cookieに署名して保存）
    # --workers 1 なのでサーバー側同期不要。Flask標準セッションで十分。
    app.config.update(
        PERMANENT_SESSION_LIFETIME=timedelta(days=30),
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Lax",
    )

    try:
        initialize_db()
    except Exception as _e:
        import logging
        logging.getLogger(__name__).error(f"initialize_db failed: {_e}")

    # クラッシュ/リロード後に "generating" のままスタックした状態をリセット
    try:
        from db.database import Connection as _Conn
        _c = _Conn()
        _c.execute(
            "UPDATE schedule_periods SET gen_status='idle', gen_message='' WHERE gen_status='generating'"
        )
        # output_position と primary_position の不整合を修正
        # (単一ポジション従業員の output_position が誤った値になっているケース)
        _c.execute(
            "UPDATE employees SET output_position = primary_position"
            " WHERE primary_position IS NOT NULL"
            " AND (output_position IS NULL OR output_position != primary_position)"
        )
        _c.commit()
        _c.close()
    except Exception:
        pass

    from routes.auth_routes import bp as auth_bp
    app.register_blueprint(auth_bp)

    @app.context_processor
    def inject_auth():
        return {"auth_enabled": bool(APP_PASSWORD)}

    # 認証チェック（ログイン・静的ファイルは除外）
    @app.before_request
    def require_login():
        if not APP_PASSWORD:
            return  # パスワード未設定なら全開放
        exempt = {"auth.login", "auth.login_post", "static", "health"}
        if request.endpoint in exempt:
            return
        if not session.get("authenticated") or session.get("sv") != SESSION_VERSION:
            session.clear()
            return redirect(url_for("auth.login", next=request.path))

    from routes.employees import bp as emp_bp
    from routes.shifts import bp as shifts_bp
    from routes.generate import bp as gen_bp
    from routes.schedule import bp as sched_bp
    from routes.customers import bp as cust_bp
    from routes.settings_routes import bp as settings_bp
    from routes.export_routes import bp as export_bp
    from routes.help_routes import bp as help_bp

    app.register_blueprint(emp_bp)
    app.register_blueprint(shifts_bp)
    app.register_blueprint(gen_bp)
    app.register_blueprint(sched_bp)
    app.register_blueprint(cust_bp)
    app.register_blueprint(settings_bp)
    app.register_blueprint(export_bp)
    app.register_blueprint(help_bp)

    @app.teardown_appcontext
    def close_db(error):
        conn = g.pop("_db_conn", None)
        if conn is not None:
            conn._flask_managed = False
            conn.close()

    @app.get("/health")
    def health():
        from flask import jsonify
        return jsonify({"status": "ok"})

    @app.errorhandler(500)
    def internal_error(e):
        import traceback, logging
        logging.getLogger(__name__).error(
            f"500 Internal Server Error: {e}\n{traceback.format_exc()}"
        )
        from flask import render_template_string
        return render_template_string("""
<!doctype html><html lang="ja"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>エラー - SDU-Shift</title>
<link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.3/dist/css/bootstrap.min.css">
</head><body style="background:#0f172a;color:white;min-height:100vh;display:flex;align-items:center;justify-content:center">
<div class="text-center p-4">
  <i class="bi bi-exclamation-triangle-fill" style="font-size:3rem;color:#f59e0b"></i>
  <h2 class="mt-3">サーバーエラーが発生しました</h2>
  <p class="text-muted">しばらく待ってから再度お試しください。</p>
  <details class="text-start mt-3" style="max-width:600px;margin:0 auto">
    <summary style="cursor:pointer;color:#94a3b8;font-size:.85rem">エラー詳細</summary>
    <pre style="font-size:.75rem;color:#ef4444;background:#1e293b;padding:1rem;border-radius:8px;margin-top:.5rem;overflow:auto">{{ err }}</pre>
  </details>
  <a href="/" class="btn btn-primary mt-4">トップへ戻る</a>
</div>
</body></html>
        """, err=str(e)), 500

    @app.get("/")
    def index():
        from flask import redirect, url_for
        return redirect(url_for("employees.index"))

    return app


if __name__ == "__main__":
    app = create_app()
    app.run(debug=True, host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))
