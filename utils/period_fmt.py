"""期間ラベルの日本語フォーマット（item 10）"""
from __future__ import annotations


def fmt(start_date: str, end_date: str) -> str:
    """
    ISO 日付文字列を日本語ラベルに変換する。

    同月: '2025年1月（1日〜31日）'
    月またぎ同年: '2025年1/27〜2/10'
    年またぎ: '2024/12/28〜2025/1/10'
    """
    from datetime import date as _d
    sd = _d.fromisoformat(start_date)
    ed = _d.fromisoformat(end_date)
    if sd.year == ed.year and sd.month == ed.month:
        return f"{sd.year}年{sd.month}月（{sd.day}日〜{ed.day}日）"
    elif sd.year == ed.year:
        return f"{sd.year}年{sd.month}/{sd.day}〜{ed.month}/{ed.day}"
    else:
        return f"{sd.year}/{sd.month}/{sd.day}〜{ed.year}/{ed.month}/{ed.day}"
