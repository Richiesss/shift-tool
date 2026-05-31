from flask import Blueprint, render_template

bp = Blueprint("help", __name__, url_prefix="/help")


@bp.get("/")
def index():
    return render_template("help/index.html")
