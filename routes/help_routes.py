from itertools import groupby

from flask import Blueprint, render_template

from utils.changelog import get_commit_log, get_known_issues

bp = Blueprint("help", __name__, url_prefix="/help")


@bp.get("/")
def index():
    commits = get_commit_log()
    changelog_groups = None
    if commits:
        changelog_groups = [
            {"date": date, "subjects": [c["subject"] for c in group]}
            for date, group in groupby(commits, key=lambda c: c["date"])
        ]
    return render_template(
        "help/index.html",
        commits=commits,
        changelog_groups=changelog_groups,
        known_issues=get_known_issues(),
    )
