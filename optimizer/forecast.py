"""客数予測（β機能）

過去の客数実績・曜日/祝日区分・気象データから、朝食/ディナーの客数を予測する。

- 学習データが少ない間は、曜日（祝日は曜日に関わらず別区分）別の加重移動平均
  （Tier1: 同じ曜日区分 → Tier2: 平日/休日(土日祝) → Tier3: 全体平均）にフォールバックする。
- 学習データが十分（既定30件以上）にある場合は scikit-learn の RandomForestRegressor で
  曜日・祝日・特殊日フラグ・月・気象（Open-Meteo）・直近の同曜日傾向を特徴量として学習する。
- 気象APIや scikit-learn が利用できない場合も例外を投げず、統計フォールバックの結果を返す。
"""
from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from datetime import date

from cache import cache
from db import repositories as repo
from utils.holidays import is_holiday

_DAY_CATS = ["月", "火", "水", "木", "金", "土", "日"]
_WEEKEND_CATS = {"土", "日", "祝日"}

_DECAY = 0.85            # 加重平均の減衰率（新しいデータほど重視）
_MIN_TIER_SAMPLES = 3    # 各階層フォールバックで使う最低サンプル数
_MIN_ML_SAMPLES = 30     # MLモデルを学習する最低データ件数
_MAX_HISTORY_DAYS = 730  # 学習に使う直近データの上限（約2年分）
_MAX_FORECAST_DAYS = 16  # Open-Meteo予報の取得可能日数

_OPEN_METEO_ARCHIVE  = "https://archive-api.open-meteo.com/v1/archive"
_OPEN_METEO_FORECAST = "https://api.open-meteo.com/v1/forecast"
_DAILY_FIELDS = "temperature_2m_max,temperature_2m_min,precipitation_sum,weathercode"

METHOD_LABELS = {
    "ml":            "機械学習モデル",
    "weekday":       "同じ曜日区分の実績平均",
    "weekday_group": "平日/休日の実績平均",
    "overall":       "全体の実績平均",
    "no_data":       "データ不足",
}


def _day_category(d: date) -> str:
    """日付から曜日区分を返す（祝日は曜日に関わらず"祝日"扱い）"""
    if is_holiday(d):
        return "祝日"
    return _DAY_CATS[d.weekday()]


def _tiered_average(values: list[float], cats: list[str], target_cat: str) -> dict:
    """
    曜日区分別の加重平均（Tier1〜3フォールバック）。
    values, cats は同じ並び（古い→新しい順）の実績値・曜日区分のリスト。
    """
    is_weekend = target_cat in _WEEKEND_CATS

    tier1 = [v for v, c in zip(values, cats) if c == target_cat]
    tier2 = [v for v, c in zip(values, cats) if (c in _WEEKEND_CATS) == is_weekend]
    tier3 = values

    if len(tier1) >= _MIN_TIER_SAMPLES:
        samples, method = tier1, "weekday"
    elif len(tier2) >= _MIN_TIER_SAMPLES:
        samples, method = tier2, "weekday_group"
    elif tier3:
        samples, method = tier3, "overall"
    else:
        return {"predicted": None, "low": None, "high": None, "n_samples": 0, "method": "no_data"}

    n = len(samples)
    weights = [_DECAY ** (n - 1 - i) for i in range(n)]
    wsum = sum(weights)
    mean = sum(w * v for w, v in zip(weights, samples)) / wsum
    if n >= 2:
        var = sum((v - mean) ** 2 for v in samples) / (n - 1)
        sd = var ** 0.5
    else:
        sd = 0.0

    return {
        "predicted": max(0, round(mean)),
        "low": max(0, round(mean - sd)),
        "high": max(0, round(mean + sd)),
        "n_samples": n,
        "method": method,
    }


# ── 気象データ（Open-Meteo） ─────────────────────────────────────────────

def _http_get_json(url: str, timeout: int = 8) -> dict | None:
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "SDU-Shift-Forecast"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode())
    except (urllib.error.URLError, TimeoutError, ValueError, OSError):
        return None


