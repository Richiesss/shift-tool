"""Flask Web Application Entry Point"""
import sys
import os
sys.path.insert(0, os.path.dirname(__file__))

import logging
from datetime import timedelta
from flask import Flask, g, session, redirect, url_for, request, flash, jsonify
from flask_compress import Compress
from flask_session import Session as FlaskSession
from flask_wtf.csrf import CSRFProtect, CSRFError
from cache import cache
from db.database import initialize_db
from auth import APP_PASSWORD, SESSION_VERSION

logger = logging.getLogger(__name__)


def create_app():
    app = Flask(__name__, template_folder="templates", static_folder="static")

    # リバースプロキシ背後でのプロキシヘッダー（X-Forwarded-Protoなど）を正しく認識させる
    from werkzeug.middleware.proxy_fix import ProxyFix
    app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1, x_prefix=1)

    is_dev = os.environ.get("FLASK_ENV") == "development" or os.environ.get("FLASK_DEBUG") == "1"

    secret_key = os.environ.get("SECRET_KEY")
    if not secret_key:
        if not is_dev:
            import secrets
            logger.warning(
                "SECRET_KEY が未設定のため、ランダムなキーを自動生成しました。"
                "サーバーの再起動ごとにセッションが失われるため、本番環境では必ず環境変数 SECRET_KEY を設定してください。"
            )
            secret_key = secrets.token_hex(32)
        else:
            secret_key = "dev-secret-key-change-in-prod"
    app.secret_key = secret_key
    Compress(app)
    cache.init_app(app, config={"CACHE_TYPE": "SimpleCache", "CACHE_DEFAULT_TIMEOUT": 180})

    # CSRF対策（長時間開いた画面からの送信もエラーにしないようトークンの有効期限は設けない）
    app.config["WTF_CSRF_TIME_LIMIT"] = None
    CSRFProtect(app)

    # サーバーサイドセッション（ファイルストア）
    # Flask-Session 0.8.x では SESSION_PERMANENT / SESSION_USE_SIGNER は廃止
    os.makedirs("/tmp/flask_sessions", exist_ok=True)
    app.config.update(
        SESSION_TYPE="filesystem",
        SESSION_FILE_DIR="/tmp/flask_sessions",
        SESSION_FILE_THRESHOLD=500,
        PERMANENT_SESSION_LIFETIME=timedelta(days=30),
        SESSION_COOKIE_SECURE=not is_dev,
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
    from routes.dashboard import bp as dashboard_bp
    app.register_blueprint(auth_bp)
    app.register_blueprint(dashboard_bp)

    @app.context_processor
    def inject_auth():
        from utils.version import get_display_version
        return {"auth_enabled": bool(APP_PASSWORD), "app_version": get_display_version()}

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

    @app.after_request
    def add_security_headers(response):
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "SAMEORIGIN"
        response.headers["X-XSS-Protection"] = "1; mode=block"
        return response

    @app.errorhandler(CSRFError)
    def handle_csrf_error(e):
        if request.is_json:
            return jsonify(error="セッションが無効です。ページを再読み込みしてください。"), 400
        flash("セッションが切れたため操作を完了できませんでした。もう一度お試しください。", "warning")
        return redirect(request.referrer or url_for("dashboard.index"))

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

    # / は dashboard blueprint が処理するため不要

    return app


if __name__ == "__main__":
    app = create_app()
    app.run(debug=True, host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))
