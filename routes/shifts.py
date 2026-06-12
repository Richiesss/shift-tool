import calendar
from datetime import date, timedelta
from flask import Blueprint, render_template, request, redirect, url_for, flash, jsonify
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
    shown = recent if not show_all else sorted_periods
    # 提出状況バッジ用: {period_id: 提出済みスタッフ数}
    n_active = len(repo.get_all_employees(active_only=True))
    submitted_counts = {
        p.id: len({r.employee_id for r in repo.get_shift_requests(p.id)})
        for p in shown
    }
    return render_template("shifts/index.html",
                           periods=shown,
                           has_older=bool(older),
                           show_all=show_all,
                           submitted_counts=submitted_counts,
                           n_active=n_active)


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

    # 「おまかせ」スタッフのプリフィル
    # 希望が未提出の日のみ、いつでも出勤可として全日「希望あり」を自動補完表示する
    # （DBへの保存は行わず表示のみ。ソルバーは always_available_* フラグを直接参照して
    #   常時出勤可として扱うため、この補完表示はあくまでユーザーへの可視化が目的）
    cur_omakase_breakfast = bool(
        current_emp and current_emp.employment_type != EmploymentType.FULL_TIME
        and current_emp.always_available_breakfast
    )
    cur_omakase_dinner = bool(
        current_emp and current_emp.employment_type != EmploymentType.FULL_TIME
        and current_emp.always_available_dinner
    )
    if cur_omakase_breakfast or cur_omakase_dinner:
        from utils.shift_patterns import default_pattern_from_fixed
        pid = default_pattern_from_fixed(cur_omakase_breakfast, cur_omakase_dinner)
        if pid:
            for d in dates:
                key = (current_emp.id, str(d))
                if key in req_map:
                    continue
                req_map[key] = ShiftRequest(employee_id=current_emp.id, date=str(d), pattern_id=pid)

    # 入力済み従業員数
    # ・社員は出勤前提なので常にカウント
    # ・朝食/ディナーともに「おまかせ」（always_available_*）なスタッフは、
    #   どの曜日もどの時間帯でアサインされても構わないと表明済みのため、
    #   希望シフトの提出が不要＝常に「入力済み」として扱う
    filled_emp_ids = {r.employee_id for r in requests_list}
    filled_count = sum(
        1 for e in employees
        if e.id in filled_emp_ids
        or e.employment_type == EmploymentType.FULL_TIME
        or (e.always_available_breakfast and e.always_available_dinner)
    )

    # アルバイトの場合のみ過去パターン提案を計算
    top_patterns = []
    dow_suggestions = {}
    if current_emp and current_emp.employment_type != EmploymentType.FULL_TIME:
        try:
            top_patterns = [
                (pid, cnt, cs, ce)
                for pid, cnt, cs, ce in repo.get_employee_pattern_history(current_emp.id)
                if pid in PATTERN_MAP and (pid != "custom" or (cs and ce))
            ]
            dow_suggestions = repo.get_employee_dow_patterns(current_emp.id)
        except Exception:
            pass

    # 選択されているスタッフの提出時の備考（note）を取得する
    emp_note = ""
    if current_emp:
        for r in requests_list:
            if r.employee_id == current_emp.id and r.note:
                emp_note = r.note
                break

    # Google 連携情報
    google_token = repo.get_google_token()
    is_google_linked = google_token is not None
    from routes.settings_routes import _get_google_client_credentials
    client_id, client_secret = _get_google_client_credentials()
    has_google_credentials = bool(client_id and client_secret)

    return render_template(
        "shifts/input.html",
        period=period,
        employees=employees,
        dates=dates,
        req_map=req_map,
        patterns=ALL_PATTERNS,
        emp_idx=emp_idx,
        current_emp=current_emp,
        cur_omakase_breakfast=cur_omakase_breakfast,
        cur_omakase_dinner=cur_omakase_dinner,
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
        is_google_linked=is_google_linked,
        has_google_credentials=has_google_credentials,
        emp_note=emp_note,
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


def _csv_tmp_path(token: str) -> str:
    """プレビュー用に保存したCSVの一時パスを返す（トークンは英数字のみ許可）"""
    import tempfile, os, re
    if not re.fullmatch(r"[0-9a-f]{32}", token or ""):
        raise ValueError("不正なトークンです")
    return os.path.join(tempfile.gettempdir(), f"sdu_csv_{token}.csv")


@bp.post("/<int:period_id>/import")
def import_csv(period_id):
    """CSVを解析してプレビューを表示する（この時点では取込まない）"""
    period = repo.get_period(period_id)
    if not period:
        return redirect(url_for("shifts.index"))
    employees = repo.get_all_employees(active_only=True)
    f = request.files.get("csv_file")
    if not f:
        flash("CSVファイルを選択してください", "error")
        return redirect(url_for("shifts.import_csv_form", period_id=period_id))
    merge = request.form.get("merge", "fill")
    import uuid
    token = uuid.uuid4().hex
    tmp_path = _csv_tmp_path(token)
    try:
        f.save(tmp_path)
        from utils.forms_csv_parser import parse_forms_csv
        result = parse_forms_csv(tmp_path, period, employees)
    except Exception as e:
        flash(f"CSVの解析に失敗しました: {e}", "error")
        return redirect(url_for("shifts.import_csv_form", period_id=period_id))

    if not result.requests:
        flash("取込み可能なデータがありませんでした", "warning")
        return redirect(url_for("shifts.import_csv_form", period_id=period_id))

    # fillモードで実際に追加される件数（既存と重複する分はスキップされる）
    existing_keys = {(r.employee_id, r.date) for r in repo.get_shift_requests(period_id)}
    n_new  = sum(1 for r in result.requests if (r.employee_id, r.date) not in existing_keys)
    n_skip = len(result.requests) - n_new

    notes_dict = {}
    emp_map = {e.id: e.name for e in employees}
    for r in result.requests:
        if r.note and r.employee_id in emp_map:
            notes_dict[emp_map[r.employee_id]] = r.note

    return render_template(
        "shifts/import_preview.html",
        period=period, token=token, merge=merge,
        matched=result.matched, unmatched=result.unmatched_names,
        warnings=result.warnings,
        n_total=len(result.requests), n_new=n_new, n_skip=n_skip,
        notes=notes_dict,
    )


@bp.post("/<int:period_id>/import/confirm")
def import_csv_confirm(period_id):
    """プレビュー確認後にCSV取込を実行する"""
    import os
    period = repo.get_period(period_id)
    if not period:
        return redirect(url_for("shifts.index"))
    employees = repo.get_all_employees(active_only=True)
    merge = request.form.get("merge", "fill")
    try:
        tmp_path = _csv_tmp_path(request.form.get("token", ""))
        if not os.path.exists(tmp_path):
            flash("プレビューの有効期限が切れました。もう一度ファイルを選択してください", "warning")
            return redirect(url_for("shifts.import_csv_form", period_id=period_id))
        from utils.forms_csv_parser import parse_forms_csv
        result = parse_forms_csv(tmp_path, period, employees)
        os.unlink(tmp_path)
    except Exception as e:
        flash(f"CSVの取込に失敗しました: {e}", "error")
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


# ── Google Forms 連携 ───────────────────────────────────────────────────────

def _google_tmp_path(token: str) -> str:
    """プレビュー用に保存したGoogleレスポンス結果(JSON)の一時パスを返す（トークンは英数字のみ許可）"""
    import tempfile, os, re
    if not re.fullmatch(r"[0-9a-f]{32}", token or ""):
        raise ValueError("不正なトークンです")
    return os.path.join(tempfile.gettempdir(), f"sdu_google_{token}.json")


@bp.post("/<int:period_id>/google/create")
def google_create_form(period_id):
    """Google フォームを自動作成する"""
    period = repo.get_period(period_id)
    if not period:
        flash("期間が見つかりません", "error")
        return redirect(url_for("shifts.index"))
        
    google_token = repo.get_google_token()
    if not google_token:
        flash("Googleアカウントとの連携が設定されていません。「設定」画面から連携を行ってください。", "error")
        return redirect(url_for("shifts.input", period_id=period_id))

    # クライアントID/シークレットの取得
    from routes.settings_routes import _get_google_client_credentials
    client_id, client_secret = _get_google_client_credentials()
    if not client_id or not client_secret:
        flash("Google API 資格情報が設定されていません。", "error")
        return redirect(url_for("shifts.input", period_id=period_id))

    try:
        from utils.google_forms_api import create_google_form
        employees = repo.get_all_employees(active_only=True)
        # アルバイトのみフォームに入力させる（社員は除外する）
        pt_employees = [e for e in employees if e.employment_type != EmploymentType.FULL_TIME]
        
        # フォーム作成
        form_id, responder_url = create_google_form(period, pt_employees, google_token)
        
        # 期間モデルに google_form_id を保存
        period.google_form_id = form_id
        repo.save_period(period)
        
        flash("Google フォームを自動作成しました！回答URLを配布してシフトを収集してください。", "success")
    except Exception as e:
        flash(f"Google フォームの作成に失敗しました: {e}", "error")

    return redirect(url_for("shifts.input", period_id=period_id))


@bp.post("/<int:period_id>/google/update")
def google_update_form(period_id):
    """Google フォームの内容（タイトル・説明・選択肢）を更新する"""
    period = repo.get_period(period_id)
    if not period or not period.google_form_id:
        flash("Google フォームが作成されていません。", "error")
        return redirect(url_for("shifts.input", period_id=period_id))

    google_token = repo.get_google_token()
    if not google_token:
        flash("Googleアカウントとの連携が設定されていません。「設定」画面から連携を行ってください。", "error")
        return redirect(url_for("shifts.input", period_id=period_id))

    # クライアントID/シークレットの取得
    from routes.settings_routes import _get_google_client_credentials
    client_id, client_secret = _get_google_client_credentials()
    if not client_id or not client_secret:
        flash("Google API 資格情報が設定されていません。", "error")
        return redirect(url_for("shifts.input", period_id=period_id))

    custom_title = request.form.get("title", "").strip()
    custom_desc = request.form.get("description", "").strip()

    try:
        from utils.google_forms_api import update_google_form
        employees = repo.get_all_employees(active_only=True)
        pt_employees = [e for e in employees if e.employment_type != EmploymentType.FULL_TIME]

        update_google_form(
            period.google_form_id,
            period,
            pt_employees,
            google_token,
            custom_title=custom_title if custom_title else None,
            custom_description=custom_desc if custom_desc else None,
        )
        flash("Google フォームの内容を最新の状態に更新しました！", "success")
    except Exception as e:
        flash(f"Google フォームの更新に失敗しました: {e}", "error")

    return redirect(url_for("shifts.input", period_id=period_id))


@bp.get("/<int:period_id>/google/accepting_status")
def google_accepting_status(period_id):
    """フォームの回答受付状態をJSONで返す（画面の非同期表示用）"""
    period = repo.get_period(period_id)
    google_token = repo.get_google_token()
    if not period or not period.google_form_id or not google_token:
        return jsonify({"ok": False}), 404
    try:
        from utils.google_forms_api import get_form_accepting_responses
        accepting = get_form_accepting_responses(period.google_form_id, google_token)
        return jsonify({"ok": True, "accepting": accepting})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@bp.post("/<int:period_id>/google/accepting")
def google_set_accepting(period_id):
    """フォームの回答受付を締め切る/再開する"""
    period = repo.get_period(period_id)
    if not period or not period.google_form_id:
        flash("Google フォームが作成されていません。", "error")
        return redirect(url_for("shifts.input", period_id=period_id))
    google_token = repo.get_google_token()
    if not google_token:
        flash("Googleアカウントとの連携が設定されていません。", "error")
        return redirect(url_for("shifts.input", period_id=period_id))
    accepting = request.form.get("accepting") == "1"
    try:
        from utils.google_forms_api import set_form_accepting_responses
        set_form_accepting_responses(period.google_form_id, google_token, accepting)
        flash("回答の受付を再開しました。" if accepting else "回答の受付を締め切りました。", "success")
    except Exception as e:
        flash(f"受付状態の変更に失敗しました（このフォームが公開設定に対応していない可能性があります）: {e}", "error")
    return redirect(url_for("shifts.input", period_id=period_id))


@bp.post("/<int:period_id>/google/sync")
def google_sync(period_id):
    """Google フォームの回答を取得し、インポートプレビューを表示する"""
    period = repo.get_period(period_id)
    if not period or not period.google_form_id:
        flash("Google フォームが作成されていません。", "error")
        return redirect(url_for("shifts.input", period_id=period_id))

    google_token = repo.get_google_token()
    if not google_token:
        flash("Googleアカウントとの連携が設定されていません。「設定」画面から連携を行ってください。", "error")
        return redirect(url_for("shifts.input", period_id=period_id))

    # クライアントID/シークレットの取得
    from routes.settings_routes import _get_google_client_credentials
    client_id, client_secret = _get_google_client_credentials()
    if not client_id or not client_secret:
        flash("Google API 資格情報が設定されていません。", "error")
        return redirect(url_for("shifts.input", period_id=period_id))

    merge = request.form.get("merge", "fill")
    
    try:
        from utils.google_forms_api import fetch_google_form_responses
        form_schema, responses = fetch_google_form_responses(period.google_form_id, google_token)
        
        from utils.forms_csv_parser import parse_google_form_responses
        employees = repo.get_all_employees(active_only=True)
        result = parse_google_form_responses(form_schema, responses, period, employees)
    except Exception as e:
        flash(f"Google フォームからのデータ同期に失敗しました: {e}", "error")
        return redirect(url_for("shifts.input", period_id=period_id))

    if not result.requests:
        flash("同期可能な回答データがありませんでした。", "warning")
        return redirect(url_for("shifts.input", period_id=period_id))

    # 一時保存用のトークンを発行して結果を JSON ファイルに保存
    import uuid, json
    token = uuid.uuid4().hex
    tmp_path = _google_tmp_path(token)
    
    requests_data = []
    for r in result.requests:
        requests_data.append({
            "employee_id": r.employee_id,
            "date": r.date,
            "pattern_id": r.pattern_id,
            "custom_start": r.custom_start,
            "custom_end": r.custom_end,
            "note": r.note
        })
        
    notes_dict = {}
    emp_map = {e.id: e.name for e in employees}
    for r in result.requests:
        if r.note and r.employee_id in emp_map:
            notes_dict[emp_map[r.employee_id]] = r.note

    payload = {
        "requests": requests_data,
        "matched": result.matched,
        "unmatched_names": result.unmatched_names,
        "warnings": result.warnings,
        "notes": notes_dict
    }
    
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False)

    existing_keys = {(r.employee_id, r.date) for r in repo.get_shift_requests(period_id)}
    n_new  = sum(1 for r in result.requests if (r.employee_id, r.date) not in existing_keys)
    n_skip = len(result.requests) - n_new

    return render_template(
        "shifts/google_import_preview.html",
        period=period, token=token, merge=merge,
        matched=result.matched, unmatched=result.unmatched_names,
        warnings=result.warnings,
        n_total=len(result.requests), n_new=n_new, n_skip=n_skip,
        notes=notes_dict,
    )


