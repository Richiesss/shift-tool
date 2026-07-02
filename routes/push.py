"""管理者向け Web Push 通知ルート（β機能）"""
from __future__ import annotations

import json
import logging
import os

from flask import Blueprint, jsonify, request

from db import repositories as repo

bp = Blueprint("push", __name__, url_prefix="/push")
logger = logging.getLogger(__name__)


# ── VAPID 鍵管理 ──────────────────────────────────────────────────────────

def _get_or_create_vapid_keys() -> tuple[str, str]:
    """VAPID 公開鍵（base64url, ブラウザ用）と秘密鍵（PEM文字列, pywebpush用）を返す。
    未生成の場合は生成して app_settings に保存する。"""
    pub  = repo.get_app_setting("vapid_public_key",  "")
    priv = repo.get_app_setting("vapid_private_key", "")
    if pub and priv:
        return pub, priv
    try:
        import base64
        from py_vapid import Vapid
        from cryptography.hazmat.primitives.serialization import (
            Encoding, PublicFormat,
        )
        v = Vapid()
        v.generate_keys()
        # 公開鍵: DER → 末尾65バイト（非圧縮 EC 点）を base64url でブラウザに渡す
        pub_der  = v.public_key.public_bytes(Encoding.DER, PublicFormat.SubjectPublicKeyInfo)
        pub  = base64.urlsafe_b64encode(pub_der[-65:]).decode().rstrip("=")
        # 秘密鍵: PEM 文字列を pywebpush へ直接渡す
        priv = v.private_pem().decode()
        repo.save_all_app_settings({"vapid_public_key": pub, "vapid_private_key": priv})
        return pub, priv
    except Exception as e:
        logger.error(f"[push] VAPID 鍵生成失敗: {e}")
        return "", ""


# ── エンドポイント ────────────────────────────────────────────────────────

@bp.get("/vapid-public-key")
def vapid_public_key():
    """フロントエンドが Push 購読時に使う VAPID 公開鍵を返す"""
    pub, _ = _get_or_create_vapid_keys()
    if not pub:
        return jsonify({"error": "VAPID 鍵の生成に失敗しました"}), 500
    return jsonify({"publicKey": pub})


@bp.post("/subscribe")
def subscribe():
    """ブラウザから Push サブスクリプションを受け取り DB に保存する"""
    data = request.get_json(silent=True)
    if not data:
        return jsonify({"ok": False, "error": "Invalid JSON"}), 400
    endpoint = data.get("endpoint")
    keys     = data.get("keys", {})
    p256dh   = keys.get("p256dh")
    auth     = keys.get("auth")
    if not (endpoint and p256dh and auth):
        return jsonify({"ok": False, "error": "必須フィールドが不足しています"}), 400
    repo.save_push_subscription(endpoint, p256dh, auth)
    return jsonify({"ok": True})


@bp.post("/unsubscribe")
def unsubscribe():
    """Push サブスクリプションを削除する"""
    data = request.get_json(silent=True)
    if not data or not data.get("endpoint"):
        return jsonify({"ok": False, "error": "endpoint が不足しています"}), 400
    repo.delete_push_subscription(data["endpoint"])
    return jsonify({"ok": True})


@bp.post("/send-test")
def send_test():
    """テスト Push 通知を全購読者に送信する（設定画面から実行）"""
    _send_push_notification("SDU-Shift テスト通知", "プッシュ通知の設定が完了しました")
    return jsonify({"ok": True})


# ── 内部ユーティリティ ────────────────────────────────────────────────────

def _send_push_notification(title: str, body: str) -> None:
    """全購読者に Push 通知を送る。失敗しても例外を伝播させない。"""
    try:
        from py_vapid import Vapid
        from pywebpush import webpush, WebPushException
    except ImportError:
        logger.warning("[push] pywebpush がインストールされていません")
        return

    pub, priv = _get_or_create_vapid_keys()
    if not pub or not priv:
        return

    subscriptions = repo.get_push_subscriptions()
    if not subscriptions:
        return

    payload = json.dumps({"title": title, "body": body}, ensure_ascii=False)
    dead_endpoints: list[str] = []

    for sub in subscriptions:
        try:
            webpush(
                subscription_info={
                    "endpoint": sub["endpoint"],
                    "keys": {"p256dh": sub["p256dh"], "auth": sub["auth"]},
                },
                data=payload,
                vapid_private_key=priv,           # PEM 文字列
                vapid_claims={"sub": "mailto:admin@sdu-shift.local"},
                ttl=86400,
            )
        except WebPushException as e:
            # 410 Gone → 購読が失効（ブラウザ側で解除済み）
            if e.response and e.response.status_code in (404, 410):
                dead_endpoints.append(sub["endpoint"])
            else:
                logger.warning(f"[push] 送信失敗 {sub['endpoint'][:40]}…: {e}")
        except Exception as e:
            logger.warning(f"[push] 送信エラー: {e}")

    for ep in dead_endpoints:
        repo.delete_push_subscription(ep)
