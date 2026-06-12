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


def create_google_form(
    period: SchedulePeriod,
    employees: list[Employee],
    credentials_json_str: str,
) -> tuple[str, str]:
    """
    指定期間用の Google フォームを自動作成し、(form_id, form_url) を返す。
    ユーザー体験(UX)向上のため、1ページで各日の出勤希望時間をチェックボックスで複数選択できる構成で生成します。
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
        "   （記入例: 4/1 10:00〜16:00）"
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

    # 各日の出勤可否と時間帯の質問
    dates = list(period.date_range())
    from utils.holidays import holiday_set
    holidays = holiday_set(dates)
    dow_labels = ["月", "火", "水", "木", "金", "土", "日"]

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
    for d in dates:
        ds = d.isoformat()
        dow = dow_labels[d.weekday()]
        is_h = ds in holidays
        date_label = f"{d.month}月{d.day}日（{dow}）"
        if is_h:
            date_label += "【祝】"

        # 出勤希望時間 (チェックボックス)
        requests.append({
            "createItem": {
                "item": {
                    "title": f"{date_label} の出勤希望時間",
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

    # 日付ごとのラベル判定用
    dates = list(period.date_range())
    from utils.holidays import holiday_set
    holidays = holiday_set(dates)
    dow_labels = ["月", "火", "水", "木", "金", "土", "日"]
    date_labels = []
    for d in dates:
        ds = d.isoformat()
        dow = dow_labels[d.weekday()]
        is_h = ds in holidays
        date_label = f"{d.month}月{d.day}日（{dow}）"
        if is_h:
            date_label += "【祝】"
        date_labels.append((ds, date_label))

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
        for ds, date_label in date_labels:
            if title == f"{date_label} の出勤希望時間":
                requests.append({
                    "updateItem": {
                        "item": {
                            "itemId": item_id,
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
                        "updateMask": "questionItem.question.choiceQuestion.options"
                    }
                })
                break

    if requests:
        forms_service.forms().batchUpdate(
            formId=form_id,
            body={"requests": requests}
        ).execute()
