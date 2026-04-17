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

    Parameters
    ----------
    csv_path : str
        Google スプレッドシートからダウンロードした CSV のパス
    period : SchedulePeriod
        対象シフト期間
    employees : list[Employee]
        登録済みスタッフ一覧
    """
    result = ImportResult()
    name_to_emp: dict[str, Employee] = {e.name: e for e in employees}

    try:
        with open(csv_path, encoding="utf-8-sig", newline="") as f:
            reader = csv.DictReader(f)
            headers = list(reader.fieldnames or [])

            # ── 列の役割を特定 ────────────────────────────────────────
            # 日付列: ヘッダーに「MM月DD日」が含まれる列
            col_to_date: dict[str, str] = {}
            for h in headers:
                ds = _parse_date_from_header(h, period)
                if ds:
                    col_to_date[h] = ds

            if not col_to_date:
                result.warnings.append(
                    "日付列が見つかりませんでした。"
                    "質問文が「4月1日(月)の出勤希望」の形式になっているか確認してください。"
                )

            # 名前列: 「名前」「氏名」を含む列
            name_col = next(
                (h for h in headers if "名前" in h or "氏名" in h), None
            )
            if not name_col:
                result.warnings.append(
                    "名前列が見つかりませんでした。"
                    "「お名前」または「氏名」という質問があるか確認してください。"
                )
                return result

            # カスタム時刻列: 「カスタム」かつ「時刻」を含む列
            custom_col = next(
                (h for h in headers if "カスタム" in h and "時刻" in h), None
            )
            # 備考列
            note_col = next(
                (h for h in headers if "備考" in h or "コメント" in h), None
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

                # カスタム時刻を解析
                custom_times: dict[str, tuple[str, str]] = {}
                if custom_col and row.get(custom_col, "").strip():
                    custom_times = _parse_custom_times(row[custom_col], period)

                note = row.get(note_col, "").strip() if note_col else ""

                row_count = 0
                for col_header, date_str in col_to_date.items():
                    value = row.get(col_header, "").strip()
                    if not value:
                        continue  # 未回答はスキップ

                    # 選択値 → pattern_id に変換
                    if value in ("休み（出勤不可）", "休み"):
                        pattern_id   = None
                        custom_start = None
                        custom_end   = None
                    elif value in ("カスタム（備考に時刻を記入）", "カスタム"):
                        pattern_id = "custom"
                        cs = custom_times.get(date_str)
                        if cs:
                            custom_start, custom_end = cs
                        else:
                            custom_start = custom_end = None
                            result.warnings.append(
                                f"「{name}」{date_str}: カスタム選択ですが時刻が未記入です"
                            )
                    elif value in _LABEL_TO_ID:
                        pattern_id   = _LABEL_TO_ID[value]
                        custom_start = None
                        custom_end   = None
                    else:
                        result.warnings.append(
                            f"「{name}」{date_str}: 選択値「{value}」が不明です（スキップ）"
                        )
                        continue

                    result.requests.append(ShiftRequest(
                        employee_id  = emp.id,
                        date         = date_str,
                        pattern_id   = pattern_id,
                        custom_start = custom_start,
                        custom_end   = custom_end,
                        note         = note,
                    ))
                    row_count += 1

                result.matched[name] = result.matched.get(name, 0) + row_count

    except UnicodeDecodeError:
        # UTF-8 でなければ cp932 で再試行
        try:
            with open(csv_path, encoding="cp932", newline="") as f:
                return parse_forms_csv.__wrapped__(csv_path, period, employees, _enc="cp932")
        except Exception as e:
            result.warnings.append(f"文字コードエラー: {e}。CSVをUTF-8で保存し直してください。")
    except Exception as e:
        result.warnings.append(f"CSV 解析エラー: {e}")

    return result


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
