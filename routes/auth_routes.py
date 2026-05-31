import logging
from flask import Blueprint, render_template, request, redirect, url_for, session, flash, make_response
from auth import APP_PASSWORD, SESSION_VERSION

logger = logging.getLogger(__name__)
bp = Blueprint("auth", __name__)


def _login_page(**kwargs):
    """キャッシュ無効ヘッダー付きでログインページを返す"""
    resp = make_response(render_template("auth/login.html", pw_len=len(APP_PASSWORD), **kwargs))
    resp.headers["Cache-Control"] = "no-store, no-cache, must-revalidate"
    resp.headers["Pragma"] = "no-cache"
    return resp


@bp.get("/login")
def login():
    if session.get("authenticated") and session.get("sv") == SESSION_VERSION:
        return redirect(url_for("employees.index"))
    return _login_page()


@bp.post("/login")
def login_post():
    pwd = request.form.get("password", "").strip()
    # next は hidden フィールドでも query param でも受け取る
    next_url = request.form.get("next") or request.args.get("next") or url_for("employees.index")

    logger.warning("LOGIN attempt: input_len=%d, expected_len=%d, match=%s",
                   len(pwd), len(APP_PASSWORD), pwd == APP_PASSWORD)

    if APP_PASSWORD and pwd == APP_PASSWORD:
        session.clear()
        session["authenticated"] = True
        session["sv"] = SESSION_VERSION
        session.permanent = True
        return redirect(next_url)

    flash(f"パスワードが違います（入力: {len(pwd)}文字 / 設定: {len(APP_PASSWORD)}文字）", "error")
    return _login_page()


@bp.get("/logout")
def logout():
    session.clear()
    return redirect(url_for("auth.login"))
