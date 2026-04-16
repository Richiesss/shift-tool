"""アプリバージョン管理とアップデートチェック"""
from __future__ import annotations

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
