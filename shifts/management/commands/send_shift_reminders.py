"""
シフト締切リマインダーコマンド。

使用例:
    python manage.py send_shift_reminders                 # 1日前（前日）にリマインド（デフォルト）
    python manage.py send_shift_reminders --days 3        # 3日前にリマインド
    python manage.py send_shift_reminders --dry-run       # 送信せず対象者だけ表示（テスト用）
    python manage.py send_shift_reminders --only shohei   # 指定ユーザー1人にだけ送信（テスト用）

締切「日」が N 日後にあたる OPEN 期間を対象にするため、締切の時刻（23:59 等）は問わない。

Heroku Scheduler で前日リマインドする場合（毎日実行）:
    python manage.py send_shift_reminders --days 1
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
            default=1,
            help='締切まで何日前の期間を対象にするか（デフォルト: 1＝前日）',
        )
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='実際には送信せず、対象期間と送信予定の対象者だけ表示する（テスト用）',
        )
        parser.add_argument(
            '--only',
            type=str,
            default='',
            help='指定した username のスタッフ1人にだけ送信する（テスト用）',
        )

    def handle(self, *args, **options):
        days = options['days']
        dry_run = options['dry_run']
        only_username = options['only']
        now = timezone.now()
        # 締切「日」が N 日後にあたる OPEN 期間を対象にする。
        # 旧実装は「実行時刻 + N日 の ±1時間」で絞っていたため、締切時刻（23:59 等）が
        # スケジューラ実行時刻と噛み合わないと永遠に拾えなかった。日付ベースに変更して解消。
        target_date = (timezone.localtime(now) + timedelta(days=days)).date()

        periods = SchedulePeriod.objects.filter(
            status='OPEN',
            submission_close_at__date=target_date,
            submission_close_at__gte=now,
        )

        if not periods.exists():
            self.stdout.write(f'対象の期間が見つかりませんでした (target date: {target_date:%Y-%m-%d})')
            return

        for period in periods:
            submitted_ids = AvailabilitySubmission.objects.filter(
                period=period,
                submitted_at__isnull=False,
            ).values_list('user_id', flat=True)

            unsubmitted_profiles = UserProfile.objects.filter(
                user__is_active=True, uses_app=True,
            ).exclude(user_id__in=submitted_ids).select_related('user')

            if only_username:
                unsubmitted_profiles = unsubmitted_profiles.filter(user__username=only_username)

            count = unsubmitted_profiles.count()
            if count == 0:
                if only_username:
                    self.stdout.write(
                        f'[{period}] 対象者なし（{only_username} は未提出スタッフに該当しません）。スキップ。'
                    )
                else:
                    self.stdout.write(f'[{period}] 全員提出済み。スキップ。')
                continue

            if dry_run:
                names = ', '.join(
                    p.user.get_full_name() or p.user.username for p in unsubmitted_profiles
                )
                self.stdout.write(f'[{period}] [DRY-RUN] 送信せず。対象={count}名: {names}')
                continue

            notification = notify_reminder(period, unsubmitted_profiles)
            self.stdout.write(
                f'[{period}] リマインド送信: LINE={notification.sent_line_count}, '
                f'メール={notification.sent_email_count}, 対象={count}名'
            )
