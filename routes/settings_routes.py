import io, json
from datetime import datetime
from flask import Blueprint, render_template, request, redirect, url_for, flash, send_file, jsonify
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
        "ft_hall_breakfast_start",    "ft_hall_breakfast_end",
        "ft_kitchen_breakfast_start", "ft_kitchen_breakfast_end",
        "ft_hall_dinner_start",       "ft_hall_dinner_end",
        "ft_kitchen_dinner_start",    "ft_kitchen_dinner_end",
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


@bp.get("/backup")
def backup():
    """全データをJSONでダウンロード"""
    from db.database import Connection
    conn = Connection()
    tables = [
        "employees","fixed_patterns","fixed_unavailable_dates",
        "schedule_periods","shift_requests","shift_assignments",
        "reservation_counts","schedule_notes","shift_constraints",
        "breakfast_band_constraints","app_settings",
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
    # 依存順でリストア
    restore_order = [
        "employees","fixed_patterns","fixed_unavailable_dates",
        "schedule_periods","shift_requests","shift_assignments",
        "reservation_counts","schedule_notes","shift_constraints",
        "breakfast_band_constraints","app_settings",
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
            for t in ["employees","schedule_periods","shift_requests","shift_assignments"]:
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
