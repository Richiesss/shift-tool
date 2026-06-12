"""Google Forms CSV インポートパーサー

期待する CSV フォーマット（Google Forms 回答のスプレッドシートを CSV 出力）:
  列1: タイムスタンプ（無視）
  列2: お名前（スタッフ登録名と完全一致）
  列3〜: "4月1日(月)の出勤希望" 形式の日付列（プルダウン値）
  末尾: カスタム入力の時刻（任意）、備考（任意）
"""
from __future__ import annotations
import csv
import re
from dataclasses import dataclass, field
from typing import Optional
from models.schedule import ShiftRequest, SchedulePeriod
from models.employee import Employee
from utils.shift_patterns import ALL_PATTERNS


# ラベル → パターンID の逆引きマップ（Google Forms 選択肢ラベルと一致させる）
_LABEL_TO_ID: dict[str, Optional[str]] = {
    "休み（出勤不可）": None,
    "休み": None,
    "カスタム（備考に時刻を記入）": "custom",
    "カスタム": "custom",
}
for _p in ALL_PATTERNS:
    _LABEL_TO_ID[_p.label] = _p.id


@dataclass
class ImportResult:
    requests: list[ShiftRequest] = field(default_factory=list)
    # スタッフ名 → 取得件数（マッチしたスタッフのみ）
    matched: dict[str, int]      = field(default_factory=dict)
    unmatched_names: list[str]   = field(default_factory=list)
    warnings: list[str]          = field(default_factory=list)


def _parse_date_from_header(header: str, period: SchedulePeriod) -> Optional[str]:
    """
    「4月1日(月)の出勤希望」などのヘッダーから YYYY-MM-DD を返す。
    期間に含まれない日付は None を返す。
    """
    m = re.search(r'(\d{1,2})月(\d{1,2})日', header)
    if not m:
        return None
    month, day = int(m.group(1)), int(m.group(2))
    for d in period.date_range():
        if d.month == month and d.day == day:
            return d.isoformat()
    return None


def _parse_numbered_shift_header(header: str, period: SchedulePeriod) -> Optional[str]:
    """
    「希望シフト [N]」形式のヘッダーから YYYY-MM-DD を返す。
    N は 1 始まりで period.start_date から N-1 日後に対応する。
    例: 「希望シフト [3]」→ period.start_date + 2 days
    """
    m = re.search(r'\[(\d+)\]', header)
    if not m:
        return None
    n = int(m.group(1))
    dates = list(period.date_range())
    if 1 <= n <= len(dates):
        return dates[n - 1].isoformat()
    return None


def _time_range_to_pattern(time_str: str) -> tuple[Optional[str], Optional[str], Optional[str]]:
    """
    「6:00~10:00」などの時刻範囲文字列を (pattern_id, custom_start, custom_end) に変換する。

    - 既存パターンの start/end と完全一致 → (pattern_id, None, None)
    - 一致なし                            → ("custom", start, end)
    - 解析不能                            → (None, None, None)

    対応フォーマット: HH:MM ~ HH:MM（半角・全角どちらも可）
    """
    import unicodedata
    s = unicodedata.normalize('NFKC', time_str.strip())
    m = re.match(r'^(\d{1,2}:\d{2})\s*[~〜～\-–—]+\s*(\d{1,2}:\d{2})$', s)
    if not m:
        return None, None, None

    def _norm(t: str) -> str:
        h, mn = t.split(':')
        return f"{int(h):02d}:{mn}"

    start = _norm(m.group(1))
    end   = _norm(m.group(2))

    for p in ALL_PATTERNS:
        if p.start and p.end:
            if _norm(p.start) == start and _norm(p.end) == end:
                return p.id, None, None

    # 既存パターンに一致しない → カスタムとして時刻を保持
    return "custom", start, end


