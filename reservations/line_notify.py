"""LINE公式アカウントからの push 通知（案L S3-B）。

Messaging API の push を使う。送信元認証＝公式アカウントの「チャネルアクセストークン（長期）」＝機密。
settings.LINE_CHANNEL_ACCESS_TOKEN（env）から読む。未設定なら送信しない（黙ってスキップ）。

方針：**予約処理は通知の成否に依存させない**。push の失敗・未設定・例外はすべて False を返して飲み込む
（予約確定は通っているのに通知失敗で500、を避ける）。仮会員（devtmp-）の userId は実在しないので
push は自然に失敗＝False になる。
"""
import logging

import requests
from django.conf import settings

from reservations.templatetags.reserve_extras import jp_date

logger = logging.getLogger(__name__)

_PUSH_URL = "https://api.line.me/v2/bot/message/push"
_TIMEOUT = 5


def _token() -> str:
    return getattr(settings, "LINE_CHANNEL_ACCESS_TOKEN", "") or ""


def is_configured() -> bool:
    return bool(_token())


def push_text(user_id: str, text: str) -> bool:
    """指定 userId へテキストを push。成功で True。未設定/失敗/例外は False（送出しない）。"""
    token = _token()
    if not token or not user_id:
        return False
    try:
        r = requests.post(
            _PUSH_URL,
            headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
            json={"to": user_id, "messages": [{"type": "text", "text": text[:4900]}]},
            timeout=_TIMEOUT,
        )
    except requests.RequestException as e:
        logger.warning("LINE push error: %s", e)
        return False
    if r.status_code == 200:
        return True
    logger.warning("LINE push failed: %s %s", r.status_code, r.text[:300])
    return False


def _item_lines(reservation):
    lines = []
    for it in reservation.items.all():
        sizes = []
        if it.quantity_large:
            sizes.append(f"大{it.quantity_large}")
        if it.quantity_regular:
            sizes.append(f"並{it.quantity_regular}")
        if it.quantity_small:
            sizes.append(f"小{it.quantity_small}")
        lines.append(f"・{it.product_name}　{' '.join(sizes)}")
    return lines


def build_confirm_message(reservation, detail_url=None) -> str:
    """予約確定の控えメッセージ（FR7）。detail_url があれば控え（受け取り完了/取消）ページへのリンクを付ける。"""
    parts = [
        "ご予約ありがとうございます！",
        f"{reservation.member.name} 様",
        "",
        f"受取店舗：{reservation.sales_location.name}",
        f"受取日：{jp_date(reservation.pickup_date)}",
        "ご注文：",
        *_item_lines(reservation),
        f"合計 {reservation.total_quantity}個 / {reservation.total_amount:.0f}円（受け取り時に現金）",
        "",
        f"お問い合わせ番号：{reservation.reservation_number}",
        "受け取り時に、お名前をお伝えください。",
    ]
    if detail_url:
        parts += ["", "▼ ご予約内容・受け取り完了・取消はこちら", detail_url]
    reserve_url = getattr(settings, "LIFF_RESERVE_URL", "")
    if reserve_url:
        parts += ["", "▼ 次回のご予約（このリンクからQRなしでご予約できます）", reserve_url]
    return "\n".join(parts)


def build_reminder_message(reservation) -> str:
    """前日リマインドのメッセージ（FR10・ノーショー対策）。"""
    parts = [
        "【明日の受け取りのご案内】",
        f"{reservation.member.name} 様",
        "",
        f"{reservation.sales_location.name}　{jp_date(reservation.pickup_date)} のご予約です。",
        *_item_lines(reservation),
        f"合計 {reservation.total_quantity}個（受け取り時に現金）",
        "",
        "販売場所・販売時間内にお越しください。お待ちしています！",
    ]
    return "\n".join(parts)


def notify_reservation_confirmed(reservation, detail_url=None) -> bool:
    """確定控えを本人へ送る。予約処理から呼ぶ（失敗してもFalseを返すだけ）。"""
    try:
        return push_text(reservation.member.line_user_id,
                         build_confirm_message(reservation, detail_url=detail_url))
    except Exception as e:  # 通知で予約を壊さない最後の砦
        logger.warning("confirm notify error: %s", e)
        return False


def notify_reservation_reminder(reservation) -> bool:
    """前日リマインドを本人へ送る。"""
    try:
        return push_text(reservation.member.line_user_id, build_reminder_message(reservation))
    except Exception as e:
        logger.warning("reminder notify error: %s", e)
        return False
