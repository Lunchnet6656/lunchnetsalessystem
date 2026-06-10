"""LINE プラットフォーム連携（案L／LIFF・本人識別・友だち判定）。

仕様: .company/engineering/harness/specs/w001-予約システム-要件定義.md（§6 案L）

フロント（LIFF）が取得した「ユーザーアクセストークン」をサーバーに渡し、
ここで LINE のAPIに問い合わせて (1) 本人識別 (2) 友だち状態 を確かめる。
アクセストークンはチャネルにスコープされ短命なので、これ自体が「本物のLINEログインを
通った」証拠になる＝フロントの userId を鵜呑みにしない（なりすまし防止）。

※チャネルアクセストークン（公式アカウントから配信する側の機密）はここでは使わない。
  ここで使うのはユーザー側のアクセストークンのみ＝機密を持たずに本人確認できる。
"""
from __future__ import annotations

import requests

_API = "https://api.line.me"
_TIMEOUT = 5


class LineAuthError(Exception):
    """LINE 認証/連携の確認に失敗（トークン無効・LINE側エラー等）。利用者向け文言を持つ。"""


def get_profile(access_token: str) -> dict:
    """アクセストークンから本人のプロフィールを取得。{user_id, display_name} を返す。

    トークンが無効ならここで弾かれる＝本人識別の正当性検証を兼ねる。
    """
    try:
        r = requests.get(
            f"{_API}/v2/profile",
            headers={"Authorization": f"Bearer {access_token}"},
            timeout=_TIMEOUT,
        )
    except requests.RequestException:
        raise LineAuthError("LINEとの通信に失敗しました。電波の良い場所で開き直してください。")
    if r.status_code != 200:
        raise LineAuthError("LINEログインの有効期限が切れています。ページを開き直してください。")
    data = r.json()
    user_id = data.get("userId")
    if not user_id:
        raise LineAuthError("LINEユーザー情報を取得できませんでした。")
    return {"user_id": user_id, "display_name": data.get("displayName", "")}


def is_friend(access_token: str) -> bool:
    """このアクセストークンの本人が、リンク済み公式アカウントを友だち追加しているか。"""
    try:
        r = requests.get(
            f"{_API}/friendship/v1/status",
            headers={"Authorization": f"Bearer {access_token}"},
            timeout=_TIMEOUT,
        )
    except requests.RequestException:
        raise LineAuthError("友だち状態の確認に失敗しました。電波の良い場所で開き直してください。")
    if r.status_code != 200:
        raise LineAuthError("友だち状態の確認に失敗しました。ページを開き直してください。")
    return bool(r.json().get("friendFlag"))
