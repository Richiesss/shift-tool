"""ソルバー専用ロガーの設定"""
import logging
import os
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path

_LOG_PATH_ENV = os.environ.get("SOLVER_LOG_PATH", "")

if _LOG_PATH_ENV:
    _log_path = Path(_LOG_PATH_ENV)
    _log_path.parent.mkdir(parents=True, exist_ok=True)
else:
    try:
        _log_path = Path.home() / ".shift_tool" / "solver.log"
        _log_path.parent.mkdir(parents=True, exist_ok=True)
    except (OSError, PermissionError):
        # VercelやCloud Runなどのサーバーレス環境でホームに書き込めない場合のフォールバック
        _log_path = Path("/tmp") / "solver.log"
        _log_path.parent.mkdir(parents=True, exist_ok=True)

_fmt = logging.Formatter(
    "%(asctime)s | %(levelname)-5s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)

_file_handler = RotatingFileHandler(
    str(_log_path), maxBytes=5 * 1024 * 1024, backupCount=3, encoding="utf-8"
)
_file_handler.setFormatter(_fmt)

_console_handler = logging.StreamHandler(sys.stdout)
_console_handler.setFormatter(_fmt)

logger = logging.getLogger("solver")
logger.setLevel(logging.DEBUG)
if not logger.handlers:
    logger.addHandler(_file_handler)
    logger.addHandler(_console_handler)

logger.propagate = False


def log_path() -> str:
    return str(_log_path)
