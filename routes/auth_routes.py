import logging
from flask import Blueprint, render_template, request, redirect, url_for, session, flash
from auth import APP_PASSWORD, SESSION_VERSION

logger = logging.getLogger(__name__)
bp = Blueprint("auth", __name__)


@bp.get("/login")
def login():
    if session.get("authenticated") and session.get("sv") == SESSION_VERSION:
        return redirect(url_for("employees.index"))
    return render_template("auth/login.html")


@bp.post("/login")
def login_post():
    pwd = request.form.get("password", "").strip()
    next_url = request.args.get("next") or url_for("employees.index")
    # デバッグログ（パスワード本体は出力しない）
    logger.warning("LOGIN attempt: input_len=%d, expected_len=%d, match=%s",
                   len(pwd), len(APP_PASSWORD), pwd == APP_PASSWORD)
    if APP_PASSWORD and pwd == APP_PASSWORD:
        session.clear()
        session["authenticated"] = True
        session["sv"] = SESSION_VERSION
        session.permanent = True
        return redirect(next_url)
    flash(f"パスワードが違います（入力: {len(pwd)}文字 / 設定: {len(APP_PASSWORD)}文字）", "error")
    return render_template("auth/login.html")


@bp.get("/logout")
def logout():
    session.clear()
    return redirect(url_for("auth.login"))
