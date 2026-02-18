import logging

from django.conf import settings

logger = logging.getLogger(__name__)


def _get_line_api():
    """LineBotApi インスタンスを返す。設定がない場合は None。"""
    token = getattr(settings, 'LINE_CHANNEL_ACCESS_TOKEN', '')
    if not token:
        return None
    try:
        from linebot import LineBotApi
        return LineBotApi(token)
    except ImportError:
        logger.warning('line-bot-sdk がインストールされていません。')
        return None


def send_line_message(line_user_id: str, message: str) -> bool:
    """指定した LINE ユーザーにプッシュメッセージを送信する。成功時は True を返す。"""
    if not line_user_id:
        return False
    api = _get_line_api()
    if api is None:
        return False
    try:
        from linebot.models import TextSendMessage
        api.push_message(line_user_id, TextSendMessage(text=message))
        return True
    except Exception as exc:
        logger.error('LINE 送信エラー (user=%s): %s', line_user_id, exc)
        return False


def get_webhook_handler():
    """WebhookHandler を返す。設定がない場合は None。"""
    secret = getattr(settings, 'LINE_CHANNEL_SECRET', '')
    if not secret:
        return None
    try:
        from linebot import WebhookHandler
        return WebhookHandler(secret)
    except ImportError:
        return None
