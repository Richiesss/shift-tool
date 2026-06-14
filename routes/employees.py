from flask import Blueprint, render_template, request, redirect, url_for, flash, jsonify
from db import repositories as repo
from models.employee import Employee
from utils.constants import EmploymentType, SkillLevel, PrimaryPosition, TimeSlot

bp = Blueprint("employees", __name__, url_prefix="/employees")


@bp.get("/")
def index():
    show_archive = request.args.get("archive") == "1"
    employees = repo.get_all_employees(active_only=False)
    employees = [e for e in employees if e.is_active != show_archive]
    q = request.args.get("q", "").strip()
    if q:
        employees = [e for e in employees if q in e.name]
    return render_template(
        "employees/index.html",
        employees=employees,
        show_archive=show_archive,
        q=q,
    )


@bp.get("/new")
def new():
    return render_template(
        "employees/form.html",
        emp=None,
        employment_types=EmploymentType,
        skill_levels=SkillLevel,
        primary_positions=PrimaryPosition,
        time_slots=TimeSlot,
    )


@bp.post("/new")
def create():
    emp = _form_to_employee(None)
    repo.save_employee(emp)
    flash(f"{emp.name} を登録しました", "success")
    return redirect(url_for("employees.index"))


@bp.get("/<int:emp_id>/edit")
def edit(emp_id):
    emp = repo.get_employee(emp_id)
    if not emp:
        flash("従業員が見つかりません", "error")
        return redirect(url_for("employees.index"))
    return render_template(
        "employees/form.html",
        emp=emp,
        employment_types=EmploymentType,
        skill_levels=SkillLevel,
        primary_positions=PrimaryPosition,
        time_slots=TimeSlot,
    )


@bp.post("/<int:emp_id>/edit")
def update(emp_id):
    emp = _form_to_employee(emp_id)
    repo.save_employee(emp)
    flash(f"{emp.name} を更新しました", "success")
    return redirect(url_for("employees.index"))


@bp.post("/reorder")
def reorder():
    ids = request.get_json()
    if not isinstance(ids, list):
        return jsonify({"ok": False, "error": "invalid"}), 400
    repo.reorder_employees([int(i) for i in ids])
    return jsonify({"ok": True})


@bp.post("/<int:emp_id>/archive")
def archive(emp_id):
    repo.delete_employee(emp_id)
    flash("アーカイブしました", "success")
    return redirect(url_for("employees.index"))


@bp.post("/<int:emp_id>/purge")
def purge(emp_id):
    emp = repo.get_employee(emp_id)
    name = emp.name if emp else "スタッフ"
    repo.purge_employee(emp_id)
    flash(f"{name} を完全に削除しました", "success")
    return redirect(url_for("employees.index") + "?archive=1")


@bp.post("/<int:emp_id>/restore")
def restore(emp_id):
    repo.restore_employee(emp_id)
    flash("復元しました", "success")
    return redirect(url_for("employees.index") + "?archive=1")


@bp.post("/bulk")
def bulk_action():
    action = request.form.get("action")
    ids = [int(i) for i in request.form.getlist("ids") if i.isdigit()]
    if not ids:
        flash("スタッフを選択してください", "error")
        return redirect(url_for("employees.index"))

    if action == "archive":
        n = repo.delete_employees_bulk(ids)
        flash(f"{n}名をアーカイブしました", "success")
        return redirect(url_for("employees.index"))
    elif action == "restore":
        n = repo.restore_employees_bulk(ids)
        flash(f"{n}名を復元しました", "success")
        return redirect(url_for("employees.index") + "?archive=1")
    elif action == "purge":
        n = repo.purge_employees_bulk(ids)
        flash(f"{n}名を完全に削除しました", "success")
        return redirect(url_for("employees.index") + "?archive=1")

    flash("不正な操作です", "error")
    return redirect(url_for("employees.index"))


def _form_to_employee(emp_id):
    f = request.form
    pp_val = f.get("primary_position") or None
    op_val = f.get("output_position")  or None
    pt_val = f.get("primary_timeslot") or None
    pt_enum = TimeSlot(pt_val) if pt_val else None
    # 所属ポジションが「どちらでも」の場合は兼務可を自動でTrue
    can_work_both = (pp_val is None)
    # 単一ポジション（ホール/キッチン）の場合は output_position を必ず primary_position に合わせる
    # （非表示の radio ボタンが古い値を送信する問題を防ぐ）
    if pp_val:
        op_val = pp_val

    # ディナー専任スタッフは朝食の開店準備・片付けを行わないため、対応可フラグは常にFalseにする
    # （非表示チェックボックスが古い値を送信する問題を防ぐ）
    can_open = bool(f.get("can_open")) and pt_enum != TimeSlot.DINNER
    can_cleanup = bool(f.get("can_cleanup")) and pt_enum != TimeSlot.DINNER

    return Employee(
        id=emp_id,
        name=f["name"].strip(),
        employment_type=EmploymentType(f["employment_type"]),
        hourly_wage=int(f.get("hourly_wage") or 0),
        has_social_insurance=bool(f.get("has_social_insurance")),
        si_allow_short_shift=bool(f.get("si_allow_short_shift")),
        hall_skill_breakfast=SkillLevel(f.get("hall_skill_breakfast") or "beginner"),
        hall_skill_dinner=SkillLevel(f.get("hall_skill_dinner") or "beginner"),
        kitchen_skill_breakfast=SkillLevel(f.get("kitchen_skill_breakfast") or "beginner"),
        kitchen_skill_dinner=SkillLevel(f.get("kitchen_skill_dinner") or "beginner"),
        primary_position=PrimaryPosition(pp_val) if pp_val else None,
        output_position=PrimaryPosition(op_val) if op_val else None,
        primary_timeslot=pt_enum,
        can_work_both_positions=can_work_both,
        can_open=can_open,
        can_cleanup=can_cleanup,
        always_available_breakfast=bool(f.get("always_available_breakfast")),
        always_available_dinner=bool(f.get("always_available_dinner")),
        is_active=True,
    )
