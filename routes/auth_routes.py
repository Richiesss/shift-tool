import logging
from urllib.parse import urlparse
from flask import Blueprint, render_template, request, redirect, url_for, session, flash, make_response
from auth import APP_PASSWORD, SESSION_VERSION

logger = logging.getLogger(__name__)
bp = Blueprint("auth", __name__)


def _safe_next(url: str) -> str:
    """next パラメータを相対パスのみに限定してオープンリダイレクトを防ぐ"""
    parsed = urlparse(url)
    if parsed.scheme or parsed.netloc:
        return url_for("employees.index")
    return url or url_for("employees.index")


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
    raw_next = request.form.get("next") or request.args.get("next") or ""
    next_url = _safe_next(raw_next)

    logger.warning("LOGIN attempt: input_len=%d, expected_len=%d, match=%s",
                   len(pwd), len(APP_PASSWORD), pwd == APP_PASSWORD)

    if APP_PASSWORD and pwd == APP_PASSWORD:
        # session.clear() は使わず直接上書き（Flask ネイティブセッションで
        # clear() → 再設定のシーケンスが Cookie に正しく反映されないことがある）
        session["authenticated"] = True
        session["sv"] = SESSION_VERSION
        session.permanent = True
        resp = make_response(redirect(next_url))
        resp.headers["Cache-Control"] = "no-store"
        return resp

    flash("パスワードが違います", "error")
    return _login_page()


@bp.get("/logout")
def logout():
    session.pop("authenticated", None)
    session.pop("sv", None)
    session.permanent = False
    return redirect(url_for("auth.login"))
