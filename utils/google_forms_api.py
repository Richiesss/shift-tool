# -*- coding: utf-8 -*-
"""Google Forms / Drive API Integration Helper"""
import os
import json
from datetime import date
from typing import Optional
from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request
from google_auth_oauthlib.flow import Flow
from googleapiclient.discovery import build

from models.schedule import SchedulePeriod
from models.employee import Employee
from utils.shift_patterns import ALL_PATTERNS


def get_oauth_credentials(client_id: str, client_secret: str, redirect_uri: str) -> Flow:
    """OAuth2 フローオブジェクトを構築する"""
    client_config = {
        "web": {
            "client_id": client_id,
            "client_secret": client_secret,
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": "https://oauth2.googleapis.com/token",
        }
    }
    return Flow.from_client_config(
        client_config,
        scopes=[
            "https://www.googleapis.com/auth/forms.body",
            "https://www.googleapis.com/auth/forms.responses.readonly",
            "https://www.googleapis.com/auth/drive.file"
        ],
        redirect_uri=redirect_uri
    )


def get_credentials_from_json(credentials_json_str: str) -> Credentials:
    """保存されたJSON文字列から Credentials オブジェクトを復元し、Expired なら Refresh する"""
    info = json.loads(credentials_json_str)
    creds = Credentials.from_authorized_user_info(info)
    if creds and creds.expired and creds.refresh_token:
        # トークンをリフレッシュ
        creds.refresh(Request())
        # 更新されたトークンをDBに再保存
        from db import repositories as repo
        repo.save_google_token(creds.to_json())
    return creds


DOW_LABELS = ["月", "火", "水", "木", "金", "土", "日"]

# 各日設問の共通ヒント（設問下に表示される短い説明）
DATE_QUESTION_HINT = (
    "出勤できる時間帯にチェック（朝食・ディナー両方可なら両方チェック）。"
    "休み・有給の場合はその項目のみ選択してください。"
)


def _date_question_title(d: date, holidays: set[str]) -> str:
    """各日設問のタイトルを返す。土曜=🔵 / 日曜・祝日=🔴 のマーカーで視認性を上げる。
    末尾の「の出勤希望時間」と「M月D日」表記は回答パーサが依存しているため変更しないこと。"""
    ds = d.isoformat()
    is_h = ds in holidays
    if is_h or d.weekday() == 6:
        mark = "🔴"
    elif d.weekday() == 5:
        mark = "🔵"
    else:
        mark = ""
    label = f"{d.month}月{d.day}日（{DOW_LABELS[d.weekday()]}）"
    if is_h:
        label += "【祝】"
    return f"{mark}{label} の出勤希望時間"


def _week_chunks(dates: list[date]) -> list[list[date]]:
    """日付リストを週（月曜始まり）ごとのまとまりに分割する"""
    chunks: list[list[date]] = []
    cur: list[date] = []
    for d in dates:
        if cur and d.weekday() == 0:
            chunks.append(cur)
            cur = []
        cur.append(d)
    if cur:
        chunks.append(cur)
    return chunks


def _week_label(idx: int, chunk: list[date]) -> tuple[str, str]:
    """週セクションの見出しと説明を返す"""
    s, e = chunk[0], chunk[-1]
    title = f"第{idx + 1}週（{s.month}/{s.day}〜{e.month}/{e.day}）"
    desc = "この週の出勤希望を入力してください。"
    return title, desc


def _email_settings_request() -> dict:
    """メール収集なし（ログイン不要で回答できる状態）を明示する設定リクエスト"""
    return {
        "updateSettings": {
            "settings": {"emailCollectionType": "DO_NOT_COLLECT"},
            "updateMask": "emailCollectionType",
        }
    }


