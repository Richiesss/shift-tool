from flask import Blueprint, render_template, request, redirect, url_for, flash
from db import repositories as repo
from utils.constants import TimeSlot, Position

bp = Blueprint("settings", __name__, url_prefix="/settings")


@bp.get("/")
def index():
    constraints = repo.get_shift_constraints()
    settings = repo.get_all_app_settings()
    band_constraints = repo.get_breakfast_band_constraints()
    return render_template(
        "settings/index.html",
        constraints=constraints,
        settings=settings,
        band_constraints=band_constraints,
        TimeSlot=TimeSlot,
        Position=Position,
    )


@bp.post("/save")
def save():
    constraints = repo.get_shift_constraints()
    new_constraints = {}
    for (slot, pos) in constraints:
        prefix = f"{slot.value}_{pos.value}"
        new_constraints[(slot, pos)] = {
            "min": int(request.form.get(f"min_{prefix}", 0)),
            "max": int(request.form.get(f"max_{prefix}", 0)),
            "min_leader": int(request.form.get(f"ml_{prefix}", 0)),
        }
    repo.save_shift_constraints(new_constraints)

    settings_keys = [
        "reserv_threshold_breakfast", "reserv_extra_breakfast",
        "reserv_threshold_dinner", "reserv_extra_dinner",
    ]
    new_settings = {k: request.form.get(k, "") for k in settings_keys}
    repo.save_all_app_settings(new_settings)

    band_constraints = repo.get_breakfast_band_constraints()
    new_band = {}
    for (band, pos) in band_constraints:
        prefix = f"{band}_{pos}"
        new_band[(band, pos)] = {
            "min":        int(request.form.get(f"band_min_{prefix}", 0)),
            "max":        int(request.form.get(f"band_max_{prefix}", 0)),
            "min_leader": int(request.form.get(f"band_ml_{prefix}", 0)),
        }
    repo.save_breakfast_band_constraints(new_band)

    flash("設定を保存しました", "success")
    return redirect(url_for("settings.index"))
