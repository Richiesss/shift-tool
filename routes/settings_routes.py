import io, json, os
from datetime import datetime
from flask import Blueprint, render_template, request, redirect, url_for, flash, send_file, jsonify, session
from cache import cache
from db import repositories as repo
from optimizer import forecast
from utils.constants import TimeSlot, Position
from utils.form_helpers import safe_int
from utils.google_forms_api import get_oauth_credentials

bp = Blueprint("settings", __name__, url_prefix="/settings")

# ローカル環境のHTTPでOAuth2をテストできるようにする
if os.environ.get("FLASK_ENV") == "development" or os.environ.get("FLASK_DEBUG") == "1":
    os.environ['OAUTHLIB_INSECURE_TRANSPORT'] = '1'


def _get_google_client_credentials():
    """環境変数またはDBから Google Client ID と Client Secret を取得する"""
    client_id = os.environ.get("GOOGLE_CLIENT_ID")
    client_secret = os.environ.get("GOOGLE_CLIENT_SECRET")
    if not client_id or not client_secret:
        settings = repo.get_all_app_settings()
        client_id = settings.get("google_client_id", "")
        client_secret = settings.get("google_client_secret", "")
    return client_id.strip(), client_secret.strip()


@bp.get("/")
def index():
    constraints = repo.get_shift_constraints()
    settings = repo.get_all_app_settings()
    band_constraints = repo.get_breakfast_band_constraints()
    from utils.constants import EmploymentType
    all_employees = repo.get_all_employees(active_only=True)
    pt_employees = [e for e in all_employees if e.employment_type != EmploymentType.FULL_TIME]

    # Google 連携情報
    google_token = repo.get_google_token()
    is_google_linked = google_token is not None
    client_id, client_secret = _get_google_client_credentials()
    has_google_credentials = bool(client_id and client_secret)

    return render_template(
        "settings/index.html",
        constraints=constraints,
        settings=settings,
        band_constraints=band_constraints,
        pt_employees=pt_employees,
        TimeSlot=TimeSlot,
        Position=Position,
        is_google_linked=is_google_linked,
        has_google_credentials=has_google_credentials,
        google_client_id=client_id,
        google_client_secret=client_secret,
    )


@bp.post("/save")
def save():
    constraints = repo.get_shift_constraints()
    new_constraints = {}
    for (slot, pos) in constraints:
        prefix = f"{slot.value}_{pos.value}"
        new_constraints[(slot, pos)] = {
            "min": safe_int(request.form.get(f"min_{prefix}")),
            "max": safe_int(request.form.get(f"max_{prefix}")),
            "min_leader": safe_int(request.form.get(f"ml_{prefix}")),
        }
    repo.save_shift_constraints(new_constraints)

    settings_keys = [
        "reserv_threshold_breakfast", "reserv_extra_breakfast",
        "reserv_threshold_breakfast2", "reserv_extra_breakfast2",
        "reserv_threshold_dinner", "reserv_extra_dinner",
        "reserv_threshold_dinner2", "reserv_extra_dinner2",
        "ft_hall_breakfast_start",    "ft_hall_breakfast_end",
        "ft_kitchen_breakfast_start", "ft_kitchen_breakfast_end",
        "ft_hall_dinner_start",       "ft_hall_dinner_end",
        "ft_kitchen_dinner_start",    "ft_kitchen_dinner_end",
        "base_hourly_wage",           "early_morning_allowance",
        "early_allowance_start",      "early_allowance_end",
        "max_consecutive_days",       "min_days_off",
        "store_lat",                  "store_lon",
        "google_client_id",           "google_client_secret",
    ]
    new_settings = {k: request.form.get(k, "") for k in settings_keys}
    new_settings["customer_forecast_enabled"] = "1" if request.form.get("customer_forecast_enabled") else "0"
    repo.save_all_app_settings(new_settings)

    # 緯度・経度を変更したら気象キャッシュ・客数予測キャッシュを破棄して即時反映する
    cache.delete_memoized(forecast.get_weather_map_cached)
    cache.delete_memoized(forecast.predict_customer_counts_cached)

    band_constraints = repo.get_breakfast_band_constraints()
    new_band = {}
    for (band, pos) in band_constraints:
        prefix = f"{band}_{pos}"
        new_band[(band, pos)] = {
            "min":        safe_int(request.form.get(f"band_min_{prefix}")),
            "max":        safe_int(request.form.get(f"band_max_{prefix}")),
            "min_leader": safe_int(request.form.get(f"band_ml_{prefix}")),
        }
    repo.save_breakfast_band_constraints(new_band)

    flash("設定を保存しました", "success")
    return redirect(url_for("settings.index"))


@bp.get("/google/login")
def google_login():
    client_id, client_secret = _get_google_client_credentials()
    if not client_id or not client_secret:
        flash("Google Client ID または Client Secret が設定されていません。", "error")
        return redirect(url_for("settings.index"))

    is_dev = os.environ.get("FLASK_ENV") == "development" or os.environ.get("FLASK_DEBUG") == "1"
    scheme = "http" if is_dev else "https"
    redirect_uri = url_for("settings.google_callback", _external=True, _scheme=scheme)

    flow = get_oauth_credentials(client_id, client_secret, redirect_uri)
    authorization_url, state = flow.authorization_url(
        access_type='offline',
        include_granted_scopes='true',
        prompt='consent'
    )
    session['google_oauth_state'] = state
    
    # PKCE 用の code_verifier をセッションに保存
    if hasattr(flow, 'code_verifier') and flow.code_verifier:
        session['google_code_verifier'] = flow.code_verifier
        
    return redirect(authorization_url)


