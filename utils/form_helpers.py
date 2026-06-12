def safe_int(value, default=0) -> int:
    """フォーム入力値・設定値を int に変換する。空文字や不正な値は default を返す"""
    try:
        return int(value)
    except (TypeError, ValueError):
        return default
