from datetime import timedelta
from io import StringIO

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.test import TestCase
from django.utils import timezone

from shifts.models import (
    AvailabilitySubmission,
    SchedulePeriod,
    ShiftNotification,
    UserProfile,
)

User = get_user_model()


class SendShiftRemindersCommandTest(TestCase):
    """send_shift_reminders コマンドの対象期間抽出（締切日ベース）を検証する。"""

    def setUp(self):
        # アプリ利用中・未提出のスタッフを1名用意（LINE/メール未設定なので実送信はされない）。
        # UserProfile は post_save シグナルで自動生成されるため取得して使う。
        self.user = User.objects.create_user(username='staff1', password='x')
        UserProfile.objects.filter(user=self.user).update(uses_app=True)

        now_local = timezone.localtime(timezone.now())
        # 締切は 23:59（時刻ウィンドウとは噛み合わない時刻）で作る。
        self._eod = now_local.replace(hour=23, minute=59, second=0, microsecond=0)

    def _make_period(self, close_days_from_now, status='OPEN'):
        close_at = self._eod + timedelta(days=close_days_from_now)
        return SchedulePeriod.objects.create(
            start_date=(close_at + timedelta(days=1)).date(),
            end_date=(close_at + timedelta(days=14)).date(),
            submission_open_at=timezone.now() - timedelta(days=1),
            submission_close_at=close_at,
            status=status,
        )

    def _run(self, days=1, *extra):
        out = StringIO()
        call_command('send_shift_reminders', '--days', str(days), *extra, stdout=out)
        return out.getvalue()

    def test_前日締切2359でもリマインドが送られる(self):
        """締切翌日（=実行の1日後）23:59 の OPEN 期間は --days 1 で拾われる。"""
        period = self._make_period(close_days_from_now=1)
        self._run(days=1)
        self.assertTrue(
            ShiftNotification.objects.filter(period=period, notification_type='REMINDER').exists()
        )

    def test_締切が対象日でない期間は対象外(self):
        """締切が2日後の期間は --days 1 では拾われない。"""
        period = self._make_period(close_days_from_now=2)
        self._run(days=1)
        self.assertFalse(
            ShiftNotification.objects.filter(period=period, notification_type='REMINDER').exists()
        )

    def test_OPEN以外は対象外(self):
        period = self._make_period(close_days_from_now=1, status='REVIEW')
        self._run(days=1)
        self.assertFalse(
            ShiftNotification.objects.filter(period=period, notification_type='REMINDER').exists()
        )

    def test_全員提出済みならスキップ(self):
        period = self._make_period(close_days_from_now=1)
        AvailabilitySubmission.objects.create(
            user=self.user, period=period, submitted_at=timezone.now(),
        )
        self._run(days=1)
        self.assertFalse(
            ShiftNotification.objects.filter(period=period, notification_type='REMINDER').exists()
        )

    def test_dry_runは送信もレコード作成もしない(self):
        period = self._make_period(close_days_from_now=1)
        out = self._run(1, '--dry-run')
        self.assertIn('DRY-RUN', out)
        self.assertFalse(
            ShiftNotification.objects.filter(period=period, notification_type='REMINDER').exists()
        )

    def test_only指定で該当者がいなければ送信しない(self):
        period = self._make_period(close_days_from_now=1)
        self._run(1, '--only', 'nobody')
        self.assertFalse(
            ShiftNotification.objects.filter(period=period, notification_type='REMINDER').exists()
        )

    def test_only指定で該当者がいれば送信する(self):
        period = self._make_period(close_days_from_now=1)
        self._run(1, '--only', self.user.username)
        self.assertTrue(
            ShiftNotification.objects.filter(period=period, notification_type='REMINDER').exists()
        )
