"""直行直帰の勤務時間計算のテスト（要件定義 §10・画面設計の UX 検証基準）。"""
import datetime
from decimal import Decimal
from unittest import mock

from django.contrib.auth.models import User
from django.test import TestCase

from sales import direct_return as dr
from sales.models import (
    DailyReport,
    DirectReturnDayAdjustment,
    DirectReturnExtraAttendance,
    DirectReturnWorkTime,
    SalesLocation,
    UserMenuPermission,
)

TODAY = datetime.date(2026, 10, 14)


def make_user(username, last, first, direct=True, viewer=True, staff=False):
    user = User.objects.create_user(username=username, password="pass", last_name=last, first_name=first, is_staff=staff)
    perm, _ = UserMenuPermission.objects.get_or_create(user=user)
    perm.direct_return = direct
    perm.can_view_direct_return_attendance = viewer
    perm.save()
    return user


def make_location(name, no, direct=1):
    return SalesLocation.objects.create(no=no, name=name, type="店", price_type="A", service_name="", direct_return=direct)


def work_time(location, start, end, effective_from=datetime.date(2026, 1, 1)):
    return DirectReturnWorkTime.objects.create(
        location=location, start_time=datetime.time.fromisoformat(start),
        end_time=datetime.time.fromisoformat(end), effective_from=effective_from,
    )


def report(user, d, location_name):
    return DailyReport.objects.create(date=d, location=location_name, total_revenue=0, person_in_charge=f"{user.last_name} {user.first_name}")


class PeriodTest(TestCase):
    def test_period_of(self):
        self.assertEqual(dr.period_of(2026, 10), (datetime.date(2026, 9, 16), datetime.date(2026, 10, 15)))
        self.assertEqual(dr.period_of(2026, 1), (datetime.date(2025, 12, 16), datetime.date(2026, 1, 15)))
        self.assertEqual(dr.period_of(2026, 3), (datetime.date(2026, 2, 16), datetime.date(2026, 3, 15)))

    def test_period_containing(self):
        self.assertEqual(dr.period_containing(datetime.date(2026, 10, 15)), (2026, 10))
        self.assertEqual(dr.period_containing(datetime.date(2026, 10, 16)), (2026, 11))
        self.assertEqual(dr.period_containing(datetime.date(2026, 12, 20)), (2027, 1))

    def test_label(self):
        self.assertEqual(dr.period_label(2026, 10), "10月分（9/16〜10/15）")

    def test_parse_adjust_hours(self):
        self.assertEqual(dr.parse_adjust_hours(""), (Decimal("0"), None))
        self.assertEqual(dr.parse_adjust_hours("0.75")[0], Decimal("0.75"))
        for bad in ("0.3", "-0.25", "abc", "25", "NaN"):
            self.assertIsNone(dr.parse_adjust_hours(bad)[0], bad)

    def test_format_hours(self):
        self.assertEqual(dr.format_hours(Decimal("4.5")), "4.50")
        self.assertEqual(dr.format_hours(Decimal("4")), "4.00")
        self.assertEqual(dr.format_hours(Decimal("3.75")), "3.75")