def _safe_get(lst, i):
    if not lst or i >= len(lst):
        return None
    return lst[i]


def _fetch_weather_range(lat: float, lon: float, start: date, end: date, archive: bool) -> dict[str, dict]:
    """指定期間の気象データを取得し {date_str: {...}} で返す（失敗時は{}）"""
    base = _OPEN_METEO_ARCHIVE if archive else _OPEN_METEO_FORECAST
    params = {
        "latitude": lat, "longitude": lon,
        "daily": _DAILY_FIELDS,
        "timezone": "Asia/Tokyo",
    }
    if archive:
        params["start_date"] = start.isoformat()
        params["end_date"] = end.isoformat()
    else:
        params["forecast_days"] = min(_MAX_FORECAST_DAYS, max(1, (end - date.today()).days + 1))

    data = _http_get_json(f"{base}?{urllib.parse.urlencode(params)}")
    if not data or "daily" not in data:
        return {}

    daily = data["daily"]
    times = daily.get("time", [])
    return {
        d: {
            "temp_max":      _safe_get(daily.get("temperature_2m_max"), i),
            "temp_min":      _safe_get(daily.get("temperature_2m_min"), i),
            "precipitation": _safe_get(daily.get("precipitation_sum"), i),
            "weather_code":  _safe_get(daily.get("weathercode"), i),
        }
        for i, d in enumerate(times)
    }


def _get_weather_map(dates: list[date], lat: float | None, lon: float | None) -> dict[str, dict]:
    """指定日付群の気象データを {date_str: {...}} で返す（DBキャッシュ＋Open-Meteo）"""
    if lat is None or lon is None or not dates:
        return {}

    date_strs = sorted({d.isoformat() for d in dates})
    cached = repo.get_weather_cache(date_strs)
    missing = [ds for ds in date_strs if ds not in cached]
    if not missing:
        return cached

    today = date.today()
    missing_dates = [date.fromisoformat(ds) for ds in missing]
    past   = [d for d in missing_dates if d < today]
    future = [d for d in missing_dates if d >= today]

    fetched: dict[str, dict] = {}
    if past:
        fetched.update(_fetch_weather_range(lat, lon, min(past), max(past), archive=True))
    if future:
        fetched.update(_fetch_weather_range(lat, lon, min(future), max(future), archive=False))

    if fetched:
        repo.save_weather_cache(fetched)
    cached.update(fetched)
    return cached


def _weather_means(history: list[dict], weather_map: dict[str, dict]) -> dict[str, float]:
    """欠損気象データの補完用に、学習データ期間の平均値を計算する"""
    defaults = {"temp_max": 20.0, "temp_min": 10.0, "precipitation": 0.0, "weather_code": 0.0}
    sums: dict[str, list[float]] = {k: [] for k in defaults}
    for r in history:
        w = weather_map.get(r["date"])
        if not w:
            continue
        for k in defaults:
            if w.get(k) is not None:
                sums[k].append(w[k])
    return {k: (sum(v) / len(v) if v else defaults[k]) for k, v in sums.items()}


# ── 特徴量・MLモデル ────────────────────────────────────────────────────

def _feature_row(d: date, weather: dict | None, is_special_day: int, means: dict, lag_avg: float) -> list[float]:
    weekday_onehot = [1.0 if d.weekday() == i else 0.0 for i in range(7)]
    holiday_flag = 1.0 if is_holiday(d) else 0.0
    w = weather or {}
    temp_max = w.get("temp_max")      if w.get("temp_max")      is not None else means["temp_max"]
    temp_min = w.get("temp_min")      if w.get("temp_min")      is not None else means["temp_min"]
    precip   = w.get("precipitation") if w.get("precipitation") is not None else means["precipitation"]
    wcode    = w.get("weather_code")  if w.get("weather_code")  is not None else means["weather_code"]
    return weekday_onehot + [holiday_flag, float(is_special_day), float(d.month),
                              temp_max, temp_min, precip, wcode, lag_avg]