def _parse_custom_times(raw: str, period: SchedulePeriod) -> dict[str, tuple[str, str]]:
    """
    「4/1: 10:00〜16:00, 4/5: 11:00〜20:00」などを解析して
    {date_str: (start, end)} を返す。

    対応フォーマット:
      - 4/1: 10:00〜16:00
      - 4月1日: 10:00〜16:00
      - 4.1 10:00〜16:00
      - 4/1 10:00-16:00
      - 4/1 10:00～16:00
      - 4/1 10:00–16:00  （en dash）
      - 全角数字・全角コロン（自動で半角に変換）
    """
    import unicodedata
    # 全角英数記号を半角に統一（１→1、：→: など）
    raw = unicodedata.normalize('NFKC', raw)

    result: dict[str, tuple[str, str]] = {}
    pattern = re.findall(
        r'(\d{1,2})[/月.\-](\d{1,2})[日]?'   # 日付: 4/1 4月1日 4.1 4-1
        r'[\s:：]*'                             # 区切り (コロン・スペース・なし)
        r'(\d{1,2}:\d{2})'                     # 開始時刻
        r'\s*[〜~～\-–—]+\s*'                  # 範囲記号
        r'(\d{1,2}:\d{2})',                    # 終了時刻
        raw,
    )
    for month_s, day_s, start, end in pattern:
        for d in period.date_range():
            if d.month == int(month_s) and d.day == int(day_s):
                result[d.isoformat()] = (start, end)
                break
    return result


def parse_forms_csv(
    csv_path: str,
    period: SchedulePeriod,
    employees: list[Employee],
) -> ImportResult:
    """
    Google Forms の回答 CSV を解析して ImportResult を返す。

    2種類のフォーム形式に自動対応:

    【旧形式】グリッド質問 / 1日1列
        列ヘッダー例: 「4月1日(月)の出勤希望」
        値: パターンラベル直接

    【新形式】GASフォーム / 1日3列
        列ヘッダー例:
          「4月1日（月）は出勤できますか？」     → 出勤可否列
          「4月1日（月）朝食の希望シフトを…」   → 朝食時間列
          「4月1日（月）ディナーの希望シフトを…」→ ディナー時間列
        値:
          可否列: 「朝食のみ」「ディナーのみ」「ダブル（朝食＋ディナー両方）」「休み」
          時間列: パターンラベル or「その他（備考欄に時刻を記入）」
    """
    result = ImportResult()
    name_to_emp: dict[str, Employee] = {e.name: e for e in employees}

    try:
        with open(csv_path, encoding="utf-8-sig", newline="") as f:
            reader = csv.DictReader(f)
            headers = list(reader.fieldnames or [])

            # ── 名前列を特定 ─────────────────────────────────────────
            name_col = next(
                (h for h in headers if "名前" in h or "氏名" in h), None
            )
            if not name_col:
                result.warnings.append(
                    "名前列が見つかりませんでした。"
                    "「お名前」または「氏名」という質問があるか確認してください。"
                )
                return result

            # 備考列・カスタム時刻列
            note_col   = next((h for h in headers if "備考" in h or "コメント" in h), None)
            custom_col = next((h for h in headers if "カスタム" in h and "時刻" in h), None)

            # ── 日付列を分類 ─────────────────────────────────────────
            # date_str → {"avail": col, "b": col, "d": col, "direct": col}
            date_cols: dict[str, dict[str, str]] = {}
            for h in headers:
                ds = _parse_date_from_header(h, period)
                if not ds:
                    ds = _parse_numbered_shift_header(h, period)  # 「希望シフト [N]」形式
                if not ds:
                    continue
                if ds not in date_cols:
                    date_cols[ds] = {}
                if "出勤できますか" in h:
                    date_cols[ds]["avail"] = h          # 新形式: 出勤可否
                elif "朝食" in h and "シフト" in h:
                    date_cols[ds]["b"] = h              # 新形式: 朝食時間
                elif "ディナー" in h and "シフト" in h:
                    date_cols[ds]["d"] = h              # 新形式: ディナー時間
                else:
                    date_cols[ds]["direct"] = h         # 旧形式: パターン直接選択

            if not date_cols:
                result.warnings.append(
                    "日付列が見つかりませんでした。"
                    "ヘッダーに「4月1日」のような日付が含まれているか確認してください。"
                )

            # ── 行ごとに処理 ─────────────────────────────────────────
            for row_num, row in enumerate(reader, start=2):
                name = row.get(name_col, "").strip()
                if not name:
                    result.warnings.append(f"行{row_num}: 名前が空です（スキップ）")
                    continue

                emp = name_to_emp.get(name)
                if emp is None:
                    if name not in result.unmatched_names:
                        result.unmatched_names.append(name)
                    result.warnings.append(
                        f"「{name}」はスタッフ管理に登録されていません（スキップ）"
                    )
                    continue

                # 備考・カスタム時刻を解析
                note = row.get(note_col, "").strip() if note_col else ""
                custom_times: dict[str, tuple[str, str]] = {}
                if note:
                    custom_times.update(_parse_custom_times(note, period))
                if custom_col and row.get(custom_col, "").strip():
                    custom_times.update(_parse_custom_times(row[custom_col], period))

                row_count = 0

                for date_str, cols in date_cols.items():
                    req = None

                    if "avail" in cols:
                        # ── 新形式: 可否列 + 時間列 ──────────────────
                        req = _process_new_fmt(
                            row, date_str, cols, custom_times, note, emp, result, name
                        )
                    elif "direct" in cols:
                        # ── 旧形式: 直接パターン選択 ─────────────────
                        req = _process_direct_fmt(
                            row, date_str, cols["direct"], custom_times, note, emp, result, name
                        )

                    if req is not None:
                        result.requests.append(req)
                        row_count += 1

                result.matched[name] = result.matched.get(name, 0) + row_count

    except UnicodeDecodeError:
        try:
            with open(csv_path, encoding="cp932", newline="") as f2:
                reader2 = csv.DictReader(f2)
                # 簡易リトライ: cp932 でそのまま再帰
                result.warnings.append(
                    "UTF-8 で読み込めなかったため cp932 で再試行しました。"
                    "次回からは CSV を UTF-8 で保存することを推奨します。"
                )
        except Exception as e:
            result.warnings.append(f"文字コードエラー: {e}。CSV を UTF-8 で保存し直してください。")
    except Exception as e:
        result.warnings.append(f"CSV 解析エラー: {e}")

    return result


