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
      - 4/1 10:00-16:00
      - 4/1 10:00～16:00
    """
    result: dict[str, tuple[str, str]] = {}
    pattern = re.findall(
        r'(\d{1,2})[/月](\d{1,2})[日]?\s*[:\s]\s*(\d{1,2}:\d{2})\s*[〜~～\-]+\s*(\d{1,2}:\d{2})',
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

def build_form_guide(period: SchedulePeriod) -> str:
    """
    指定期間用の Google Forms 設問テンプレートを人間が読める文字列で返す。
    """
    from utils.constants import DAY_OF_WEEK_LABELS

    lines = [
        "═══════════════════════════════════════════════════════",
        f"  Google Forms テンプレート  （対象期間: {period.start_date} ～ {period.end_date}）",
        "═══════════════════════════════════════════════════════",
        "",
        "【Q1】お名前  ← 記述式（短文）・必須",
        "  ヒント: 「スタッフ登録名と完全に一致させてください」",
        "",
        "【Q2〜】各日付の出勤希望  ← プルダウン・必須",
    ]

    for d in period.date_range():
        dow = DAY_OF_WEEK_LABELS[d.weekday()]
        lines.append(f"  質問文: 「{d.month}月{d.day}日({dow})の出勤希望」")

    lines += [
        "",
        "  ↑ 全日付の選択肢（この文字列をそのままコピー）:",
        "    休み（出勤不可）",
    ]
    for p in ALL_PATTERNS:
        if p.id != "custom":
            lines.append(f"    {p.label}")
    lines += [
        "    カスタム（備考に時刻を記入）",
        "",
        "【最終Q】カスタム入力の時刻  ← 記述式（長文）・任意",
        "  ヒント: 「カスタムを選んだ日の時刻を記入してください",
        "           例: 4/1: 10:00〜16:00, 4/5: 13:00〜22:00」",
        "",
        "【任意】その他備考  ← 記述式（長文）・任意",
        "",
        "═══════════════════════════════════════════════════════",
        "  回答収集後: スプレッドシート → ファイル → ダウンロード → CSV",
        "═══════════════════════════════════════════════════════",
    ]
    return "\n".join(lines)
