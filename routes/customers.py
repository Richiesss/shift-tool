import calendar
from datetime import date
from flask import Blueprint, render_template, request, redirect, url_for, flash
from cache import cache
from db import repositories as repo
from optimizer import forecast
from utils.form_helpers import safe_int
from utils.holidays import holiday_set

bp = Blueprint("customers", __name__, url_prefix="/customers")


@bp.get("/")
def list_periods():
    periods = repo.get_all_periods()
    if periods:
        return redirect(url_for("customers.index", period_id=periods[0].id))
    flash("期間がありません", "info")
    return redirect(url_for("shifts.index"))


@bp.get("/<int:period_id>")
def index(period_id):
    period = repo.get_period(period_id)
    if not period:
        flash("期間が見つかりません", "error")
        return redirect(url_for("shifts.index"))
    periods  = repo.get_all_periods()
    dates    = period.date_range()
    counts   = repo.get_reservation_counts(period_id)
    thresh_b = safe_int(repo.get_app_setting("reserv_threshold_breakfast", "100"), 100)
    thresh_d = safe_int(repo.get_app_setting("reserv_threshold_dinner",    "25"), 25)
    gen         = repo.get_period_gen_status(period_id)
    needs_regen = gen.get("needs_regen", False)

    forecast_enabled = repo.get_app_setting("customer_forecast_enabled", "0") == "1"
    forecast_data = {}
    if forecast_enabled:
        special_days = tuple(sorted(ds for ds, c in counts.items() if c.get("is_special_day")))
        date_strs = tuple(d.isoformat() for d in dates)
        forecast_data = forecast.predict_customer_counts_cached(date_strs, special_days)

    return render_template(
        "customers/index.html",
        period=period, periods=periods, dates=dates, counts=counts,
        thresh_b=thresh_b, thresh_d=thresh_d,
        needs_regen=needs_regen,
        holidays=holiday_set(dates),
        forecast_enabled=forecast_enabled,
        forecast=forecast_data,
        method_labels=forecast.METHOD_LABELS,
    )


@bp.post("/<int:period_id>/save")
def save(period_id):
    period = repo.get_period(period_id)
    if not period:
        flash("期間が見つかりません", "error")
        return redirect(url_for("shifts.index"))
    dates  = period.date_range()
    # 変更前の値を取得して比較
    old_counts = repo.get_reservation_counts(period_id)
    changed = False
    forecast_changed = False
    for d in dates:
        ds = str(d)
        new_b  = safe_int(request.form.get(f"b_{ds}"))
        new_dn = safe_int(request.form.get(f"d_{ds}"))
        new_special = 1 if request.form.get(f"special_{ds}") else 0
        old    = old_counts.get(ds, {})
        if new_b != old.get("breakfast", 0) or new_dn != old.get("dinner", 0):
            changed = True
        if new_special != old.get("is_special_day", 0):
            forecast_changed = True
        repo.save_reservation_count(period_id, ds, new_b, new_dn, new_special)
    if changed or forecast_changed:
        cache.delete_memoized(forecast.predict_customer_counts_cached)
    # 値が実際に変わった && 生成済みシフトがある場合のみ再生成フラグを立てる
    if changed:
        gen = repo.get_period_gen_status(period_id)
        if gen["status"] == "done":
            repo.set_period_needs_regen(period_id, True)
            flash("予約客数を保存しました。シフトが生成済みのため、再生成を推奨します。", "warning")
        else:
            flash("予約客数を保存しました", "success")
    else:
        flash("予約客数を保存しました（変更なし）", "success")
    return redirect(url_for("customers.index", period_id=period_id))


# ── 過去客数データ入力（β）────────────────────────────────────────────

@bp.get("/history")
def history():
    """運用開始前の過去客数（紙の集計表など）を月単位で入力する画面"""
    today = date.today()
    ym = request.args.get("ym", "")
    try:
        year, month = (int(p) for p in ym.split("-"))
        if month < 1 or month > 12:
            raise ValueError
    except ValueError:
        year, month = today.year, today.month

    last_day = calendar.monthrange(year, month)[1]
    dates = [date(year, month, d) for d in range(1, last_day + 1)]
    ym = f"{year:04d}-{month:02d}"
    data = repo.get_customer_count_history(ym)

    if month == 1:
        prev_ym = f"{year - 1:04d}-12"
    else:
        prev_ym = f"{year:04d}-{month - 1:02d}"
    if month == 12:
        next_ym = f"{year + 1:04d}-01"
    else:
        next_ym = f"{year:04d}-{month + 1:02d}"

    return render_template(
        "customers/history.html",
        dates=dates, data=data, ym=ym,
        prev_ym=prev_ym, next_ym=next_ym,
        holidays=holiday_set(dates),
    )


@bp.post("/history/save")
def save_history():
    ym = request.form.get("ym", "")
    try:
        year, month = (int(p) for p in ym.split("-"))
        last_day = calendar.monthrange(year, month)[1]
    except ValueError:
        flash("年月が不正です", "error")
        return redirect(url_for("customers.history"))

    entries = {}
    for d in range(1, last_day + 1):
        ds = f"{year:04d}-{month:02d}-{d:02d}"
        b  = request.form.get(f"b_{ds}", "")
        dn = request.form.get(f"d_{ds}", "")
        entries[ds] = {
            "breakfast": safe_int(b),
            "dinner":    safe_int(dn),
        }
    repo.save_customer_count_history(entries)
    cache.delete_memoized(forecast.predict_customer_counts_cached)
    flash("過去客数データを保存しました", "success")
    return redirect(url_for("customers.history", ym=ym))