def _process_new_fmt(
    row: dict,
    date_str: str,
    cols: dict[str, str],
    custom_times: dict,
    note: str,
    emp,
    result: "ImportResult",
    name: str,
) -> "Optional[ShiftRequest]":
    """新形式（GAS/API分岐フォーム）の1日分を処理して ShiftRequest を返す。"""
    avail_val = row.get(cols["avail"], "").strip()
    if not avail_val:
        return None  # 未回答 = スキップ

    if avail_val in ("いいえ（休み）", "いいえ", "休み"):
        return ShiftRequest(
            employee_id=emp.id, date=date_str,
            pattern_id=None, custom_start=None, custom_end=None, note=note,
        )

    b_val = row.get(cols.get("b", ""), "").strip() if "b" in cols else ""
    d_val = row.get(cols.get("d", ""), "").strip() if "d" in cols else ""

    # 以前の「朝食のみ」「ディナーのみ」「ダブル」の選択肢だった場合の互換処理
    if "ダブル" in avail_val:
        return ShiftRequest(
            employee_id=emp.id, date=date_str,
            pattern_id="double", custom_start=None, custom_end=None, note=note,
        )
    elif "朝食" in avail_val and "ディナー" not in avail_val:
        return _resolve_time_value(b_val, date_str, custom_times, note, emp, result, name)
    elif "ディナー" in avail_val and "朝食" not in avail_val:
        return _resolve_time_value(d_val, date_str, custom_times, note, emp, result, name)

    # 新形式（はい ➔ 朝食・ディナー両方をチェック）
    has_b = bool(b_val and b_val != "休み（出勤不可）" and "休み" not in b_val and "休み" not in b_val)
    has_d = bool(d_val and d_val != "休み（出勤不可）" and "休み" not in d_val and "休み" not in d_val)

    if has_b and has_d:
        return ShiftRequest(
            employee_id=emp.id, date=date_str,
            pattern_id="double", custom_start=None, custom_end=None, note=note,
        )
    elif has_b:
        return _resolve_time_value(b_val, date_str, custom_times, note, emp, result, name)
    elif has_d:
        return _resolve_time_value(d_val, date_str, custom_times, note, emp, result, name)
    else:
        # 時間帯に有効な値がない、または「休み」が選ばれている場合
        return ShiftRequest(
            employee_id=emp.id, date=date_str,
            pattern_id=None, custom_start=None, custom_end=None, note=note,
        )


