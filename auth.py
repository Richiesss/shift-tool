"""共有パスワード認証モジュール"""
import os
from functools import wraps
from flask import session, redirect, url_for, request

APP_PASSWORD = os.environ.get("APP_PASSWORD", "")


def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if not APP_PASSWORD:
            return f(*args, **kwargs)  # パスワード未設定なら認証スキップ
        if not session.get("authenticated"):
            return redirect(url_for("auth.login", next=request.path))
        return f(*args, **kwargs)
    return decorated
