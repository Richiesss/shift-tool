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
    """
    creds = get_credentials_from_json(credentials_json_str)
    forms_service = build("forms", "v1", credentials=creds)

    title = f"{period.start_date} 〜 {period.end_date} 希望シフト提出"
    description = (
        "【注意事項】\n"
        "・お名前をリストから正確に選んでください。\n"
        "・日付ごとに「出勤可否」および「希望シフト時間」を入力してください。\n"
        "・ダブルは朝食・ディナー両方フル出勤（時間帯は固定）を表します。\n"
        "・リストにない時間帯は「その他」を選択して、最後の備考欄に記入してください。"
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

    # 朝食/ディナーのシフトパターン選択肢
    b_options = []
    d_options = []
    for p in ALL_PATTERNS:
        if p.id not in ("custom", "double") and p.start and p.end:
            label = p.label
            if p.covers_breakfast():
                b_options.append({"value": label})
            if p.covers_dinner():
                d_options.append({"value": label})
    
    b_options.append({"value": "その他（備考欄に時刻を記入）"})
    d_options.append({"value": "その他（備考欄に時刻を記入）"})

    idx = 1
    for d in dates:
        ds = d.isoformat()
        dow = dow_labels[d.weekday()]
        is_h = ds in holidays
        date_label = f"{d.month}月{d.day}日（{dow}）"
        if is_h:
            date_label += "【祝】"

        # A. 出勤可否 (ラジオボタン)
        requests.append({
            "createItem": {
                "item": {
                    "title": f"{date_label}は出勤できますか？",
                    "questionItem": {
                        "question": {
                            "required": False,
                            "choiceQuestion": {
                                "type": "RADIO",
                                "options": [
                                    {"value": "朝食のみ"},
                                    {"value": "ディナーのみ"},
                                    {"value": "ダブル（朝食＋ディナー両方）"},
                                    {"value": "休み"}
                                ]
                            }
                        }
                    }
                },
                "location": {"index": idx}
            }
        })
        idx += 1

        # B. 朝食希望 (プルダウン)
        requests.append({
            "createItem": {
                "item": {
                    "title": f"{date_label}朝食の希望シフト",
                    "questionItem": {
                        "question": {
                            "required": False,
                            "choiceQuestion": {
                                "type": "DROP_DOWN",
                                "options": b_options
                            }
                        }
                    }
                },
                "location": {"index": idx}
            }
        })
        idx += 1

        # C. ディナー希望 (プルダウン)
        requests.append({
            "createItem": {
                "item": {
                    "title": f"{date_label}ディナーの希望シフト",
                    "questionItem": {
                        "question": {
                            "required": False,
                            "choiceQuestion": {
                                "type": "DROP_DOWN",
                                "options": d_options
                            }
                        }
                    }
                },
                "location": {"index": idx}
            }
        })
        idx += 1

    # QLast: 備考 (長文記述式)
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

    # batchUpdate を実行してフォームに設問を追加
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
