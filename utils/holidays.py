try:
    import jpholiday as _jpholiday
    def is_holiday(d) -> bool:
        return bool(_jpholiday.is_holiday(d))
except ImportError:
    def is_holiday(d) -> bool:
        return False


def holiday_set(dates) -> set:
    """日付のイテラブルから祝日のISO文字列セットを返す"""
    return {d.isoformat() for d in dates if is_holiday(d)}
