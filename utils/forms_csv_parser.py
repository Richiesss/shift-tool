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
    """新形式（GASフォーム）の1日分を処理して ShiftRequest を返す。"""
    avail_val = row.get(cols["avail"], "").strip()
    if not avail_val:
        return None  # 未回答 = スキップ

    if avail_val == "休み":
        return ShiftRequest(
            employee_id=emp.id, date=date_str,
            pattern_id=None, custom_start=None, custom_end=None, note=note,
        )

    if "ダブル" in avail_val:
        return ShiftRequest(
            employee_id=emp.id, date=date_str,
            pattern_id="double", custom_start=None, custom_end=None, note=note,
        )

    # 朝食のみ or ディナーのみ → 対応する時間列を読む
    if "朝食" in avail_val:
        time_val = row.get(cols.get("b", ""), "").strip()
    else:
        time_val = row.get(cols.get("d", ""), "").strip()

    return _resolve_time_value(time_val, date_str, custom_times, note, emp, result, name)


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

    pattern_id = _LABEL_TO_ID.get(value)
    if pattern_id is None and value not in _LABEL_TO_ID:
        result.warnings.append(
            f"「{name}」{date_str}: 選択値「{value}」が不明です（スキップ）"
        )
        return None

    return ShiftRequest(
        employee_id=emp.id, date=date_str,
        pattern_id=pattern_id, custom_start=None, custom_end=None, note=note,
    )


# Google Forms 設問テンプレート文字列
# ─────────────────────────────────────────────────────────────────────────────

def build_form_guide(period: SchedulePeriod, employees=None) -> str:
    """
    指定期間用の Google Forms 設問テンプレートを人間が読める文字列で返す。

    Parameters
    ----------
    period    : 対象シフト期間
    employees : 登録済みスタッフ一覧（Noneの場合は「登録済みスタッフ名」と表記）
    """
    from utils.constants import DAY_OF_WEEK_LABELS

    lines = [
        "═══════════════════════════════════════════════════════",
        f"  Google Forms テンプレート  （対象期間: {period.start_date} ～ {period.end_date}）",
        "═══════════════════════════════════════════════════════",
        "",
        "┌─────────────────────────────────────────────────────",
        "│ 【Q1】お名前  ← プルダウン（任意）",
        "│",
        "│  作成手順: 「プルダウン」を選択 → 選択肢に以下を追加",
        "│",
    ]

    if employees:
        for emp in employees:
            lines.append(f"│    {emp.name}")
    else:
        lines.append("│    （登録済みスタッフ名を1名ずつ追加してください）")

    lines += [
        "│",
        "│  ※ 「必須」はオフ（未回答 = スキップ扱い）",
        "└─────────────────────────────────────────────────────",
        "",
    ]

    # 日付を週ごとに分割
    all_dates = list(period.date_range())
    weeks: list[list] = []
    chunk: list = []
    for d in all_dates:
        chunk.append(d)
        if d.weekday() == 6 or d == all_dates[-1]:  # 日曜 or 最終日
            weeks.append(chunk)
            chunk = []

    # 選択肢（グリッドの列）
    choice_lines = ["    休み（出勤不可）"]
    for p in ALL_PATTERNS:
        if p.id != "custom":
            choice_lines.append(f"    {p.label}")
    choice_lines.append("    カスタム（備考に時刻を記入）")

    for week_idx, week_dates in enumerate(weeks):
        start_d = week_dates[0]
        end_d   = week_dates[-1]
        label   = f"第{week_idx+1}週（{start_d.month}/{start_d.day}〜{end_d.month}/{end_d.day}）の出勤希望"

        lines += [
            "┌─────────────────────────────────────────────────────",
            f"│ 【グリッドQ{week_idx+2}】{label}",
            "│     ← 選択式グリッド（任意）",
            "│",
            "│  作成手順:",
            "│    1. 「選択式グリッド」を選択",
            f"│    2. 質問文: 「{label}」",
            "│    3. 「各行に回答を必須にする」は オフ",
            "│",
            "│  ▼ 行（日付）をこの順に追加:",
        ]
        for d in week_dates:
            dow = DAY_OF_WEEK_LABELS[d.weekday()]
            lines.append(f"│    {d.month}月{d.day}日({dow})")

        lines += [
            "│",
            "│  ▼ 列（シフト選択肢）をこの順に追加:",
        ]
        lines += [f"│  {c}" for c in choice_lines]
        lines += [
            "│",
            "│  ※ グリッドの列選択肢は全週で共通（コピー＆ペーストで作成可）",
            "└─────────────────────────────────────────────────────",
            "",
        ]

    lines += [
        "┌─────────────────────────────────────────────────────",
        "│ 【最終Q】カスタム入力の時刻  ← 記述式（長文）・任意",
        "│",
        "│  質問文: 「カスタムを選んだ日の時刻を入力してください」",
        "│  説明文: 「日付と時刻をセットで記入してください",
        "│           例: 4/1 10:00〜16:00  4/5 13:00〜22:00",
        "│           （カンマ・改行・スペース区切り、どれでも可）」",
        "└─────────────────────────────────────────────────────",
        "",
        "┌─────────────────────────────────────────────────────",
        "│ 【任意Q】その他備考  ← 記述式（長文）・任意",
        "│  質問文: 「その他、シフトに関する連絡事項があれば記入してください」",
        "└─────────────────────────────────────────────────────",
        "",
        "═══════════════════════════════════════════════════════",
        "  【CSV 取り込み手順】",
        "  1. フォームの回答タブ → スプレッドシートにリンク",
        "  2. スプレッドシートを開く",
        "  3. ファイル → ダウンロード → CSV（.csv）",
        "  4. このアプリの「希望シフト入力」画面で「Google Forms CSV をインポート」",
        "═══════════════════════════════════════════════════════",
    ]
    return "\n".join(lines)