def _process_direct_fmt(
    row: dict,
    date_str: str,
    col: str,
    custom_times: dict,
    note: str,
    emp,
    result: "ImportResult",
    name: str,
) -> "Optional[ShiftRequest]":
    """旧形式（直接パターン選択）の1日分を処理して ShiftRequest を返す。"""
    value = row.get(col, "").strip()
    if not value:
        return None
    if value in ("休み（出勤不可）", "休み"):
        return ShiftRequest(
            employee_id=emp.id, date=date_str,
            pattern_id=None, custom_start=None, custom_end=None, note=note,
        )
    return _resolve_time_value(value, date_str, custom_times, note, emp, result, name)


def _resolve_time_value(
    value: str,
    date_str: str,
    custom_times: dict,
    note: str,
    emp,
    result: "ImportResult",
    name: str,
) -> "Optional[ShiftRequest]":
    """パターンラベル文字列を ShiftRequest に変換する共通処理。"""
    if not value:
        return None

    if "その他" in value or value in ("カスタム（備考欄に時刻を記入）", "カスタム"):
        cs = custom_times.get(date_str)
        if cs:
            custom_start, custom_end = cs
        else:
            custom_start = custom_end = None
            result.warnings.append(
                f"「{name}」{date_str}: 「その他/カスタム」選択ですが備考欄に時刻が見つかりません"
            )
        return ShiftRequest(
            employee_id=emp.id, date=date_str,
            pattern_id="custom", custom_start=custom_start, custom_end=custom_end, note=note,
        )

    # ラベルで直接一致を試みる
    if value in _LABEL_TO_ID:
        return ShiftRequest(
            employee_id=emp.id, date=date_str,
            pattern_id=_LABEL_TO_ID[value], custom_start=None, custom_end=None, note=note,
        )

    # 「HH:MM~HH:MM」形式の時刻範囲を試みる（例: 6:00~10:00）
    if re.match(r'^\d{1,2}:\d{2}', value):
        pid, cs, ce = _time_range_to_pattern(value)
        if pid is not None:
            return ShiftRequest(
                employee_id=emp.id, date=date_str,
                pattern_id=pid, custom_start=cs, custom_end=ce, note=note,
            )

    result.warnings.append(
        f"「{name}」{date_str}: 選択値「{value}」が不明です（スキップ）"
    )
    return None


# Google Forms 設問テンプレート文字列
# ─────────────────────────────────────────────────────────────────────────────

def build_form_guide(period: SchedulePeriod, employees=None) -> str:
    """
    指定期間用の Google Forms 設問テンプレートを人間が読める文字列で返す。

    Parameters
    ----------
    period    : 対象シフト期間
    employees : 登録済みスタッフ一覧（None の場合は「スタッフ名を追加」と表記）
    """
    from utils.constants import DAY_OF_WEEK_LABELS

    all_dates = list(period.date_range())
    n = len(all_dates)

    lines = [
        "═══════════════════════════════════════════════════════",
        f"  Google Forms テンプレート  （対象期間: {period.start_date} ～ {period.end_date}）",
        "═══════════════════════════════════════════════════════",
        "",
        "【フォーム構成】",
        "  Q1: お名前（プルダウン）",
        f"  Q2: 希望シフト（選択式グリッド・{n}行×シフト列）",
        "  Q3: 備考（記述式・任意）",
        "",
        "─────────────────────────────────────────────────────",
        "【Q1】お名前  ← プルダウン",
        "─────────────────────────────────────────────────────",
        "  作成手順: 「プルダウン」を選択 → 選択肢に以下を追加",
        "",
    ]

    if employees:
        for emp in employees:
            lines.append(f"    {emp.name}")
    else:
        lines.append("    （登録済みスタッフ名を1名ずつ追加してください）")

    lines += [
        "",
        "─────────────────────────────────────────────────────",
        "【Q2】希望シフト  ← 選択式グリッド",
        "─────────────────────────────────────────────────────",
        "  作成手順:",
        "    1. 「選択式グリッド」を選択",
        "    2. 質問文: 「希望シフト」",
        "    3. 「各行に回答を必須にする」は オフ（未回答 = 休み扱い）",
        "",
        "  ▼ 行ラベル（日付を上から順に追加）:",
    ]

    for i, d in enumerate(all_dates, start=1):
        dow = DAY_OF_WEEK_LABELS[d.weekday()]
        lines.append(f"    {i}  （{d.month}/{d.day}・{dow}）")

    lines += [
        "",
        "  ▼ 列ラベル（シフト時間帯を左から順に追加）:",
        "    ※ 店舗の実際の勤務時間帯に合わせて自由に設定してください",
        "    ※ 以下はアプリに登録済みの標準パターン例:",
        "",
    ]

    for p in ALL_PATTERNS:
        if p.id not in ("custom", "double") and p.start and p.end:
            lines.append(f"    {p.start}~{p.end}")

    lines += [
        "    休み",
        "",
        "  ⚠️  列ラベルは「HH:MM~HH:MM」形式で入力してください",
        "     （例: 6:00~10:00）",
        "     アプリに登録されていない時間帯はカスタムとして自動登録されます",
        "",
        "─────────────────────────────────────────────────────",
        "【Q3】備考  ← 記述式（長文）・任意",
        "─────────────────────────────────────────────────────",
        "  質問文: 「その他、シフトに関する連絡事項があれば記入してください」",
        "",
        "═══════════════════════════════════════════════════════",
        "  【CSV 取り込み手順】",
        "  1. フォームの回答タブ →「スプレッドシートにリンク」",
        "  2. スプレッドシートを開く",
        "  3. ファイル → ダウンロード → CSV（.csv）",
        "  4. このアプリの「希望シフト入力」画面で",
        "     「Google Forms CSV をインポート」から読み込む",
        "═══════════════════════════════════════════════════════",
    ]
    return "\n".join(lines)


