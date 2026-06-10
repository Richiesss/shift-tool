"""予約客数に応じた増員数の計算ヘルパー（ソルバー・スケジュール表示で共用）"""


def tiered_extra(count: int, tiers: list[tuple[int, int]]) -> int:
    """
    予約客数 count に対して、満たされた閾値段階のうち最大の増員数を返す。

    tiers: [(閾値1, 増員数1), (閾値2, 増員数2), ...]
    閾値が0以下の段階は無効として無視する。
    例: tiers=[(100, 1), (150, 2)] のとき
        count=120 → 1（閾値1のみ満たす）
        count=160 → 2（閾値1・2を満たすが、より上位の段階を採用）
    """
    extra = 0
    for threshold, amount in tiers:
        if threshold > 0 and count >= threshold:
            extra = max(extra, amount)
    return extra


def effective_min_max(constraint: dict, extra: int) -> tuple[int, int]:
    """
    シフト制約 {"min": int, "max": int, ...} と予約客数による増員数 extra から、
    その日・スロット・ポジションの実効的な最低人数・最大人数を返す。

    増員がない（客数が少ない）日は max を min と同じ値に制限し、
    設定上の max まで余分に人員を配置できないようにする。
    増員がある日は設定上の max と (min+extra) の大きい方を上限とする。
    """
    base_min = constraint.get("min", 0)
    base_max = constraint.get("max", 0)
    if extra:
        base_min += extra
        base_max = max(base_max, base_min)
    else:
        base_max = base_min
    return base_min, base_max