class CalculationTest(TestCase):
    def setUp(self):
        self.user = make_user("yamada", "山田", "太郎")
        self.a = make_location("シーサイド", 2)
        self.b = make_location("新横浜", 31)
        work_time(self.a, "10:00", "14:00")
        work_time(self.b, "10:30", "13:30")

    def summary(self, year=2026, month=10):
        start, end = dr.period_of(year, month)
        return dr.build_summaries([self.user], start, end)[0]

    def test_kakemochi_sums_each_locations_base(self):
        """基準1：日によって売り場が違う人は、日ごとの定時の合計になる。"""
        report(self.user, datetime.date(2026, 10, 1), "シーサイド")
        report(self.user, datetime.date(2026, 10, 2), "新横浜")
        s = self.summary()
        self.assertEqual(s.day_count, 2)
        self.assertEqual(s.hours_total, Decimal("7"))

    def test_work_time_change_keeps_old_days(self):
        """基準2：途中の日付で変えても、変更前の日は古い時間。"""
        work_time(self.a, "10:00", "15:00", effective_from=datetime.date(2026, 10, 5))
        report(self.user, datetime.date(2026, 10, 4), "シーサイド")
        report(self.user, datetime.date(2026, 10, 5), "シーサイド")
        s = self.summary()
        self.assertEqual([d.hours for d in s.days], [Decimal("4"), Decimal("5")])

    def test_adjustments(self):
        """基準3：早出0.25・残業0.5・遅刻0.25 → 定時＋0.5。"""
        report(self.user, datetime.date(2026, 10, 1), "シーサイド")
        DirectReturnDayAdjustment.objects.create(
            user=self.user, date=datetime.date(2026, 10, 1),
            early_hours=Decimal("0.25"), overtime_hours=Decimal("0.5"), late_hours=Decimal("0.25"),
        )
        s = self.summary()
        self.assertEqual(s.hours_total, Decimal("4.5"))
        self.assertEqual(s.base_total, Decimal("4"))

    def test_two_reports_same_day_is_one_day(self):
        """基準4：同じ日に日計表が2件でも1日（日計表は日付×売り場で1件なので、2件目は別の売り場）。売り場は先に送った方。"""
        report(self.user, datetime.date(2026, 10, 1), "シーサイド")
        report(self.user, datetime.date(2026, 10, 1), "新横浜")
        s = self.summary()
        self.assertEqual(s.day_count, 1)
        self.assertEqual(s.hours_total, Decimal("4"))

    def test_extra_attendance_counts(self):
        """基準5：出勤追加が出勤日数と勤務時間に入る。"""
        DirectReturnExtraAttendance.objects.create(user=self.user, date=datetime.date(2026, 10, 3), location=self.b)
        s = self.summary()
        self.assertEqual(s.day_count, 1)
        self.assertEqual(s.extra_count, 1)
        self.assertEqual(s.hours_total, Decimal("3"))

    def test_unset_location_not_counted(self):
        """基準6：就業時間が未設定の売り場の日は合計に入らない。"""
        make_location("新山下", 30)
        report(self.user, datetime.date(2026, 10, 1), "新山下")
        report(self.user, datetime.date(2026, 10, 2), "シーサイド")
        s = self.summary()
        self.assertEqual(s.day_count, 2)
        self.assertEqual(len(s.unset_days), 1)
        self.assertEqual(s.hours_total, Decimal("4"))

    def test_outside_period_ignored(self):
        report(self.user, datetime.date(2026, 10, 16), "シーサイド")
        report(self.user, datetime.date(2026, 9, 15), "シーサイド")
        self.assertEqual(self.summary().day_count, 0)

    def test_location_matched_by_name_with_spaces(self):
        report(self.user, datetime.date(2026, 10, 1), " シーサイド ")
        self.assertEqual(self.summary().hours_total, Decimal("4"))