def create_google_form(
    period: SchedulePeriod,
    employees: list[Employee],
    credentials_json_str: str,
) -> tuple[str, str]:
    """
    指定期間用の Google フォームを自動作成し、(form_id, form_url) を返す。

    UX設計:
    - 週ごとにページを分割（pageBreakItem）して進捗バーを表示し、スクロール量を抑える
    - 各日の出勤希望時間はチェックボックスで複数選択（土曜=🔵 / 日曜・祝日=🔴 マーカー付き）
    - メール収集なし（emailCollectionType=DO_NOT_COLLECT）でログインなしでも回答できる
    """
    creds = get_credentials_from_json(credentials_json_str)
    forms_service = build("forms", "v1", credentials=creds)

    title = f"{period.start_date} 〜 {period.end_date} 希望シフト提出"
    description = (
        "【希望シフト提出時の注意事項】\n"
        "1. お名前をリストから正確に選択してください。\n"
        "2. 日付ごとに希望する時間帯を選択してください（複数選択可）。\n"
        "   - 「朝食」と「ディナー」は両方チェックを入れて構いません（店舗の状況や労働時間に合わせて割り当てられます）。\n"
        "   - 朝食の中、またはディナーの中で入れる時間が複数ある場合は、一番希望する時間帯を1つだけ選んでください。\n"
        "3. 出勤できない日、または有給を希望する日はそれぞれ「休み（出勤不可）」または「有給」を選択してください。\n"
        "4. 選択肢にない時間帯を希望する場合は「その他」を選択し、最後の備考欄に具体的な希望時間を記入してください。\n"
        "   （記入例: 4/1 10:00〜16:00）\n"
        "※ 週ごとにページが分かれています。「次へ」で進んでください。"
    )

    # 1. フォームを作成
    form_body = {
        "info": {
            "title": title,
            "documentTitle": title
        }
    }
    form = forms_service.forms().create(body=form_body).execute()
    form_id = form["formId"]
    responder_url = form["responderUri"]

    # 2. 設問バッチ作成リクエストを構築
    requests = []

    # 説明文の設定
    requests.append({
        "updateFormInfo": {
            "info": {
                "description": description
            },
            "updateMask": "description"
        }
    })

    # メール収集なし（ログイン不要で回答できる状態を保証）
    requests.append(_email_settings_request())

    # Q1: お名前 (必須プルダウン)
    emp_names = [e.name for e in employees]
    requests.append({
        "createItem": {
            "item": {
                "title": "お名前",
                "description": "ご自身の名前を選択してください。",
                "questionItem": {
                    "question": {
                        "required": True,
                        "choiceQuestion": {
                            "type": "DROP_DOWN",
                            "options": [{"value": name} for name in emp_names]
                        }
                    }
                }
            },
            "location": {"index": 0}
        }
    })

    # 各日の出勤可否と時間帯の質問（週ごとにページ分割）
    dates = list(period.date_range())
    from utils.holidays import holiday_set
    holidays = holiday_set(dates)

    # チェックボックス用の選択肢リストを作成
    checkbox_options = [{"value": "休み（出勤不可）"}, {"value": "有給"}]
    for p in ALL_PATTERNS:
        if p.id not in ("custom", "double") and p.start and p.end:
            label = p.label
            if p.covers_breakfast():
                checkbox_options.append({"value": f"朝食: {label}"})
            if p.covers_dinner():
                checkbox_options.append({"value": f"ディナー: {label}"})

    checkbox_options.append({"value": "その他（備考欄に時刻を記入）"})

    idx = 1
    for w_i, chunk in enumerate(_week_chunks(dates)):
        w_title, w_desc = _week_label(w_i, chunk)
        if w_i == 0:
            # 1ページ目はテキスト見出しで週を示す（ページ区切りは2週目から）
            requests.append({
                "createItem": {
                    "item": {"title": w_title, "description": w_desc, "textItem": {}},
                    "location": {"index": idx}
                }
            })
        else:
            # 2週目以降は改ページ（進捗バーが表示され、スクロール量が減る）
            requests.append({
                "createItem": {
                    "item": {"title": w_title, "description": w_desc, "pageBreakItem": {}},
                    "location": {"index": idx}
                }
            })
        idx += 1

        for d in chunk:
            # 出勤希望時間 (チェックボックス、土曜=🔵/日曜・祝=🔴)
            requests.append({
                "createItem": {
                    "item": {
                        "title": _date_question_title(d, holidays),
                        "description": DATE_QUESTION_HINT,
                        "questionItem": {
                            "question": {
                                "required": True,
                                "choiceQuestion": {
                                    "type": "CHECKBOX",
                                    "options": checkbox_options
                                }
                            }
                        }
                    },
                    "location": {"index": idx}
                }
            })
            idx += 1

    # QLast: 備考質問 (長文記述式)
    requests.append({
        "createItem": {
            "item": {
                "title": "その他、シフトに関する連絡事項があれば記入してください",
                "description": "「その他」を選んだ日の時刻、遅刻・早退の予定などを記入してください。\n例: 4/1 10:00〜16:00",
                "questionItem": {
                    "question": {
                        "required": False,
                        "textQuestion": {
                            "paragraph": True
                        }
                    }
                }
            },
            "location": {"index": idx}
        }
    })

    # batchUpdate を実行して設問を追加
    forms_service.forms().batchUpdate(
        formId=form_id,
        body={"requests": requests}
    ).execute()

    return form_id, responder_url


def fetch_google_form_responses(
    form_id: str,
    credentials_json_str: str,
) -> tuple[dict, list[dict]]:
    """
    Google フォームの回答データとフォーム構造を取得する。
    戻り値: (form_schema, responses_list)
    """
    creds = get_credentials_from_json(credentials_json_str)
    forms_service = build("forms", "v1", credentials=creds)

    # フォーム構造を取得して設問ID -> タイトルのマッピングを作る
    form_schema = forms_service.forms().get(formId=form_id).execute()
    
    # 回答一覧を取得
    resp_data = forms_service.forms().responses().list(formId=form_id).execute()
    responses = resp_data.get("responses", [])

    return form_schema, responses