@bp.post("/<int:period_id>/google/sync/confirm")
def google_sync_confirm(period_id):
    """プレビュー確認後にGoogleフォーム回答の取込を実行する"""
    import os, json
    period = repo.get_period(period_id)
    if not period:
        return redirect(url_for("shifts.index"))
        
    merge = request.form.get("merge", "fill")
    token = request.form.get("token", "")
    tmp_path = _google_tmp_path(token)
    
    try:
        if not os.path.exists(tmp_path):
            flash("プレビューの有効期限が切れました。もう一度同期を実行してください", "warning")
            return redirect(url_for("shifts.input", period_id=period_id))
            
        with open(tmp_path, encoding="utf-8") as f:
            payload = json.load(f)
            
        os.unlink(tmp_path)
    except Exception as e:
        flash(f"データの同期確定に失敗しました: {e}", "error")
        return redirect(url_for("shifts.input", period_id=period_id))

    # ShiftRequest に戻す
    reqs_data = payload.get("requests", [])
    requests = []
    for r in reqs_data:
        requests.append(ShiftRequest(
            employee_id=r["employee_id"],
            date=r["date"],
            pattern_id=r["pattern_id"],
            custom_start=r["custom_start"],
            custom_end=r["custom_end"],
            note=r["note"]
        ))

    if merge == "overwrite":
        repo.save_shift_requests(period_id, requests)
    else:
        # fill: 既存データがない日付のみ追加
        existing = repo.get_shift_requests(period_id)
        existing_keys = {(r.employee_id, r.date) for r in existing}
        new_reqs = [r for r in requests if (r.employee_id, r.date) not in existing_keys]
        repo.save_shift_requests(period_id, new_reqs)

    n_matched = len(payload.get("matched", {}))
    flash(f"{n_matched}名分の希望シフトをGoogleフォームから同期しました", "success")
    
    unmatched_names = payload.get("unmatched_names", [])
    if unmatched_names:
        flash(f"マッチしなかった名前: {', '.join(unmatched_names)}", "warning")
        
    return redirect(url_for("shifts.input", period_id=period_id))

