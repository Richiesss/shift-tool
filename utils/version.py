"""アプリバージョン管理"""
from __future__ import annotations

from pathlib import Path


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
    """設定画面などに表示するバージョン文字列（gitコミットハッシュ）。"""
    return _get_git_commit_hash() or "dev"
