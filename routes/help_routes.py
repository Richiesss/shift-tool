from flask import Blueprint, render_template

from utils.changelog import get_commit_log, get_known_issues

bp = Blueprint("help", __name__, url_prefix="/help")


@bp.get("/")
def index():
    return render_template(
        "help/index.html",
        commits=get_commit_log(),
        known_issues=get_known_issues(),
    )
