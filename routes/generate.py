import logging
import threading
from flask import Blueprint, render_template, request, redirect, url_for, flash, current_app, jsonify
from db import repositories as repo
from optimizer.solver import solve, SolverConfig, PRIORITY_SCALE, SolveProgressCallback
from utils.solver_logger import logger, log_path

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
                logger.info(f"[THREAD START] period_id={period_id}  log={log_path()}")
                cb = SolveProgressCallback(period_id, max_time=25.0)
                result = solve(period, employees, requests_list, config, progress_callback=cb, period_id=period_id)
                if result.status in ("optimal", "feasible"):
                    repo.save_assignments(period_id, result.assignments)
                    msg = f"{result.status},{result.solve_time_sec:.1f}"
                    if result.warnings:
                        msg += "|" + "|".join(result.warnings)
                    repo.update_period_gen_status(period_id, "done", msg)
                    logger.info(f"[THREAD DONE] period_id={period_id}  status={result.status}  "
                                f"assignments={len(result.assignments)}  warnings={len(result.warnings)}  "
                                f"errors={len(result.errors)}")
                else:
                    errors = "; ".join(result.errors) if result.errors else "制約を満たすシフトが見つかりませんでした"
                    repo.update_period_gen_status(period_id, "failed", errors)
                    logger.error(f"[THREAD FAILED] period_id={period_id}  errors={errors}")
            except Exception as e:
                import traceback
                logger.error(f"[THREAD EXCEPTION] period_id={period_id}  {e}")
                logger.error(traceback.format_exc())
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
    elapsed = 0.0
    solutions = 0
    phase = 1
    obj = -1
    if gen["status"] == "generating" and ("progress:" in msg or "phase:" in msg):
        parts = dict(kv.split(":") for kv in msg.split(",") if ":" in kv)
        elapsed = float(parts.get("elapsed", 0))
        solutions = int(parts.get("solutions", 0))
        phase = int(parts.get("phase", 1))
        obj = int(parts.get("obj", -1))
    return jsonify({**gen, "elapsed": elapsed, "solutions": solutions, "phase": phase, "obj": obj})


@bp.get("/log")
def view_log():
    """直近のソルバーログを返す（デバッグ用）"""
    import os
    try:
        path = log_path()
        if not os.path.exists(path):
            return jsonify({"log": "(ログファイルがまだ存在しません)", "path": path})
        with open(path, encoding="utf-8") as f:
            lines = f.readlines()
        tail = "".join(lines[-200:])
        return jsonify({"log": tail, "path": path, "total_lines": len(lines)})
    except Exception as e:
        return jsonify({"error": str(e)}), 500
