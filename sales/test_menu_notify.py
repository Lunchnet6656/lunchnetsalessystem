"""S4-5：週のメニューの確認し忘れ通知のテスト。"""
import datetime
import io
from unittest import mock

from django.core.management import call_command
from django.test import TestCase
from django.utils import timezone

from sales.menu_registry import register_week
from sales.models import MenuWeekCheck
from sales.test_menu_registry import NAMES

JST = timezone.get_fixed_timezone(9 * 60)
TUESDAY_EVENING = datetime.datetime(2026, 10, 13, 19, 30, tzinfo=JST)


def run(now, *args):
    out = io.StringIO()
    with mock.patch("django.utils.timezone.localtime", return_value=now), \
         mock.patch("django.utils.timezone.localdate", return_value=now.date()), \
         mock.patch("sales.management.commands.check_menu_week_confirmed.push_text", return_value=True) as push:
        call_command("check_menu_week_confirmed", *args, stdout=out)
    return out.getvalue(), push


class CheckMenuWeekConfirmedTest(TestCase):
    def test_not_received(self):
        out, push = run(TUESDAY_EVENING)
        self.assertIn("【メニュー 未受信】10/14(水)週", push.call_args[0][1])

    def test_unconfirmed(self):
        register_week(datetime.date(2026, 10, 14), NAMES)
        out, push = run(TUESDAY_EVENING)
        self.assertIn("【メニュー 未確認】10/14(水)週のメニューが未確認です（要確認 10品）。", push.call_args[0][1])

    def test_confirmed_is_silent(self):
        register_week(datetime.date(2026, 10, 14), NAMES)
        MenuWeekCheck.objects.update(confirmed_at=TUESDAY_EVENING)
        out, push = run(TUESDAY_EVENING)
        self.assertIn("確認済み", out)
        push.assert_not_called()

    def test_only_tuesday_evening(self):
        out, push = run(TUESDAY_EVENING.replace(hour=7))
        push.assert_not_called()
        out, push = run(TUESDAY_EVENING + datetime.timedelta(days=1))
        self.assertIn("火曜ではない", out)
        push.assert_not_called()
