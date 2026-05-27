from flask import Blueprint, render_template, request, redirect, url_for, flash
from db import repositories as repo
from optimizer.solver import solve, SolverConfig, PRIORITY_SCALE

bp = Blueprint("generate", __name__, url_prefix="/generate")


@bp.get("/")
def index():
    periods = repo.get_all_periods()
    return render_template("generate/index.html", periods=periods, result=None, priority_scale=PRIORITY_SCALE)


@bp.post("/run")
def run():
    period_id = request.form.get("period_id", type=int)
    if not period_id:
        flash("期間を選択してください", "error")
        return redirect(url_for("generate.index"))

    period = repo.get_period(period_id)
    if not period:
        flash("期間が見つかりません", "error")
        return redirect(url_for("generate.index"))

    employees = repo.get_all_employees(active_only=True)
    requests = repo.get_shift_requests(period_id)

    def _scale(key):
        v = request.form.get(key, "中")
        return PRIORITY_SCALE.get(v, 1.0)

    config = SolverConfig(
        cost_scale=_scale("cost_scale"),
        pt_pref_scale=_scale("pt_pref_scale"),
        double_penalty_scale=_scale("double_penalty_scale"),
        balance_scale=_scale("balance_scale"),
        late_night_scale=_scale("late_night_scale"),
    )

    result = solve(period, employees, requests, config)

    if result.status in ("optimal", "feasible"):
        repo.save_assignments(period_id, result.assignments)
        flash(f"シフトを生成しました（{result.status}、{result.solve_time_sec:.1f}秒）", "success")
        if result.warnings:
            for w in result.warnings:
                flash(w, "warning")
        return redirect(url_for("schedule.index", period_id=period_id))
    else:
        periods = repo.get_all_periods()
        return render_template(
            "generate/index.html",
            periods=periods,
            result=result,
            priority_scale=PRIORITY_SCALE,
            selected_period_id=period_id,
        )