def _build_training_set(history: list[dict], cats: list[str], slot: str,
                         weather_map: dict[str, dict], means: dict) -> tuple[list, list]:
    values = [r[slot] for r in history]
    X, y = [], []
    for i, r in enumerate(history):
        if i == 0:
            continue  # 直近傾向（ラグ特徴量）の算出に過去データが1件も無い行は除外
        d = date.fromisoformat(r["date"])
        lag_stat = _tiered_average(values[:i], cats[:i], cats[i])
        feat = _feature_row(d, weather_map.get(r["date"]), r["is_special_day"], means, lag_stat["predicted"])
        X.append(feat)
        y.append(r[slot])
    return X, y


def predict_customer_counts(target_dates: list[date], special_days: set[str] | None = None) -> dict[str, dict]:
    """
    指定した日付リストについて朝食・ディナーの客数を予測する。

    戻り値: {date_str: {"breakfast": {予測結果}, "dinner": {予測結果}}}
    予測結果: {"predicted": int|None, "low": int|None, "high": int|None,
              "n_samples": int, "method": str}
    method は METHOD_LABELS のキーのいずれか。
    """
    special_days = special_days or set()
    history_all = repo.get_forecast_training_data()
    history = history_all[-_MAX_HISTORY_DAYS:]

    empty_slot = {"predicted": None, "low": None, "high": None, "n_samples": 0, "method": "no_data"}
    if not history:
        return {d.isoformat(): {"breakfast": dict(empty_slot), "dinner": dict(empty_slot)} for d in target_dates}

    cats = [_day_category(date.fromisoformat(r["date"])) for r in history]

    lat_s = repo.get_app_setting("store_lat", "")
    lon_s = repo.get_app_setting("store_lon", "")
    try:
        lat = float(lat_s) if lat_s else None
        lon = float(lon_s) if lon_s else None
    except ValueError:
        lat, lon = None, None

    weather_map = _get_weather_map(
        [date.fromisoformat(r["date"]) for r in history] + list(target_dates), lat, lon
    )
    means = _weather_means(history, weather_map)

    result: dict[str, dict] = {ds: {} for ds in (d.isoformat() for d in target_dates)}

    for slot in ("breakfast", "dinner"):
        values = [r[slot] for r in history]

        model = None
        if len(history) >= _MIN_ML_SAMPLES:
            X, y = _build_training_set(history, cats, slot, weather_map, means)
            if len(X) >= _MIN_ML_SAMPLES:
                try:
                    from sklearn.ensemble import RandomForestRegressor
                    model = RandomForestRegressor(n_estimators=200, max_depth=8, random_state=42)
                    model.fit(X, y)
                except Exception:
                    model = None

        for d in target_dates:
            ds = d.isoformat()
            target_cat = _day_category(d)
            stat = _tiered_average(values, cats, target_cat)

            if model is not None:
                try:
                    is_special = 1 if ds in special_days else 0
                    lag_stat = _tiered_average(values, cats, target_cat)
                    feat = _feature_row(d, weather_map.get(ds), is_special, means, lag_stat["predicted"])
                    pred = float(model.predict([feat])[0])
                    stat = {**stat, "predicted": max(0, round(pred)), "method": "ml"}
                except Exception:
                    pass

            result[ds][slot] = stat

    return result


@cache.memoize(timeout=300)
def predict_customer_counts_cached(target_date_strs: tuple[str, ...], special_days: tuple[str, ...]) -> dict:
    """predict_customer_counts() のキャッシュ付きラッパー（モデル再学習のコスト軽減のため5分間キャッシュ）

    β機能のため、DBエラー等で予測に失敗してもページ全体を巻き込まないよう
    例外を握りつぶし「データ不足」扱いの結果を返す。
    """
    target_dates = [date.fromisoformat(ds) for ds in target_date_strs]
    try:
        return predict_customer_counts(target_dates, set(special_days))
    except Exception:
        empty_slot = {"predicted": None, "low": None, "high": None, "n_samples": 0, "method": "no_data"}
        return {ds: {"breakfast": dict(empty_slot), "dinner": dict(empty_slot)} for ds in target_date_strs}
