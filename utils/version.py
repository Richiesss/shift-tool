"""アプリバージョン管理とアップデートチェック"""
from __future__ import annotations

from pathlib import Path

# ビルド時に CI が上書きする (_version.py を生成)
try:
    from utils._version import APP_VERSION  # type: ignore
except ImportError:
    APP_VERSION = "dev"


def get_build_number() -> int:
    """'build-42' → 42 に変換。dev / 解析不能なら 0"""
    try:
        return int(APP_VERSION.split("-")[1])
    except (IndexError, ValueError):
        return 0


def _get_git_commit_hash() -> str:
    """`.git`ディレクトリを直接読んで現在のコミットハッシュ（短縮形）を取得する。
    gitコマンドに依存しないため、Dockerコンテナ内でも動作する。取得できなければ空文字を返す。"""
    try:
        git_dir = Path(__file__).resolve().parent.parent / ".git"
        head = (git_dir / "HEAD").read_text().strip()
        if head.startswith("ref:"):
            ref_path = git_dir / head.split(" ", 1)[1]
            commit = ref_path.read_text().strip()
        else:
            commit = head
        return commit[:7]
    except OSError:
        return ""


def get_display_version() -> str:
    """設定画面などに表示するバージョン文字列。
    デスクトップ版はCIで埋め込まれたAPP_VERSION（build-N）を、
    Web版（_version.py未生成）はgitコミットハッシュを返す。"""
    if APP_VERSION != "dev":
        return APP_VERSION
    return _get_git_commit_hash() or APP_VERSION


def check_for_update() -> tuple[bool, str, str]:
    """
    GitHub Releases API で最新ビルドを確認する。
    戻り値: (is_newer, latest_tag, release_url)
    ネットワークエラー時は (False, "", "") を返す。
    """
    import urllib.request
    import json
    api_url = "https://api.github.com/repos/Richiesss/shift-tool/releases/latest"
    try:
        req = urllib.request.Request(
            api_url, headers={"User-Agent": "SDU-Shift-UpdateChecker"})
        with urllib.request.urlopen(req, timeout=6) as resp:
            data = json.loads(resp.read())
        tag      = data.get("tag_name", "")
        html_url = data.get("html_url", "")
        try:
            latest_n  = int(tag.split("-")[1])
            current_n = get_build_number()
            return latest_n > current_n, tag, html_url
        except (IndexError, ValueError):
            return False, tag, html_url
    except Exception:
        return False, "", ""
