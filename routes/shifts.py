import calendar
from datetime import date, timedelta
from flask import Blueprint, render_template, request, redirect, url_for, flash
from db import repositories as repo
from models.schedule import SchedulePeriod, ShiftRequest
from utils.shift_patterns import ALL_PATTERNS, PATTERN_MAP
from utils.constants import EmploymentType
from utils.holidays import holiday_set

PAT_CATS = {
    'b_short': 'b', 'b_std': 'b', 'b_long': 'b', 'b_half': 'b',
    'd_full1': 'd', 'd_full2': 'd',
    'd_std1': 'd', 'd_std2': 'd', 'd_std3': 'd',
    'd_s1': 'd', 'd_s2': 'd', 'd_s3': 'd',
    'double': 'db',
    'custom': 'cust',
}

bp = Blueprint("shifts", __name__, url_prefix="/shifts")


@bp.get("/")
def index():
    periods = repo.get_all_periods()
    # 新しい順に並べて最新4件と残りを分ける
    sorted_periods = sorted(periods, key=lambda p: p.start_date, reverse=True)
    recent  = sorted_periods[:4]
    older   = sorted_periods[4:]
    show_all = request.args.get("all", type=int, default=0)
    return render_template("shifts/index.html",
                           periods=recent if not show_all else sorted_periods,
                           has_older=bool(older),
                           show_all=show_all)


@bp.post("/<int:period_id>/delete")
def delete_period(period_id):
    period = repo.get_period(period_id)
    if not period:
        flash("期間が見つかりません", "error")
        return redirect(url_for("shifts.index"))
    repo.delete_period(period_id)
    flash(f"{period.start_date} 〜 {period.end_date} の期間を削除しました", "success")
    return redirect(url_for("shifts.index"))


@bp.post("/period/new")
def new_period():
    year  = request.form.get("year",  type=int)
    month = request.form.get("month", type=int)
    half  = request.form.get("half", "first")  # "first" or "second"

    if not year or not month or month < 1 or month > 12:
        flash("年月が不正です", "error")
        return redirect(url_for("shifts.index"))

    last_day = calendar.monthrange(year, month)[1]
    if half == "first":
        start = date(year, month, 1)
        end   = date(year, month, 15)
    else:
        start = date(year, month, 16)
        end   = date(year, month, last_day)

    period = SchedulePeriod(id=None, start_date=str(start), end_date=str(end))
    period = repo.save_period(period)
    return redirect(url_for("shifts.input", period_id=period.id))


@bp.get("/<int:period_id>")
def input(period_id):
    period = repo.get_period(period_id)
    if not period:
        flash("期間が見つかりません", "error")
        return redirect(url_for("shifts.index"))
    employees = repo.get_all_employees(active_only=True)
    requests_list = repo.get_shift_requests(period_id)
    req_map = {(r.employee_id, r.date): r for r in requests_list}
    dates = period.date_range()

    # 1人フォーカスモードをデフォルト（grid=1 で全員表示）
    show_grid = request.args.get("grid", type=int, default=0)
    if show_grid:
        emp_idx = None
    else:
        emp_idx = request.args.get("emp_idx", type=int, default=0)

    current_emp = None
    prev_idx = next_idx = None
    if emp_idx is not None and 0 <= emp_idx < len(employees):
        current_emp = employees[emp_idx]
        prev_idx = emp_idx - 1 if emp_idx > 0 else None
        next_idx = emp_idx + 1 if emp_idx < len(employees) - 1 else None

    # 固定シフト（曜日固定パターン）によるプリフィル
    # 希望が未提出の日のみ、登録済みの固定パターンから初期値を補完する
    if current_emp and current_emp.fixed_patterns:
        from utils.shift_patterns import default_pattern_from_fixed
        for d in dates:
            key = (current_emp.id, str(d))
            if key in req_map:
                continue
            fp = current_emp.get_pattern(d.weekday())
            if not fp:
                continue
            pid = default_pattern_from_fixed(fp.breakfast, fp.dinner)
            if pid:
                req_map[key] = ShiftRequest(employee_id=current_emp.id, date=str(d), pattern_id=pid)

    # 入力済み従業員数（社員は出勤前提なので常にカウント）
    filled_emp_ids = {r.employee_id for r in requests_list}
    filled_count = sum(
        1 for e in employees
        if e.id in filled_emp_ids or e.employment_type == EmploymentType.FULL_TIME
    )

    # アルバイトの場合のみ過去パターン提案を計算
    top_patterns = []
    dow_suggestions = {}
    if current_emp and current_emp.employment_type != EmploymentType.FULL_TIME:
        try:
            top_patterns = [
                (pid, cnt) for pid, cnt in repo.get_employee_pattern_history(current_emp.id)
                if pid in PATTERN_MAP
            ]
            dow_suggestions = repo.get_employee_dow_patterns(current_emp.id)
        except Exception:
            pass

    return render_template(
        "shifts/input.html",
        period=period,
        employees=employees,
        dates=dates,
        req_map=req_map,
        patterns=ALL_PATTERNS,
        emp_idx=emp_idx,
        current_emp=current_emp,
        prev_idx=prev_idx,
        next_idx=next_idx,
        filled_count=filled_count,
        filled_emp_ids=filled_emp_ids,
        DAY_JP=["月", "火", "水", "木", "金", "土", "日"],
        top_patterns=top_patterns,
        dow_suggestions=dow_suggestions,
        PATTERN_MAP=PATTERN_MAP,
        PAT_CATS=PAT_CATS,
        holidays=holiday_set(dates),
    )


