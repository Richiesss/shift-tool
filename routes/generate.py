import logging
import threading
from flask import Blueprint, render_template, request, redirect, url_for, flash, current_app, jsonify, make_response
from db import repositories as repo
from optimizer.solver import solve, SolverConfig, PRIORITY_SCALE, SolveProgressCallback
from utils.solver_logger import logger, log_path
from utils.submission import submitted_employee_ids

bp = Blueprint("generate", __name__, url_prefix="/generate")


@bp.get("/")
def index():
    periods = repo.get_all_periods()
    active_employees = repo.get_all_employees(active_only=True)
    employees_count  = len(active_employees)
    # 各期間の前提条件チェック情報を事前計算
    period_checklist = {}
    for p in periods:
        requests     = repo.get_shift_requests(p.id)
        filled       = len(submitted_employee_ids(active_employees, requests))
        counts       = repo.get_reservation_counts(p.id)
        has_counts   = any(
            c.get("breakfast", 0) > 0 or c.get("dinner", 0) > 0
            for c in counts.values()
        )
        period_checklist[p.id] = {
            "filled":     filled,
            "total":      employees_count,
            "has_counts": has_counts,
        }
    resp = make_response(render_template(
        "generate/index.html",
        periods=periods,
        period_checklist=period_checklist,
        result=None,
        priority_scale=PRIORITY_SCALE,
    ))
    # bfcache を無効化してページ再表示時に古い needs_regen が残らないようにする
    resp.headers["Cache-Control"] = "no-store, no-cache, must-revalidate"
    resp.headers["Pragma"] = "no-cache"
    return resp


@bp.get("/checklist/<int:period_id>")
def checklist(period_id):
    """チェックリストを最新状態で返す軽量 API"""
    active_employees = repo.get_all_employees(active_only=True)
    employees_count  = len(active_employees)
    requests_list    = repo.get_shift_requests(period_id)
    filled           = len(submitted_employee_ids(active_employees, requests_list))
    counts          = repo.get_reservation_counts(period_id)
    has_counts      = any(
        c.get("breakfast", 0) > 0 or c.get("dinner", 0) > 0
        for c in counts.values()
    )
    return jsonify({
        "filled":     filled,
        "total":      employees_count,
        "has_counts": has_counts,
    })


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
        balance_scale=_scale("balance_scale"),
    )

    # 生成中ステータスへの遷移をアトミックに行う（同時POSTによる二重起動を防止）
    if not repo.try_start_generation(period_id):
        return redirect(url_for("generate.wait", period_id=period_id))
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


@bp.get("/diag/<int:period_id>")
def diag(period_id):
    """シフト入力データの診断（DBの実データを確認）"""
    from collections import defaultdict
    from utils.constants import EmploymentType
    try:
        period = repo.get_period(period_id)
        if not period:
            return jsonify({"error": "期間が見つかりません"}), 404

        employees = repo.get_all_employees(active_only=True)
        requests  = repo.get_shift_requests(period_id)
        dates     = [d.isoformat() for d in period.date_range()]

        # pattern_id の分布
        pattern_dist = defaultdict(int)
        for r in requests:
            pattern_dist[r.pattern_id or "(none)"] += 1

        # 日付×スロット別の希望人数
        by_date = {}
        req_map = defaultdict(lambda: defaultdict(list))
        for r in requests:
            if r.pattern_id in ("off_request", "paid_leave"):
                req_map[r.date]["off"].append(r.employee_id)
            else:
                if r.breakfast:
                    req_map[r.date]["breakfast"].append(r.employee_id)
                if r.dinner:
                    req_map[r.date]["dinner"].append(r.employee_id)

        for ds in dates:
            by_date[ds] = {
                "breakfast": len(req_map[ds]["breakfast"]),
                "dinner":    len(req_map[ds]["dinner"]),
                "off":       len(req_map[ds]["off"]),
            }

        ft_emps = [e for e in employees if e.employment_type == EmploymentType.FULL_TIME]
        pt_emps = [e for e in employees if e.employment_type != EmploymentType.FULL_TIME]

        # PT のうち希望提出ゼロの人
        pt_submitted = {r.employee_id for r in requests if r.pattern_id not in ("off_request","paid_leave")}
        pt_no_submit = [e.name for e in pt_emps if e.id not in pt_submitted]

        # FT のうち off_request/paid_leave があるか
        off_by_ft = defaultdict(list)
        for r in requests:
            if r.pattern_id in ("off_request", "paid_leave"):
                emp = next((e for e in ft_emps if e.id == r.employee_id), None)
                if emp:
                    off_by_ft[emp.name].append(f"{r.date}({r.pattern_id})")

        return jsonify({
            "period": f"{period.start_date}〜{period.end_date}",
            "employees": {"ft": len(ft_emps), "pt": len(pt_emps)},
            "total_requests": len(requests),
            "pattern_distribution": dict(pattern_dist),
            "pt_no_submission": pt_no_submit,
            "ft_off_requests": dict(off_by_ft),
            "daily_request_counts": by_date,
        })
    except Exception as e:
        import traceback
        return jsonify({"error": str(e), "trace": traceback.format_exc()}), 500
