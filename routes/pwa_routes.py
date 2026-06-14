from flask import Blueprint, render_template, make_response

bp = Blueprint("pwa", __name__)


@bp.get("/sw.js")
def service_worker():
    """Service Workerをルートパスで配信し、scopeをアプリ全体（/）にする"""
    resp = make_response(render_template("pwa/sw.js"))
    resp.headers["Content-Type"] = "application/javascript; charset=utf-8"
    resp.headers["Cache-Control"] = "no-cache"
    resp.headers["Service-Worker-Allowed"] = "/"
    return resp


@bp.get("/offline")
def offline():
    """オフライン時にService Workerが表示するフォールバックページ"""
    return render_template("pwa/offline.html")