@bp.post("/<int:period_id>/save")
def save(period_id):
    period = repo.get_period(period_id)
    if not period:
        flash("期間が見つかりません", "error")
        return redirect(url_for("shifts.index"))
    employees = repo.get_all_employees(active_only=True)
    dates = period.date_range()

    # フォームに含まれる従業員のみ保存（1人モード対応）
    emp_id_filter = request.form.get("save_emp_id", type=int)
    target_emps = [e for e in employees if emp_id_filter is None or e.id == emp_id_filter]

    # 保存対象従業員の旧データを先に削除（UPSERT のみでは空日付の旧レコードが残存する）
    for emp in target_emps:
        repo.delete_shift_requests_for_employee(period_id, emp.id)

    new_requests = []
    for emp in target_emps:
        for d in dates:
            pid = request.form.get(f"pattern_{emp.id}_{d}")
            # フォームに存在しない（= 未送信）日付のみスキップ。
            # 空文字列（アルバイトの「休み」/社員の「出勤予定」）は
            # 「希望提出済み」を表す有効な値として保存する。
            if pid is None:
                continue
            cs = request.form.get(f"custom_start_{emp.id}_{d}") or None
            ce = request.form.get(f"custom_end_{emp.id}_{d}") or None
            new_requests.append(ShiftRequest(
                employee_id=emp.id,
                date=str(d),
                pattern_id=pid or None,
                custom_start=cs,
                custom_end=ce,
            ))

    repo.save_shift_requests(period_id, new_requests)
    flash("希望シフトを保存しました", "success")

    # 1人モードなら次の従業員へ
    next_idx = request.form.get("next_emp_idx", type=int)
    if next_idx is not None:
        return redirect(url_for("shifts.input", period_id=period_id, emp_idx=next_idx))
    return redirect(url_for("shifts.input", period_id=period_id))


@bp.get("/<int:period_id>/import")
def import_csv_form(period_id):
    period = repo.get_period(period_id)
    if not period:
        return redirect(url_for("shifts.index"))
    employees = repo.get_all_employees(active_only=True)
    return render_template("shifts/import_csv.html", period=period, employees=employees)


@bp.post("/<int:period_id>/import")
def import_csv(period_id):
    period = repo.get_period(period_id)
    if not period:
        return redirect(url_for("shifts.index"))
    employees = repo.get_all_employees(active_only=True)
    f = request.files.get("csv_file")
    if not f:
        flash("CSVファイルを選択してください", "error")
        return redirect(url_for("shifts.import_csv_form", period_id=period_id))
    merge = request.form.get("merge", "fill")
    try:
        import tempfile, os
        with tempfile.NamedTemporaryFile(delete=False, suffix=".csv") as tmp:
            f.save(tmp.name)
            tmp_path = tmp.name
        from utils.forms_csv_parser import parse_forms_csv
        result = parse_forms_csv(tmp_path, period, employees)
        os.unlink(tmp_path)
    except Exception as e:
        flash(f"CSVの解析に失敗しました: {e}", "error")
        return redirect(url_for("shifts.import_csv_form", period_id=period_id))

    if not result.requests:
        flash("取込み可能なデータがありませんでした", "warning")
        return redirect(url_for("shifts.import_csv_form", period_id=period_id))

    if merge == "overwrite":
        repo.save_shift_requests(period_id, result.requests)
    else:
        # fill: 既存データがない日付のみ追加
        existing = repo.get_shift_requests(period_id)
        existing_keys = {(r.employee_id, r.date) for r in existing}
        new_reqs = [r for r in result.requests if (r.employee_id, r.date) not in existing_keys]
        repo.save_shift_requests(period_id, new_reqs)

    n_matched = len(result.matched)
    flash(f"{n_matched}名分の希望シフトを取込みました", "success")
    if result.unmatched_names:
        flash(f"マッチしなかった名前: {', '.join(result.unmatched_names)}", "warning")
    return redirect(url_for("shifts.input", period_id=period_id))
