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
