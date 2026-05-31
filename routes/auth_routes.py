from flask import Blueprint, render_template, request, redirect, url_for, session, flash
from auth import APP_PASSWORD

bp = Blueprint("auth", __name__)


@bp.get("/login")
def login():
    if session.get("authenticated"):
        return redirect(url_for("employees.index"))
    return render_template("auth/login.html")


@bp.post("/login")
def login_post():
    pwd = request.form.get("password", "")
    next_url = request.args.get("next") or url_for("employees.index")
    if APP_PASSWORD and pwd == APP_PASSWORD:
        session["authenticated"] = True
        session.permanent = True
        return redirect(next_url)
    flash("パスワードが違います", "error")
    return render_template("auth/login.html", next=next_url)


@bp.get("/logout")
def logout():
    session.clear()
    return redirect(url_for("auth.login"))