def parse_google_form_responses(
    form_schema: dict,
    responses: list[dict],
    period: SchedulePeriod,
    employees: list[Employee],
) -> ImportResult:
    """
    Google Forms API のレスポンス (JSON) を解析して ImportResult を返す。
    """
    result = ImportResult()
    name_to_emp: dict[str, Employee] = {e.name: e for e in employees}

    question_id_to_title = {}
    titles = []
    name_col = None
    note_col = None

    items = form_schema.get("items", [])
    for item in items:
        title = item.get("title", "")
        q_item = item.get("questionItem")
        if q_item and "question" in q_item:
            q_id = q_item["question"]["questionId"]
            question_id_to_title[q_id] = title
            titles.append(title)
            
            if "名前" in title or "氏名" in title:
                name_col = title
            elif "備考" in title or "連絡事項" in title:
                note_col = title

    if not name_col:
        result.warnings.append("お名前を選択する質問が見つかりませんでした。")
        return result

    # 日付ごとの設問を特定
    date_cols: dict[str, dict[str, str]] = {}
    for title in titles:
        ds = _parse_date_from_header(title, period)
        if not ds:
            continue
        if ds not in date_cols:
            date_cols[ds] = {}
        if "出勤できますか" in title:
            date_cols[ds]["avail"] = title
        elif "朝食" in title and "シフト" in title:
            date_cols[ds]["b"] = title
        elif "ディナー" in title and "シフト" in title:
            date_cols[ds]["d"] = title
        else:
            date_cols[ds]["direct"] = title

    for idx, resp in enumerate(responses, start=1):
        answers = resp.get("answers", {})
        row = {}
        for q_id, ans in answers.items():
            title = question_id_to_title.get(q_id)
            if not title:
                continue
            text_answers = ans.get("textAnswers", {}).get("answers", [])
            if text_answers:
                # 今回はラジオ・プルダウン・記述なので最初の1つを取得
                val = text_answers[0].get("value", "")
                row[title] = val.strip()

        name = row.get(name_col, "").strip()
        if not name:
            result.warnings.append(f"回答{idx}: 名前が空です（スキップ）")
            continue

        emp = name_to_emp.get(name)
        if emp is None:
            if name not in result.unmatched_names:
                result.unmatched_names.append(name)
            result.warnings.append(
                f"「{name}」はスタッフ管理に登録されていません（スキップ）"
            )
            continue

        # 備考・カスタム時刻を解析
        note = row.get(note_col, "").strip() if note_col else ""
        custom_times: dict[str, tuple[str, str]] = {}
        if note:
            custom_times.update(_parse_custom_times(note, period))

        row_count = 0
        for date_str, cols in date_cols.items():
            req = None
            if "avail" in cols:
                req = _process_new_fmt(
                    row, date_str, cols, custom_times, note, emp, result, name
                )
            elif "direct" in cols:
                req = _process_direct_fmt(
                    row, date_str, cols["direct"], custom_times, note, emp, result, name
                )

            if req is not None:
                result.requests.append(req)
                row_count += 1

        result.matched[name] = result.matched.get(name, 0) + row_count

    return result