def update_google_form(
    form_id: str,
    period: SchedulePeriod,
    employees: list[Employee],
    credentials_json_str: str,
    custom_title: Optional[str] = None,
    custom_description: Optional[str] = None,
) -> None:
    """
    既存の Google フォームの内容を更新する。
    - タイトルや説明文（注意事項）の更新
    - お名前リストの最新化
    - 各日のチェックボックス選択肢の最新化
    """
    creds = get_credentials_from_json(credentials_json_str)
    forms_service = build("forms", "v1", credentials=creds)

    # 現在のフォームの構造を取得
    form_info = forms_service.forms().get(formId=form_id).execute()
    items = form_info.get("items", [])

    requests = []

    # 1. フォームの基本情報の更新
    info_update = {}
    update_masks = []
    if custom_title:
        info_update["title"] = custom_title
        update_masks.append("title")
    if custom_description:
        info_update["description"] = custom_description
        update_masks.append("description")

    if info_update:
        requests.append({
            "updateFormInfo": {
                "info": info_update,
                "updateMask": ",".join(update_masks)
            }
        })

    # お名前リストの準備
    emp_names = [e.name for e in employees]

    # チェックボックス用の選択肢リストを作成
    checkbox_options = [{"value": "休み（出勤不可）"}, {"value": "有給"}]
    for p in ALL_PATTERNS:
        if p.id not in ("custom", "double") and p.start and p.end:
            label = p.label
            if p.covers_breakfast():
                checkbox_options.append({"value": f"朝食: {label}"})
            if p.covers_dinner():
                checkbox_options.append({"value": f"ディナー: {label}"})
    checkbox_options.append({"value": "その他（備考欄に時刻を記入）"})

    # 日付ごとのタイトル判定用（(month, day) → 新形式タイトル）
    dates = list(period.date_range())
    from utils.holidays import holiday_set
    holidays = holiday_set(dates)
    title_by_md = {(d.month, d.day): _date_question_title(d, holidays) for d in dates}

    # メール収集なし設定を再適用（旧フォームにも反映される）
    requests.append(_email_settings_request())

    # 各アイテムの更新リクエストを構築
    for idx, item in enumerate(items):
        title = item.get("title", "")
        item_id = item.get("itemId")
        q_item = item.get("questionItem")
        if not q_item or "question" not in q_item:
            continue
        q_id = q_item["question"]["questionId"]

        # A. お名前の更新
        if title == "お名前":
            requests.append({
                "updateItem": {
                    "item": {
                        "itemId": item_id,
                        "questionItem": {
                            "question": {
                                "questionId": q_id,
                                "required": True,
                                "choiceQuestion": {
                                    "type": "DROP_DOWN",
                                    "options": [{"value": name} for name in emp_names]
                                }
                            }
                        }
                    },
                    "location": {
                        "index": idx
                    },
                    "updateMask": "questionItem.question.choiceQuestion.options"
                }
            })
            continue

        # B. 日付ごとのチェックボックスの更新
        # 旧形式（マーカーなし）のタイトルにも一致するよう「M月D日」を抽出して照合し、
        # タイトル・説明も最新形式（🔵/🔴マーカー+ヒント文）に更新する
        import re as _re
        m = _re.search(r'(\d{1,2})月(\d{1,2})日', title)
        if m and "出勤希望時間" in title:
            new_title = title_by_md.get((int(m.group(1)), int(m.group(2))))
            if new_title:
                requests.append({
                    "updateItem": {
                        "item": {
                            "itemId": item_id,
                            "title": new_title,
                            "description": DATE_QUESTION_HINT,
                            "questionItem": {
                                "question": {
                                    "questionId": q_id,
                                    "required": True,
                                    "choiceQuestion": {
                                        "type": "CHECKBOX",
                                        "options": checkbox_options
                                    }
                                }
                            }
                        },
                        "location": {
                            "index": idx
                        },
                        "updateMask": "title,description,questionItem.question.choiceQuestion.options"
                    }
                })

    if requests:
        forms_service.forms().batchUpdate(
            formId=form_id,
            body={"requests": requests}
        ).execute()


def get_form_accepting_responses(
    form_id: str,
    credentials_json_str: str,
) -> Optional[bool]:
    """フォームが回答を受け付けているかを返す。
    公開設定に対応していない（レガシー）フォームの場合は None を返す。"""
    creds = get_credentials_from_json(credentials_json_str)
    forms_service = build("forms", "v1", credentials=creds)
    form = forms_service.forms().get(formId=form_id).execute()
    publish = form.get("publishSettings", {}).get("publishState")
    if publish is None:
        return None
    return bool(publish.get("isAcceptingResponses"))


def set_form_accepting_responses(
    form_id: str,
    credentials_json_str: str,
    accepting: bool,
) -> None:
    """フォームの回答受付を開始/停止する（締切管理）。
    停止すると回答者には「回答の受付は終了しました」と表示される。"""
    creds = get_credentials_from_json(credentials_json_str)
    forms_service = build("forms", "v1", credentials=creds)
    forms_service.forms().setPublishSettings(
        formId=form_id,
        body={
            "publishSettings": {
                "publishState": {
                    "isPublished": True,
                    "isAcceptingResponses": accepting,
                }
            },
            "updateMask": "publishState.isAcceptingResponses",
        },
    ).execute()
