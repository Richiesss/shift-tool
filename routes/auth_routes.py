from flask import Blueprint, render_template, request, redirect, url_for, session, flash
from auth import APP_PASSWORD, SESSION_VERSION

bp = Blueprint("auth", __name__)


@bp.get("/login")
def login():
    if session.get("authenticated") and session.get("sv") == SESSION_VERSION:
        return redirect(url_for("employees.index"))
    return render_template("auth/login.html")


@bp.post("/login")
def login_post():
    # 入力パスワードも strip して比較
    pwd = request.form.get("password", "").strip()
    next_url = request.args.get("next") or url_for("employees.index")
    if APP_PASSWORD and pwd == APP_PASSWORD:
        session.clear()
        session["authenticated"] = True
        session["sv"] = SESSION_VERSION
        session.permanent = True
        return redirect(next_url)
    flash("パスワードが違います", "error")
    return render_template("auth/login.html")


@bp.get("/logout")
def logout():
    session.clear()
    return redirect(url_for("auth.login"))
