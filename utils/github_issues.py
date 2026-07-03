"""アプリ上から投稿されたバグ報告・機能要望をGitHub Issueとして起票する"""
from __future__ import annotations

import json
import os
import urllib.error
import urllib.request

from utils.changelog import REPO


class GitHubIssueError(Exception):
    """Issue作成に失敗した場合の例外。メッセージはユーザー向けにそのまま表示できる内容にする。"""


def create_issue(title: str, body: str, labels: list[str]) -> dict:
    """GitHub Issueを作成し、作成されたissueの番号とURLを返す。

    Issue作成は匿名アクセスでは行えないため、GITHUB_TOKEN（repo権限を持つトークン）が必須。"""
    token = os.environ.get("GITHUB_TOKEN")
    if not token:
        raise GitHubIssueError(
            "現在フィードバックの送信を受け付けられません（GITHUB_TOKEN未設定）。管理者にお問い合わせください。"
        )

    api_url = f"https://api.github.com/repos/{REPO}/issues"
    payload = json.dumps({"title": title, "body": body, "labels": labels}).encode("utf-8")
    req = urllib.request.Request(
        api_url,
        data=payload,
        method="POST",
        headers={
            "User-Agent": "SDU-Shift-FeedbackForm",
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=8) as resp:
            data = json.loads(resp.read())
    except urllib.error.HTTPError as e:
        raise GitHubIssueError(f"GitHub Issueの作成に失敗しました（エラーコード {e.code}）。時間をおいて再度お試しください。") from e
    except Exception as e:
        raise GitHubIssueError(f"GitHub Issueの作成に失敗しました: {e}") from e

    return {"number": data["number"], "url": data["html_url"]}
