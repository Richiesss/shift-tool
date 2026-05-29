import threading
from flask import Blueprint, render_template, request, redirect, url_for, flash, current_app, jsonify
from db import repositories as repo
from optimizer.solver import solve, SolverConfig, PRIORITY_SCALE, SolveProgressCallback

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

    # すでに生成中なら待機ページへ
    gen = repo.get_period_gen_status(period_id)
    if gen["status"] == "generating":
        return redirect(url_for("generate.wait", period_id=period_id))

    employees = repo.get_all_employees(active_only=True)
    requests_list = repo.get_shift_requests(period_id)

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

    # 生成中ステータスに更新してバックグラウンドで実行
    repo.update_period_gen_status(period_id, "generating", "")
    app = current_app._get_current_object()

    def _run_solver():
        # app_context を最初から張る（cache.memoize など Flask 依存の処理を含むため）
        with app.app_context():
            try:
                cb = SolveProgressCallback(period_id, max_time=10.0)
                result = solve(period, employees, requests_list, config, progress_callback=cb)
                if result.status in ("optimal", "feasible"):
                    repo.save_assignments(period_id, result.assignments)
                    msg = f"{result.status},{result.solve_time_sec:.1f}"
                    if result.warnings:
                        msg += "|" + "|".join(result.warnings)
                    repo.update_period_gen_status(period_id, "done", msg)
                else:
                    errors = "; ".join(result.errors) if result.errors else "制約を満たすシフトが見つかりませんでした"
                    repo.update_period_gen_status(period_id, "failed", errors)
            except Exception as e:
                try:
                    repo.update_period_gen_status(period_id, "failed", str(e))
                except Exception:
                    pass

    threading.Thread(target=_run_solver, daemon=True).start()
    return redirect(url_for("generate.wait", period_id=period_id))


@bp.get("/wait/<int:period_id>")
def wait(period_id):
    period = repo.get_period(period_id)
    return render_template("generate/waiting.html", period=period)


@bp.get("/status/<int:period_id>")
def status(period_id):
    gen = repo.get_period_gen_status(period_id)
    msg = gen.get("message", "")
    progress = 0
    solutions = 0
    if gen["status"] == "generating" and msg.startswith("progress:"):
        parts = dict(kv.split(":") for kv in msg.split(",") if ":" in kv)
        progress = int(parts.get("progress", 0))
        solutions = int(parts.get("solutions", 0))
    elif gen["status"] == "done":
        progress = 100
    return jsonify({**gen, "progress": progress, "solutions": solutions})
