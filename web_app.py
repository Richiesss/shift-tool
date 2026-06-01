"""Flask Web Application Entry Point"""
import sys
import os
sys.path.insert(0, os.path.dirname(__file__))

from datetime import timedelta
from flask import Flask, g, session, redirect, url_for, request
from flask_compress import Compress
from flask_session import Session as FlaskSession
from cache import cache
from db.database import initialize_db
from auth import APP_PASSWORD, SESSION_VERSION


def create_app():
    app = Flask(__name__, template_folder="templates", static_folder="static")
    app.secret_key = os.environ.get("SECRET_KEY", "dev-secret-key-change-in-prod")
    Compress(app)
    cache.init_app(app, config={"CACHE_TYPE": "SimpleCache", "CACHE_DEFAULT_TIMEOUT": 180})

    # サーバーサイドセッション（ファイルストア）
    # Cookie にはセッションIDのみ保存 → プロキシ/マルチワーカー環境でも安定動作
    os.makedirs("/tmp/flask_sessions", exist_ok=True)
    app.config.update(
        SESSION_TYPE="filesystem",
        SESSION_FILE_DIR="/tmp/flask_sessions",
        SESSION_FILE_THRESHOLD=500,
        SESSION_PERMANENT=True,
        PERMANENT_SESSION_LIFETIME=timedelta(days=30),
        SESSION_USE_SIGNER=True,
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Lax",
    )
    FlaskSession(app)

    initialize_db()

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
        exempt = {"auth.login", "auth.login_post", "static"}
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

    @app.get("/")
    def index():
        from flask import redirect, url_for
        return redirect(url_for("employees.index"))

    return app


if __name__ == "__main__":
    app = create_app()
    app.run(debug=True, host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))
