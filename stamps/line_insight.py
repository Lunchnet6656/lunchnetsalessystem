"""LINE公式アカウントの友だち統計（Insight: followers）取得。

仕様: .company/engineering/harness/specs/lunchnetスタンプ友だち分析-Insight連携.md

Messaging API の insight/followers を使い、公式アカウント全体の
「累計友だち追加数・累計ブロック数・実質届く友だち数」を日次で取得する。
チャネルアクセストークン（settings.LINE_CHANNEL_ACCESS_TOKEN）を使う＝配信側の集計。
per-userのリッチメニュー配信と同じトークンを reservations.line_richmenu 経由で共用する。

数字の性質（読み方の注意）：
- 前日ぶんまでしか取れない（当日は未確定）。
- followers/blocks は「累計・延べ」（ブロック/退会/アンブロックでも減らない）。
- targetedReaches が実質届く友だち（ブロック除外）。少人数だと0/欠落することがある。
- status: ready=確定 / unready=集計未完（後で再取得）/ out_of_service=対象外日。
"""
from __future__ import annotations

import logging

import requests
from django.utils import timezone

from reservations import line_richmenu

logger = logging.getLogger(__name__)

_API = "https://api.line.me"
_TIMEOUT = 10


class InsightError(Exception):
    """Insight取得に失敗（トークン未設定・通信/LINE側エラー）。"""


def fetch_friend_insight(date) -> dict:
    """指定日(date: date)の友だち統計をLINEから取得し dict を返す（保存はしない）。

    返り値: {status, followers, targeted_reaches, blocks, raw}
    フィールド欠落に強いよう .get() で読む（少人数だと targetedReaches 等が無い）。
    """
    token = line_richmenu._token()
    if not token:
        raise InsightError("LINE_CHANNEL_ACCESS_TOKEN が未設定です。")
    try:
        r = requests.get(
            f"{_API}/v2/bot/insight/followers",
            headers={"Authorization": f"Bearer {token}"},
            params={"date": date.strftime("%Y%m%d")},
            timeout=_TIMEOUT,
        )
    except requests.RequestException as e:
        raise InsightError(f"LINEとの通信に失敗しました：{e}")
    if r.status_code != 200:
        raise InsightError(f"LINE Insight取得に失敗（HTTP {r.status_code}）：{r.text[:200]}")
    data = r.json() or {}
    return {
        "status": data.get("status", "unready"),
        "followers": int(data.get("followers") or 0),
        "targeted_reaches": int(data.get("targetedReaches") or 0),
        "blocks": int(data.get("blocks") or 0),
        "raw": data,
    }


def save_friend_insight(date):
    """指定日の統計を取得して FriendInsightSnapshot に upsert する。保存した obj を返す。"""
    from stamps.models import FriendInsightSnapshot

    d = fetch_friend_insight(date)
    obj, _ = FriendInsightSnapshot.objects.update_or_create(
        date=date,
        defaults={
            "status": d["status"],
            "followers": d["followers"],
            "targeted_reaches": d["targeted_reaches"],
            "blocks": d["blocks"],
            "fetched_at": timezone.now(),
        },
    )
    return obj