@mock.patch("django.utils.timezone.localdate", return_value=TODAY)
class ScreenTest(TestCase):
    def setUp(self):
        self.admin = make_user("boss", "本部", "花子", direct=False, staff=True)
        self.staff = make_user("yamada", "山田", "太郎")
        self.other = make_user("sato", "佐藤", "次郎")
        self.loc = make_location("シーサイド", 2)
        work_time(self.loc, "10:00", "14:00")
        report(self.staff, datetime.date(2026, 10, 1), "シーサイド")
        report(self.other, datetime.date(2026, 10, 2), "シーサイド")
        DirectReturnDayAdjustment.objects.create(user=self.staff, date=datetime.date(2026, 10, 1), note="電車遅延",
                                                 late_hours=Decimal("0.25"))

    def test_admin_sees_everyone(self, _):
        self.client.force_login(self.admin)
        res = self.client.get("/direct_return_attendance/?period=2026-10")
        self.assertEqual(res.status_code, 200)
        self.assertContains(res, "山田 太郎")
        self.assertContains(res, "佐藤 次郎")
        self.assertContains(res, "3.75")
        self.assertContains(res, "日付×売り場")

    def test_old_query_still_works(self, _):
        self.client.force_login(self.admin)
        res = self.client.get("/direct_return_attendance/?target_year=2026&target_month=10")
        self.assertContains(res, "10月分（9/16〜10/15）")

    def test_self_sees_only_own_daily_and_total(self, _):
        """基準10：本人には日別・トータル・備考が出て、入力欄が無い。"""
        self.client.force_login(self.staff)
        res = self.client.get("/direct_return_attendance/?period=2026-10")
        self.assertEqual(res.status_code, 200)
        self.assertContains(res, "3.75h")
        self.assertContains(res, "電車遅延")
        self.assertNotContains(res, "佐藤 次郎")
        self.assertNotContains(res, 'type="number"')
        self.assertNotContains(res, "日付×売り場")

    def test_without_view_permission(self, _):
        """基準8：権限オフはURL直打ちでも開けない。"""
        user = make_user("nope", "無", "権限", viewer=False)
        self.client.force_login(user)
        res = self.client.get("/direct_return_attendance/")
        self.assertRedirects(res, "/dashboard/", fetch_redirect_response=False)

    def test_staff_cannot_use_admin_screens(self, _):
        """基準7：一般スタッフは調整・出勤追加・就業時間設定ができない。"""
        self.client.force_login(self.staff)
        urls = [
            f"/direct_return_attendance/staff/{self.staff.pk}/",
            f"/direct_return_attendance/staff/{self.staff.pk}/extra/new/",
            "/direct_return_attendance/work_times/",
            f"/direct_return_attendance/work_times/{self.loc.pk}/",
        ]
        for url in urls:
            self.assertRedirects(self.client.get(url), "/dashboard/", fetch_redirect_response=False)
            self.assertRedirects(self.client.post(url, {}), "/dashboard/", fetch_redirect_response=False)
        self.client.post(f"/direct_return_attendance/staff/{self.staff.pk}/?period=2026-10",
                         {"overtime_hours_2026-10-01": "5"})
        self.assertEqual(DirectReturnDayAdjustment.objects.get(user=self.staff).overtime_hours, 0)

    def test_toggle_requires_admin(self, _):
        self.client.force_login(self.staff)
        res = self.client.post(f"/user/toggle_direct_return/{self.other.pk}/", {"checked": "false"})
        self.assertEqual(res.status_code, 403)
        self.assertTrue(UserMenuPermission.objects.get(user=self.other).direct_return)

    def test_admin_saves_adjustments(self, _):
        self.client.force_login(self.admin)
        url = f"/direct_return_attendance/staff/{self.staff.pk}/?period=2026-10"
        res = self.client.post(url, {
            "early_hours_2026-10-01": "0.5", "late_hours_2026-10-01": "", "leave_early_hours_2026-10-01": "",
            "overtime_hours_2026-10-01": "1", "note_2026-10-01": "棚卸し",
        })
        self.assertEqual(res.status_code, 302)
        adj = DirectReturnDayAdjustment.objects.get(user=self.staff)
        self.assertEqual((adj.early_hours, adj.late_hours, adj.overtime_hours, adj.note),
                         (Decimal("0.5"), Decimal("0"), Decimal("1"), "棚卸し"))

    def test_invalid_step_saves_nothing(self, _):
        """UX4：0.3 はエラー。ほかの行も保存されず、入力値は残る。"""
        report(self.staff, datetime.date(2026, 10, 3), "シーサイド")
        self.client.force_login(self.admin)
        url = f"/direct_return_attendance/staff/{self.staff.pk}/?period=2026-10"
        res = self.client.post(url, {"overtime_hours_2026-10-01": "1", "overtime_hours_2026-10-03": "0.3"})
        self.assertEqual(res.status_code, 200)
        self.assertContains(res, "0.25h単位")
        self.assertContains(res, 'value="0.3"')
        self.assertEqual(DirectReturnDayAdjustment.objects.get(user=self.staff).overtime_hours, 0)

    def test_negative_day_rejected(self, _):
        self.client.force_login(self.admin)
        url = f"/direct_return_attendance/staff/{self.staff.pk}/?period=2026-10"
        res = self.client.post(url, {"late_hours_2026-10-01": "3", "leave_early_hours_2026-10-01": "2"})
        self.assertContains(res, "マイナス")

    def test_clearing_row_deletes_adjustment(self, _):
        self.client.force_login(self.admin)
        self.client.post(f"/direct_return_attendance/staff/{self.staff.pk}/?period=2026-10", {})
        self.assertFalse(DirectReturnDayAdjustment.objects.exists())

    def test_extra_add_and_duplicate_report_blocked(self, _):
        """基準5・UX6：日計表がある日は出勤追加できない。無い日は追加できる。"""
        self.client.force_login(self.admin)
        url = f"/direct_return_attendance/staff/{self.staff.pk}/extra/new/"
        res = self.client.post(url, {"date": "2026-10-01", "location": self.loc.pk, "reason": "training"})
        self.assertContains(res, "日計表（シーサイド）があるので")
        res = self.client.post(url, {"date": "2026-10-03", "location": self.loc.pk, "reason": "training"})
        self.assertRedirects(res, f"/direct_return_attendance/staff/{self.staff.pk}/?period=2026-10",
                             fetch_redirect_response=False)
        res = self.client.post(url, {"date": "2026-10-03", "location": self.loc.pk, "reason": "other"})
        self.assertContains(res, "もう出勤追加があります")
        self.assertEqual(DirectReturnExtraAttendance.objects.count(), 1)

    def test_extra_delete(self, _):
        extra = DirectReturnExtraAttendance.objects.create(user=self.staff, date=datetime.date(2026, 10, 3), location=self.loc)
        self.client.force_login(self.admin)
        self.assertEqual(self.client.get(f"/direct_return_attendance/extra/{extra.pk}/delete/").status_code, 405)
        self.client.post(f"/direct_return_attendance/extra/{extra.pk}/delete/")
        self.assertFalse(DirectReturnExtraAttendance.objects.exists())

    def test_work_time_confirm_then_register(self, _):
        """UX7：確認では保存されず、文章で言い直す。登録で保存。"""
        self.client.force_login(self.admin)
        url = f"/direct_return_attendance/work_times/{self.loc.pk}/"
        data = {"effective_from": "2026-11-01", "start_time": "10:00", "end_time": "14:30", "note": ""}
        res = self.client.post(url, {**data, "action": "confirm"})
        self.assertContains(res, "10:00〜14:00（4.00h）")
        self.assertContains(res, "10:00〜14:30（4.50h）")
        self.assertContains(res, "10/31までの勤務は今のまま")
        self.assertEqual(DirectReturnWorkTime.objects.count(), 1)
        res = self.client.post(url, {**data, "action": "register"})
        self.assertRedirects(res, "/direct_return_attendance/work_times/", fetch_redirect_response=False)
        self.assertEqual(DirectReturnWorkTime.objects.count(), 2)

    def test_work_time_validation(self, _):
        self.client.force_login(self.admin)
        url = f"/direct_return_attendance/work_times/{self.loc.pk}/"
        res = self.client.post(url, {"effective_from": "2026-11-01", "start_time": "14:00", "end_time": "10:00",
                                     "action": "confirm"})
        self.assertContains(res, "終業は始業より後にしてください")
        res = self.client.post(url, {"effective_from": "2026-11-01", "start_time": "10:10", "end_time": "14:00",
                                     "action": "confirm"})
        self.assertContains(res, "15分単位で入れてください")

    def test_work_time_cancel_only_pending(self, _):
        pending = work_time(self.loc, "10:00", "15:00", effective_from=datetime.date(2026, 11, 1))
        current = DirectReturnWorkTime.objects.get(effective_from=datetime.date(2026, 1, 1))
        self.client.force_login(self.admin)
        self.client.post(f"/direct_return_attendance/work_times/delete/{current.pk}/")
        self.client.post(f"/direct_return_attendance/work_times/delete/{pending.pk}/")
        self.assertEqual(list(DirectReturnWorkTime.objects.values_list("pk", flat=True)), [current.pk])

    def test_work_time_list_unset_first(self, _):
        make_location("新山下", 30)
        self.client.force_login(self.admin)
        res = self.client.get("/direct_return_attendance/work_times/")
        content = res.content.decode()
        self.assertLess(content.index("新山下"), content.index("シーサイド"))
        self.assertContains(res, "就業時間未設定")
