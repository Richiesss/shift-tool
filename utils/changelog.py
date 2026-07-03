"""更新履歴（コミットログ）と既知の不具合（GitHub Issues）の取得"""
from __future__ import annotations

import json
import os
import subprocess
import urllib.request
from pathlib import Path

from cache import cache

REPO = "Richiesss/shift-tool"
_REPO_ROOT = Path(__file__).resolve().parent.parent


def get_commit_log(limit: int = 30) -> list[dict] | None:
    """直近のコミットログを取得する（更新履歴表示用）。

    gitコマンドが利用できない環境（取得失敗時）は None を返す。"""
    try:
        result = subprocess.run(
            ["git", "log", f"-n{limit}", "--no-merges",
             "--date=format:%Y-%m-%d", "--pretty=format:%h\t%ad\t%s"],
            capture_output=True, text=True, timeout=5, check=True,
            cwd=_REPO_ROOT,
        )
    except (OSError, subprocess.SubprocessError):
        return None

    commits = []
    for line in result.stdout.splitlines():
        parts = line.split("\t", 2)
        if len(parts) != 3:
            continue
        commits.append({"hash": parts[0], "date": parts[1], "subject": parts[2]})
    return commits


@cache.memoize(timeout=1800)
def get_known_issues() -> list[dict] | None:
    """GitHub Issues から state:open の一覧を取得する（既知の不具合表示用）。

    ラベルによる絞り込みは行わない（bugラベルが付いていない未対応issueも
    表示対象とするため）。通信エラー時は None を返す。"""
    api_url = (
        f"https://api.github.com/repos/{REPO}/issues"
        "?state=open&per_page=50&sort=created&direction=desc"
    )
    headers = {"User-Agent": "SDU-Shift-IssueChecker", "Accept": "application/vnd.github+json"}
    github_token = os.environ.get("GITHUB_TOKEN")
    if github_token:
        headers["Authorization"] = f"Bearer {github_token}"

    try:
        req = urllib.request.Request(api_url, headers=headers)
        with urllib.request.urlopen(req, timeout=6) as resp:
            data = json.loads(resp.read())
    except Exception:
        return None

    issues = []
    for item in data:
        if "pull_request" in item:
            continue
        issues.append({
            "number": item["number"],
            "title": item["title"],
            "url": item["html_url"],
            "created_at": item.get("created_at", "")[:10],
        })
    return issues
