"""LINE リッチメニュー API（per-user 配信）。

狙い：テスト店舗で登録（onboarding）したお客さんの userId にだけ「予約メニュー」を出す。
公式アカウントのチャネルアクセストークン（settings.LINE_CHANNEL_ACCESS_TOKEN）を使う。
**デフォルトメニューには設定しない**（= 全友だちには出さない）。per-user リンクのみ＝テスト店舗
限定の規律を守ったまま、登録客だけが常駐メニューで1タップ再入場できる。

セットアップ（メニュー作成・画像アップ）は管理コマンド setup_reservation_richmenu で行う。
"""
import logging

import requests
from django.conf import settings

logger = logging.getLogger(__name__)

_API = "https://api.line.me"
_DATA_API = "https://api-data.line.me"


def _token() -> str:
    return getattr(settings, "LINE_CHANNEL_ACCESS_TOKEN", "") or ""


def _auth(json=True):
    h = {"Authorization": f"Bearer {_token()}"}
    if json:
        h["Content-Type"] = "application/json"
    return h


def create_rich_menu(menu: dict) -> str:
    """リッチメニューを作成し richMenuId を返す（画像は別途 upload_image）。"""
    r = requests.post(f"{_API}/v2/bot/richmenu", headers=_auth(), json=menu, timeout=10)
    r.raise_for_status()
    return r.json()["richMenuId"]


def upload_image(rich_menu_id: str, png_bytes: bytes) -> None:
    """リッチメニュー画像（PNG）をアップロード。"""
    r = requests.post(
        f"{_DATA_API}/v2/bot/richmenu/{rich_menu_id}/content",
        headers={"Authorization": f"Bearer {_token()}", "Content-Type": "image/png"},
        data=png_bytes, timeout=30,
    )
    r.raise_for_status()


def link_to_user(user_id: str, rich_menu_id: str = "") -> bool:
    """指定 userId にリッチメニューをリンク（その人にだけ表示）。best-effort（失敗で例外を投げない）。"""
    rich_menu_id = rich_menu_id or getattr(settings, "LINE_RESERVE_RICHMENU_ID", "")
    if not (_token() and user_id and rich_menu_id):
        return False
    try:
        r = requests.post(
            f"{_API}/v2/bot/user/{user_id}/richmenu/{rich_menu_id}",
            headers=_auth(json=False), timeout=5,
        )
    except requests.RequestException as e:
        logger.warning("richmenu link error: %s", e)
        return False
    if r.status_code == 200:
        return True
    logger.warning("richmenu link failed: %s %s", r.status_code, r.text[:200])
    return False


def unlink_from_user(user_id: str) -> bool:
    if not (_token() and user_id):
        return False
    try:
        r = requests.delete(f"{_API}/v2/bot/user/{user_id}/richmenu",
                            headers=_auth(json=False), timeout=5)
        return r.status_code == 200
    except requests.RequestException:
        return False


def list_rich_menus() -> list:
    r = requests.get(f"{_API}/v2/bot/richmenu/list", headers=_auth(json=False), timeout=10)
    r.raise_for_status()
    return r.json().get("richmenus", [])


def delete_rich_menu(rich_menu_id: str) -> None:
    r = requests.delete(f"{_API}/v2/bot/richmenu/{rich_menu_id}", headers=_auth(json=False), timeout=10)
    r.raise_for_status()
