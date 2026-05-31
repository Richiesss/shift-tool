"""共有パスワード認証モジュール"""
import os
from functools import wraps
from flask import session, redirect, url_for, request

# 末尾スペース・改行を除去して比較ミスを防ぐ
APP_PASSWORD = os.environ.get("APP_PASSWORD", "").strip()

# セッション無効化用バージョン — 上げると全デバイスの既存セッションが失効する
SESSION_VERSION = 1


def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if not APP_PASSWORD:
            return f(*args, **kwargs)  # パスワード未設定なら認証スキップ
        if not session.get("authenticated") or session.get("sv") != SESSION_VERSION:
            session.clear()
            return redirect(url_for("auth.login", next=request.path))
        return f(*args, **kwargs)
    return decorated
