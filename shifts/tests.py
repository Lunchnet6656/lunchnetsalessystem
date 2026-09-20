from datetime import date, datetime, timedelta
from io import StringIO

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from sales.models import SalesLocation
from shifts.models import (
    AvailabilityDay,
    AvailabilitySubmission,
    SchedulePeriod,
    ShiftAssignment,
    ShiftNotification,
    UserProfile,
)
from shifts.notifications import _render

User = get_user_model()


class ReminderDeadlineTimezoneTest(TestCase):
    """リマインド文面の締切時刻がJST（ローカル時間）で表示されることを検証する。"""

    def test_締切がJSTで表示される(self):
        # 締切を 2026/10/15 23:59 JST で作成（DBにはUTC 14:59で保存される）
        close_jst = timezone.make_aware(datetime(2026, 10, 15, 23, 59))
        period = SchedulePeriod.objects.create(
            start_date=date(2026, 10, 1),
            end_date=date(2026, 10, 15),
            submission_open_at=timezone.now(),
            submission_close_at=close_jst,
            status="OPEN",
        )
        rendered = _render("{deadline}", period)
        # UTC直表示なら 14:59 になってしまう。JSTなら 23:59。
        self.assertEqual(rendered, "10/15 23:59")
        self.assertNotIn("14:59", rendered)


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


class AutofillPriorityTest(TestCase):
    """同じ売り場を複数人が希望したとき、割当優先度の小さい人が自動割当で勝つことを検証する。"""

    def setUp(self):
        self.date = date(2026, 10, 5)  # 月曜・非祝日

        self.location = SalesLocation.objects.create(
            no=1, name="テスト売り場", type="通常", price_type="通常",
            service_name="弁当", requires_drive=False, excluded_from_shift=False,
        )

        # 優先度の高い人（数字が小さい）／低い人（数字が大きい）
        self.high = User.objects.create_user(username="high", password="x")
        UserProfile.objects.filter(user=self.high).update(
            default_location=self.location, assignment_priority=10,
        )
        self.low = User.objects.create_user(username="low", password="x")
        UserProfile.objects.filter(user=self.low).update(
            default_location=self.location, assignment_priority=200,
        )

        self.period = SchedulePeriod.objects.create(
            start_date=self.date, end_date=self.date,
            submission_open_at=timezone.now(),
            submission_close_at=timezone.now(),
            status="REVIEW",
        )

        # 両者ともこの日「出勤」希望を提出
        for u in (self.high, self.low):
            sub = AvailabilitySubmission.objects.create(
                user=u, period=self.period, status="SUBMITTED",
            )
            AvailabilityDay.objects.create(
                submission=sub, date=self.date, availability="WORK",
            )

        self.staff = User.objects.create_user(
            username="staff", password="x", is_staff=True,
        )

    def _run_autofill(self):
        self.client.force_login(self.staff)
        resp = self.client.post(reverse("shifts:api_autofill", args=[self.period.id]))
        self.assertEqual(resp.status_code, 200)

    def test_優先度の小さい人が勝つ(self):
        self._run_autofill()
        assignments = ShiftAssignment.objects.filter(
            date=self.date, sales_location=self.location,
        )
        self.assertEqual(assignments.count(), 1)  # 1売り場1人
        self.assertEqual(assignments.first().user_id, self.high.id)

    def test_登録順に依存せず優先度で決まる(self):
        # low を優先度1にすると、登録順が後でも low が勝つ
        UserProfile.objects.filter(user=self.low).update(assignment_priority=1)
        UserProfile.objects.filter(user=self.high).update(assignment_priority=50)
        self._run_autofill()
        assignments = ShiftAssignment.objects.filter(
            date=self.date, sales_location=self.location,
        )
        self.assertEqual(assignments.count(), 1)
        self.assertEqual(assignments.first().user_id, self.low.id)

    def test_シフト期間の新規作成フォームは折りたたみで表示される(self):
        """スマホで一覧が埋もれないよう、新規作成フォームは details で折りたたむ。"""
        self.client.force_login(self.staff)
        resp = self.client.get(reverse("shifts:admin_periods"))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "period-create")  # 折りたたみコンテナ
        self.assertContains(resp, "＋ 新規期間を作成")

    def test_割当グリッドにスタッフのコメントが表示される(self):
        """提出の備考・日別コメントが割当グリッドのコメント欄に出る。"""
        sub = AvailabilitySubmission.objects.get(user=self.high, period=self.period)
        sub.remarks = "来週は早退希望"
        sub.save()
        day = AvailabilityDay.objects.get(submission=sub, date=self.date)
        day.comment = "この日は15時まで"
        day.save()
        self.client.force_login(self.staff)
        resp = self.client.get(
            reverse("shifts:admin_period_assignment", args=[self.period.id])
        )
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "スタッフからのコメント")
        self.assertContains(resp, "来週は早退希望")
        self.assertContains(resp, "この日は15時まで")

    def test_割当グリッドの日付ヘッダーに充足状況が連動する(self):
        """割当グリッドが200で開き、日付ヘッダーにヒートマップの色が付く。"""
        self.client.force_login(self.staff)
        resp = self.client.get(
            reverse("shifts:admin_period_assignment", args=[self.period.id])
        )
        self.assertEqual(resp.status_code, 200)
        # 凡例が出ている
        self.assertContains(resp, "日付の色＝充足状況")
        # 2名がWORK・OFF0名なので OK（緑）判定 = 日付ヘッダーに heat-ok が付く
        self.assertContains(resp, "heat-ok")
