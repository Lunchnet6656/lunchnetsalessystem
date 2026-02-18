"""
シフト締切リマインダーコマンド。

使用例:
    python manage.py send_shift_reminders           # 3日前にリマインド（デフォルト）
    python manage.py send_shift_reminders --days 1  # 1日前にリマインド

Heroku Scheduler で毎朝9時に実行:
    python manage.py send_shift_reminders --days 3
"""
from datetime import timedelta

from django.core.management.base import BaseCommand
from django.utils import timezone

from shifts.models import AvailabilitySubmission, SchedulePeriod, UserProfile
from shifts.notifications import notify_reminder


class Command(BaseCommand):
    help = '締切 N 日前の OPEN 期間を対象に未提出スタッフへリマインドを送信する'

    def add_arguments(self, parser):
        parser.add_argument(
            '--days',
            type=int,
            default=3,
            help='締切まで何日前の期間を対象にするか（デフォルト: 3）',
        )

    def handle(self, *args, **options):
        days = options['days']
        now = timezone.now()
        target_dt = now + timedelta(days=days)

        # 締切が target_dt 前後 1 時間の OPEN 期間を取得
        periods = SchedulePeriod.objects.filter(
            status='OPEN',
            submission_close_at__range=(
                target_dt - timedelta(hours=1),
                target_dt + timedelta(hours=1),
            ),
        )

        if not periods.exists():
            self.stdout.write(f'対象の期間が見つかりませんでした (target: {target_dt:%Y-%m-%d %H:%M})')
            return

        for period in periods:
            submitted_ids = AvailabilitySubmission.objects.filter(
                period=period,
                submitted_at__isnull=False,
            ).values_list('user_id', flat=True)

            unsubmitted_profiles = UserProfile.objects.filter(
                user__is_active=True,
            ).exclude(user_id__in=submitted_ids).select_related('user')

            count = unsubmitted_profiles.count()
            if count == 0:
                self.stdout.write(f'[{period}] 全員提出済み。スキップ。')
                continue

            notification = notify_reminder(period, unsubmitted_profiles)
            self.stdout.write(
                f'[{period}] リマインド送信: LINE={notification.sent_line_count}, '
                f'メール={notification.sent_email_count}, 対象={count}名'
            )
