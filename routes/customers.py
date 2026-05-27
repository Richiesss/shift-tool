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
    periods = repo.get_all_periods()
    dates = period.date_range()
    counts = repo.get_reservation_counts(period_id)
    return render_template(
        "customers/index.html",
        period=period,
        periods=periods,
        dates=dates,
        counts=counts,
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
        b = int(request.form.get(f"b_{ds}", 0) or 0)
        dn = int(request.form.get(f"d_{ds}", 0) or 0)
        repo.save_reservation_count(period_id, ds, b, dn)
    flash("予約客数を保存しました", "success")
    return redirect(url_for("customers.index", period_id=period_id))
