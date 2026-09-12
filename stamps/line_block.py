"""LINE会員ごとのブロック状態判定。

LINEのMessaging APIには「ブロックした友だちの一覧」を返す仕組みが無い。個別会員が
公式アカウントをブロックしているかは、プロフィールAPIを叩いて判定する：

    GET /v2/bot/profile/{userId}
      200 → 友だち かつ ブロックしていない（＝アクティブ）
      404/403 → ブロック中 または 退会・未追加（＝実質届かない）
      その他/通信失敗 → 判定不能（unknown）

本アプリのLineMemberは全員LIFF経由で友だち追加済みなので、いま404/403が返る＝
ブロック（または退会）とみなす。都度叩くと重い＆レート制限があるため、友だち管理画面の
「ブロック状態を更新」から一括照会し、結果を LineMember.blocked / block_checked_at に焼く。
"""
from __future__ import annotations

import logging

import requests
from django.utils import timezone

from reservations import line_richmenu

logger = logging.getLogger(__name__)

_API = "https://api.line.me"
_TIMEOUT = 5

# 判定結果
ACTIVE = "active"      # 友だち・届く
BLOCKED = "blocked"    # ブロック中／退会
UNKNOWN = "unknown"    # 判定不能（未設定・通信失敗・想定外レスポンス）


def check_block_status(user_id: str) -> str:
    """1人のuserIdのブロック状態を返す（ACTIVE / BLOCKED / UNKNOWN）。

    トークン未設定・userId空・通信失敗・想定外HTTPは UNKNOWN（＝既存フラグを変えない）。
    仮会員（devtmp-）のuserIdは実在しないので404→BLOCKED になり得るが、
    呼び出し側で除外する想定。
    """
    token = line_richmenu._token()
    if not token or not user_id:
        return UNKNOWN
    try:
        r = requests.get(
            f"{_API}/v2/bot/profile/{user_id}",
            headers={"Authorization": f"Bearer {token}"},
            timeout=_TIMEOUT,
        )
    except requests.RequestException as e:
        logger.warning("LINE profile fetch error: %s", e)
        return UNKNOWN
    if r.status_code == 200:
        return ACTIVE
    if r.status_code in (403, 404):
        return BLOCKED
    logger.warning("LINE profile unexpected status: %s %s", r.status_code, r.text[:200])
    return UNKNOWN


def refresh_block_status(members) -> dict:
    """会員群のブロック状態を一括照会し、blocked / block_checked_at を更新する。

    判定できた会員だけ更新（UNKNOWNは既存値を保持）。返り値は集計 dict：
    {"checked": 照会数, "active": n, "blocked": n, "unknown": n, "updated": 保存数}。
    実在しない仮会員（devtmp-）はスキップする。
    """
    now = timezone.now()
    stats = {"checked": 0, "active": 0, "blocked": 0, "unknown": 0, "updated": 0}
    for m in members:
        if not m.line_user_id or m.line_user_id.startswith("devtmp-"):
            continue
        stats["checked"] += 1
        status = check_block_status(m.line_user_id)
        stats[status] += 1
        if status == UNKNOWN:
            continue
        new_blocked = (status == BLOCKED)
        m.blocked = new_blocked
        m.block_checked_at = now
        m.save(update_fields=["blocked", "block_checked_at"])
        stats["updated"] += 1
    return stats
