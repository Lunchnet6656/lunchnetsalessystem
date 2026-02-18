"""
シフト通知送信ユーティリティ。
LINE / メールで未提出スタッフ or 全スタッフに通知する。
"""
import logging

from django.conf import settings
from django.core.mail import send_mail

from .line_bot import send_line_message
from .models import ShiftNotification, UserProfile

logger = logging.getLogger(__name__)


def notify_users(period, notification_type: str, title: str, body: str, profiles=None) -> ShiftNotification:
    """
    指定プロフィール一覧（デフォルト: 全アクティブスタッフ）に LINE / メールで通知し、
    ShiftNotification レコードを作成して返す。
    """
    if profiles is None:
        profiles = UserProfile.objects.filter(user__is_active=True).select_related('user')

    line_count = 0
    email_count = 0

    for profile in profiles:
        if profile.notify_via_line and profile.line_user_id:
            ok = send_line_message(profile.line_user_id, f'{title}\n\n{body}')
            if ok:
                line_count += 1
        elif profile.notify_via_email and profile.notification_email:
            try:
                send_mail(
                    subject=title,
                    message=body,
                    from_email=settings.DEFAULT_FROM_EMAIL,
                    recipient_list=[profile.notification_email],
                    fail_silently=False,
                )
                email_count += 1
            except Exception as exc:
                logger.error('メール送信エラー (%s): %s', profile.notification_email, exc)

    notification = ShiftNotification.objects.create(
        period=period,
        notification_type=notification_type,
        title=title,
        body=body,
        sent_line_count=line_count,
        sent_email_count=email_count,
    )
    return notification


def notify_period_open(period) -> ShiftNotification:
    """募集開始通知を全スタッフに送信する。"""
    title = '【シフト募集開始】'
    body = (
        f'新しいシフト希望募集が始まりました。\n'
        f'対象期間: {period.start_date.strftime("%-m/%-d")} 〜 {period.end_date.strftime("%-m/%-d")}\n'
        f'提出締切: {period.submission_close_at.strftime("%-m/%-d %H:%M")}\n\n'
        'アプリからシフト希望を提出してください。'
    )
    return notify_users(period, 'OPEN', title, body)


def notify_reminder(period, unsubmitted_profiles) -> ShiftNotification:
    """締切リマインドを未提出スタッフに送信する。"""
    title = '【シフト提出リマインド】締切が迫っています'
    body = (
        f'シフト希望の締切が近づいています。\n'
        f'対象期間: {period.start_date.strftime("%-m/%-d")} 〜 {period.end_date.strftime("%-m/%-d")}\n'
        f'提出締切: {period.submission_close_at.strftime("%-m/%-d %H:%M")}\n\n'
        'まだ未提出の方はお早めに提出してください。'
    )
    return notify_users(period, 'REMINDER', title, body, profiles=unsubmitted_profiles)


def notify_manual_reminder(period, unsubmitted_profiles) -> ShiftNotification:
    """管理者による手動リマインドを未提出スタッフに送信する。"""
    title = '【シフト提出のお願い】'
    body = (
        f'シフト希望をまだご提出いただいていません。\n'
        f'対象期間: {period.start_date.strftime("%-m/%-d")} 〜 {period.end_date.strftime("%-m/%-d")}\n'
        f'提出締切: {period.submission_close_at.strftime("%-m/%-d %H:%M")}\n\n'
        '早急にアプリからご提出ください。'
    )
    return notify_users(period, 'MANUAL', title, body, profiles=unsubmitted_profiles)
