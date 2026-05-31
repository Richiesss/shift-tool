from flask import Blueprint, render_template, request, redirect, url_for, flash
from db import repositories as repo

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
    thresh_b = int(repo.get_app_setting("reserv_threshold_breakfast", "100"))
    thresh_d = int(repo.get_app_setting("reserv_threshold_dinner",    "25"))
    gen      = repo.get_period_gen_status(period_id)
    schedule_generated = gen["status"] == "done"
    return render_template(
        "customers/index.html",
        period=period, periods=periods, dates=dates, counts=counts,
        thresh_b=thresh_b, thresh_d=thresh_d,
        schedule_generated=schedule_generated,
    )


@bp.post("/<int:period_id>/save")
def save(period_id):
    period = repo.get_period(period_id)
    if not period:
        flash("期間が見つかりません", "error")
        return redirect(url_for("shifts.index"))
    dates = period.date_range()
    for d in dates:
        ds = str(d)
        b  = int(request.form.get(f"b_{ds}", 0) or 0)
        dn = int(request.form.get(f"d_{ds}", 0) or 0)
        repo.save_reservation_count(period_id, ds, b, dn)
    # 生成済みシフトがある場合は再生成フラグを立てる
    gen = repo.get_period_gen_status(period_id)
    if gen["status"] == "done":
        repo.set_period_needs_regen(period_id, True)
        flash("予約客数を保存しました。シフトが生成済みのため、再生成を推奨します。", "warning")
    else:
        flash("予約客数を保存しました", "success")
    return redirect(url_for("customers.index", period_id=period_id))