@bp.get("/google/callback")
def google_callback():
    state = session.get('google_oauth_state')
    if not state or state != request.args.get('state'):
        flash("OAuthのステートが一致しません。もう一度お試しください。", "error")
        return redirect(url_for("settings.index"))

    client_id, client_secret = _get_google_client_credentials()
    is_dev = os.environ.get("FLASK_ENV") == "development" or os.environ.get("FLASK_DEBUG") == "1"
    scheme = "http" if is_dev else "https"
    redirect_uri = url_for("settings.google_callback", _external=True, _scheme=scheme)

    flow = get_oauth_credentials(client_id, client_secret, redirect_uri)
    
    # セッションから code_verifier を復元
    if 'google_code_verifier' in session:
        flow.code_verifier = session.pop('google_code_verifier')

    try:
        # プロキシ環境により request.url が http で入ってくる場合があるため、
        # oauthlib での insecure_transport エラーを回避するために https に強制置換する
        auth_response_url = request.url
        if not is_dev and auth_response_url.startswith("http://"):
            auth_response_url = auth_response_url.replace("http://", "https://", 1)

        flow.fetch_token(authorization_response=auth_response_url)
        creds = flow.credentials
        repo.save_google_token(creds.to_json())
        session.pop('google_oauth_state', None)
        flash("Googleアカウントとの連携に成功しました！", "success")
    except Exception as e:
        flash(f"Googleアカウントの連携に失敗しました: {e}", "error")

    return redirect(url_for("settings.index"))


@bp.get("/google/disconnect")
def google_disconnect():
    repo.delete_google_token()
    flash("Googleアカウントとの連携を解除しました。", "success")
    return redirect(url_for("settings.index"))



@bp.get("/backup")
def backup():
    """全データをJSONでダウンロード"""
    from db.database import Connection
    conn = Connection()
    # google_tokens（Google連携のOAuthトークン）は機密情報のため意図的に対象外とする
    tables = [
        "employees","fixed_patterns","fixed_unavailable_dates",
        "schedule_periods","shift_requests","shift_assignments",
        "reservation_counts","customer_count_history","schedule_notes","staff_notes","cell_notes",
        "shift_constraints","breakfast_band_constraints","app_settings","assignment_log",
    ]
    data = {}
    for t in tables:
        try:
            rows = conn.execute(f"SELECT * FROM {t}").fetchall()
            data[t] = [dict(r) for r in rows]
        except Exception:
            data[t] = []
    conn.close()
    payload = json.dumps(data, ensure_ascii=False, default=str, indent=2)
    fname = f"sdu_shift_backup_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
    return send_file(
        io.BytesIO(payload.encode()),
        as_attachment=True, download_name=fname,
        mimetype="application/json"
    )


@bp.post("/restore")
def restore():
    """JSONファイルからデータを復元"""
    f = request.files.get("backup_file")
    if not f:
        flash("ファイルを選択してください", "error")
        return redirect(url_for("settings.index"))
    try:
        data = json.loads(f.read().decode())
    except Exception:
        flash("JSONファイルの解析に失敗しました", "error")
        return redirect(url_for("settings.index"))

    from db.database import Connection
    from db.seeder import _get_schema
    conn = Connection()
    # 依存順でリストア（google_tokens はバックアップ対象外のため含めない）
    restore_order = [
        "employees","fixed_patterns","fixed_unavailable_dates",
        "schedule_periods","shift_requests","shift_assignments",
        "reservation_counts","customer_count_history","schedule_notes","staff_notes","cell_notes",
        "shift_constraints","breakfast_band_constraints","app_settings","assignment_log",
    ]
    try:
        for table in restore_order:
            rows = data.get(table, [])
            if not rows:
                continue
            # テーブルをクリア
            conn.execute(f"DELETE FROM {table}")
            schema = _get_schema(conn, table)
            cols = [c for c in rows[0].keys() if c in schema]
            ph = "%s" if conn.backend == "postgres" else "?"
            placeholders = ",".join([ph]*len(cols))
            sql = f"INSERT INTO {table} ({','.join(cols)}) VALUES ({placeholders})"
            for row in rows:
                try:
                    conn.execute(sql, [row.get(c) for c in cols])
                except Exception:
                    pass
        # シーケンスリセット（PostgreSQL）
        if conn.backend == "postgres":
            for t in ["employees","schedule_periods","shift_requests","shift_assignments","assignment_log"]:
                try:
                    conn.execute(f"SELECT setval(pg_get_serial_sequence('{t}','id'), COALESCE((SELECT MAX(id) FROM {t}),1))")
                except Exception:
                    pass
        conn.commit()
        # キャッシュクリア
        from cache import cache
        cache.clear()
        flash("バックアップからリストアしました", "success")
    except Exception as e:
        flash(f"リストア失敗: {e}", "error")
    finally:
        conn.close()
    return redirect(url_for("settings.index"))
