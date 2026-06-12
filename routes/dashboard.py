from datetime import date as _date
from collections import defaultdict
from flask import Blueprint, render_template, url_for
from db import repositories as repo
from utils.constants import TimeSlot, Position
from utils.submission import submitted_employee_ids

bp = Blueprint("dashboard", __name__)

_DAY_JP   = ['月', '火', '水', '木', '金', '土', '日']
_SLOT_LBL = {'breakfast': '朝食', 'dinner': 'ディナー'}
_POS_LBL  = {'hall': 'ホール', 'kitchen': 'キッチン'}


@bp.get("/")
def index():
    today     = _date.today()
    today_str = today.isoformat()

    periods   = repo.get_all_periods()
    latest    = periods[0] if periods else None
    all_staff = repo.get_all_employees(active_only=True)
    staff_count = len(all_staff)

    # 戻り値の初期値
    today_res            = {}   # {'breakfast': int, 'dinner': int}
    today_staff          = {}   # {(slot_val, pos_val): count}
    today_short          = []   # 今日の不足区分ラベル
    period_info          = None
    upcoming_short_days  = []   # 直近7日の不足日ラベル
    unsubmitted          = []   # 希望未提出スタッフ
    todos                = []   # TODOアイテム
    res_counts           = {}   # 予約客数（今日の予約客数表示用）

    if not latest:
        todos.append({
            'level': 'warn', 'icon': 'bi-calendar-plus',
            'text': 'シフト期間を作成する',
            'sub':  '期間を設定してシフト管理を開始しましょう',
            'url':  url_for('shifts.index'),
        })
    else:
        assignments  = repo.get_assignments(latest.id)
        dates        = latest.date_range()
        constraints  = repo.get_shift_constraints()
        requests     = repo.get_shift_requests(latest.id)
        res_counts   = repo.get_reservation_counts(latest.id)

        # ── 今日の予約客数 ──
        today_res = res_counts.get(today_str, {})

        # ── 今日の出勤（スロット × ポジション別） ──
        _today_map = defaultdict(int)
        for a in assignments:
            if a.date == today_str:
                _today_map[(a.time_slot.value, a.position.value)] += 1
        today_staff = dict(_today_map)

        # ── 全期間の人員充足チェック ──
        count_map = defaultdict(int)
        for a in assignments:
            count_map[(a.date, a.time_slot.value, a.position.value)] += 1

        shortage_dates = set()
        for d in dates:
            ds = d.isoformat()
            for slot in TimeSlot:
                for pos in Position:
                    c  = constraints.get((slot, pos), {})
                    mn = c.get("min", 0)
                    if mn > 0 and count_map[(ds, slot.value, pos.value)] < mn:
                        shortage_dates.add(ds)
        shortage_count = len(shortage_dates)

        # ── 今日の不足区分 ──
        for slot in TimeSlot:
            for pos in Position:
                c   = constraints.get((slot, pos), {})
                mn  = c.get("min", 0)
                cnt = count_map.get((today_str, slot.value, pos.value), 0)
                if mn > 0 and cnt < mn:
                    today_short.append(
                        f"{_SLOT_LBL[slot.value]}/{_POS_LBL[pos.value]}（{cnt}/{mn}名）"
                    )

        # ── 直近7日の不足日 ──
        for d in dates:
            ds = d.isoformat()
            if ds < today_str:
                continue
            if (d - today).days > 7:
                break
            if ds in shortage_dates:
                upcoming_short_days.append(
                    f"{d.month}/{d.day}({_DAY_JP[d.weekday()]})"
                )

        # ── 希望未提出スタッフ ──
        # （社員・朝食/ディナーともに「おまかせ」のスタッフは希望入力画面と同様に提出済み扱い）
        submitted_ids = submitted_employee_ids(all_staff, requests)
        unsubmitted   = [e for e in all_staff if e.id not in submitted_ids]

        # ── 期間情報 ──
        end       = _date.fromisoformat(latest.end_date)
        days_left = (end - today).days
        needs_regen = repo.get_period_gen_status(latest.id).get("needs_regen", False)

        period_info = {
            'period':          latest,
            'days_left':       days_left,
            'shortage_count':  shortage_count,
            'has_assignments': len(assignments) > 0,
            'request_count':   len(submitted_ids),
            'in_range':        latest.start_date <= today_str <= latest.end_date,
            'needs_regen':     needs_regen,
        }

        # ── TODOリスト自動生成（優先度順） ──
        if shortage_count > 0:
            todos.append({
                'level': 'danger', 'icon': 'bi-exclamation-triangle-fill',
                'text': '人員不足を解消する',
                'sub':  f'{shortage_count}日分に不足あり — シフト表で確認・手動追加',
                'url':  url_for('schedule.index', period_id=latest.id),
            })

        if unsubmitted:
            names = '、'.join(e.name for e in unsubmitted[:5])
            if len(unsubmitted) > 5:
                names += f' 他{len(unsubmitted)-5}名'
            todos.append({
                'level': 'warn', 'icon': 'bi-person-exclamation',
                'text': f'希望シフト未提出 {len(unsubmitted)}名',
                'sub':  names,
                'url':  url_for('shifts.index'),
            })

        if needs_regen:
            todos.append({
                'level': 'warn', 'icon': 'bi-arrow-repeat',
                'text': 'シフトの再生成を推奨',
                'sub':  '予約客数が更新されました',
                'url':  url_for('generate.index'),
            })

        if not period_info['has_assignments']:
            todos.append({
                'level': 'info', 'icon': 'bi-lightning-fill',
                'text': 'シフトを自動生成する',
                'sub':  'まだシフトが作成されていません',
                'url':  url_for('generate.index'),
            })
        elif latest.status == 'draft':
            todos.append({
                'level': 'info', 'icon': 'bi-check-circle',
                'text': 'シフトを確定する',
                'sub':  'シフトが下書き状態です',
                'url':  url_for('schedule.index', period_id=latest.id),
            })

        # 期間中は毎日表示：予約は日々変わるため常に最新化を促す
        if period_info['in_range']:
            todos.append({
                'level': 'info', 'icon': 'bi-cup-hot',
                'text': '予約客数を最新に更新する',
                'sub':  '予約変更があれば今日時点の数字に更新してください',
                'url':  url_for('customers.list_periods'),
            })

    return render_template(
        'dashboard/index.html',
        today=today,
        today_str=today_str,
        staff_count=staff_count,
        period_info=period_info,
        today_res=today_res,
        today_staff=today_staff,
        today_short=today_short,
        unsubmitted=unsubmitted,
        upcoming_short_days=upcoming_short_days,
        todos=todos,
        DAY_JP=_DAY_JP,
    )
