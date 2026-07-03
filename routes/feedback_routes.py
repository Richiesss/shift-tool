from flask import Blueprint, render_template, request, redirect, url_for, flash

from cache import cache
from utils.changelog import get_known_issues
from utils.github_issues import create_issue, GitHubIssueError

bp = Blueprint("feedback", __name__, url_prefix="/feedback")

# (GitHub labels, 表示名, 入力欄のガイド文)
TYPE_INFO = {
    "bug": ("bug", "不具合報告", "どの画面で何をしたら、どうなったか（再現手順）と、本来どうなるべきか（期待する動作）を書いてください。"),
    "feature": ("enhancement", "機能要望", "どんな場面で困っていて、どう改善されると嬉しいかを具体的に書いてください。"),
}

MIN_TITLE_LEN = 4
MIN_DETAIL_LEN = 20


@bp.get("/")
def index():
    return render_template("feedback/index.html", type_info=TYPE_INFO)


@bp.post("/submit")
def submit():
    feedback_type = request.form.get("type", "bug")
    if feedback_type not in TYPE_INFO:
        feedback_type = "bug"
    title = request.form.get("title", "").strip()
    detail = request.form.get("detail", "").strip()
    reporter = request.form.get("reporter", "").strip()

    errors = []
    if len(title) < MIN_TITLE_LEN:
        errors.append(f"タイトルは{MIN_TITLE_LEN}文字以上で入力してください。")
    if len(detail) < MIN_DETAIL_LEN:
        errors.append(f"内容が簡潔すぎます。状況が伝わるよう、具体的に{MIN_DETAIL_LEN}文字以上で入力してください。")

    if errors:
        for e in errors:
            flash(e, "error")
        return render_template(
            "feedback/index.html", type_info=TYPE_INFO,
            form_type=feedback_type, form_title=title, form_detail=detail, form_reporter=reporter,
        ), 400

    label, type_label_ja, _ = TYPE_INFO[feedback_type]
    body_parts = [detail]
    if reporter:
        body_parts.append(f"\n---\n報告者: {reporter}")
    body_parts.append("\n_(このIssueはアプリ内フィードバックフォームから自動投稿されました)_")

    try:
        result = create_issue(
            title=f"[{type_label_ja}] {title}",
            body="\n".join(body_parts),
            labels=[label, "user-feedback"],
        )
    except GitHubIssueError as e:
        flash(str(e), "error")
        return render_template(
            "feedback/index.html", type_info=TYPE_INFO,
            form_type=feedback_type, form_title=title, form_detail=detail, form_reporter=reporter,
        ), 502

    # ヘルプ画面「既知の不具合」に新しいissueがすぐ反映されるようキャッシュを破棄する
    cache.delete_memoized(get_known_issues)

    flash(f"送信しました。ご報告ありがとうございます（Issue #{result['number']}）。", "success")
    return redirect(url_for("feedback.index"))
