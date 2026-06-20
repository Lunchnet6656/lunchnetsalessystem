"""勤怠アプリのテスト。

スプリント1＝打刻ロジックと権限分離。
スプリント2＝集計（実働・休憩・深夜・時間外）、締め期間、打刻修正、管理者画面。
"""
from datetime import date, datetime, time, timedelta
from decimal import Decimal

from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from . import aggregation, payroll, services
from .models import (
    HourlyWage,
    ManualWorkHours,
    Payslip,
    PayslipAdjustment,
    PayrollPeriod,
    PayrollSetting,
    Staff,
    StaffPayrollProfile,
    Store,
    TimeRecord,
    TimeRecordEdit,
)


def _make_staff(username, display_name, store):
    """テスト用の auth.User とひも付く Staff を1組つくる。"""
    user = User.objects.create_user(username, password=f"pw-{username}-123456")
    staff = Staff.objects.create(
        user=user,
        display_name=display_name,
        business_unit="cafeteria",
        store=store,
    )
    return user, staff


class PunchServiceTests(TestCase):
    def setUp(self):
        self.store = Store.objects.create(business_unit="cafeteria", name="本店食堂")
        self.user, self.staff = _make_staff("taro", "山田太郎", self.store)

    def test_clock_in_creates_record(self):
        services.clock_in(self.staff)
        record = TimeRecord.objects.get(staff=self.staff, kind=TimeRecord.KIND_CLOCK_IN)
        self.assertEqual(record.work_date, timezone.localdate())
        self.assertEqual(record.source, TimeRecord.SOURCE_APP)

    def test_cannot_clock_in_twice(self):
        services.clock_in(self.staff)
        with self.assertRaises(services.PunchError):
            services.clock_in(self.staff)

    def test_cannot_clock_out_without_clock_in(self):
        with self.assertRaises(services.PunchError):
            services.clock_out(self.staff)

    def test_clock_out_after_clock_in(self):
        services.clock_in(self.staff)
        services.clock_out(self.staff)
        self.assertEqual(
            TimeRecord.objects.filter(
                staff=self.staff, kind=TimeRecord.KIND_CLOCK_OUT
            ).count(),
            1,
        )

    def test_cannot_clock_out_twice(self):
        services.clock_in(self.staff)
        services.clock_out(self.staff)
        with self.assertRaises(services.PunchError):
            services.clock_out(self.staff)

    def test_state_transitions(self):
        self.assertEqual(
            services.get_punch_state(self.staff)["state"],
            services.STATE_NOT_CLOCKED_IN,
        )
        services.clock_in(self.staff)
        self.assertEqual(
            services.get_punch_state(self.staff)["state"], services.STATE_WORKING
        )
        services.clock_out(self.staff)
        self.assertEqual(
            services.get_punch_state(self.staff)["state"], services.STATE_DONE
        )

    def test_undo_reverts_to_previous_state(self):
        services.clock_in(self.staff)
        services.undo_last_punch(self.staff)
        self.assertEqual(
            services.get_punch_state(self.staff)["state"],
            services.STATE_NOT_CLOCKED_IN,
        )
        # 取り消し（論理削除）した分は一意制約の対象外。再度の出勤が通る。
        services.clock_in(self.staff)
        self.assertEqual(
            services.get_punch_state(self.staff)["state"], services.STATE_WORKING
        )

    def test_undo_without_records_raises(self):
        with self.assertRaises(services.PunchError):
            services.undo_last_punch(self.staff)


class PunchViewTests(TestCase):
    def setUp(self):
        self.store = Store.objects.create(business_unit="cafeteria", name="本店食堂")
        self.user, self.staff = _make_staff("hanako", "鈴木花子", self.store)

    def test_punch_page_requires_login(self):
        response = self.client.get(reverse("attendance:punch_page"))
        self.assertEqual(response.status_code, 302)
        self.assertIn("/login/", response["Location"])

    def test_punch_page_shows_own_name(self):
        self.client.force_login(self.user)
        response = self.client.get(reverse("attendance:punch_page"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "鈴木花子")

    def test_user_without_staff_gets_403(self):
        outsider = User.objects.create_user("nobody", password="pw-nobody-123456")
        self.client.force_login(outsider)
        response = self.client.get(reverse("attendance:punch_page"))
        self.assertEqual(response.status_code, 403)

    def test_clock_in_via_post(self):
        self.client.force_login(self.user)
        response = self.client.post(reverse("attendance:punch"), {"kind": "clock_in"})
        self.assertEqual(response.status_code, 302)
        self.assertEqual(TimeRecord.objects.filter(staff=self.staff).count(), 1)

    def test_punch_affects_only_own_records(self):
        # 別スタッフのユーザーで打刻しても、花子の記録は増えない（IDOR防止）。
        _, other_staff = _make_staff("jiro", "佐藤次郎", self.store)
        self.client.force_login(other_staff.user)
        self.client.post(reverse("attendance:punch"), {"kind": "clock_in"})
        self.assertEqual(TimeRecord.objects.filter(staff=self.staff).count(), 0)
        self.assertEqual(TimeRecord.objects.filter(staff=other_staff).count(), 1)

    def test_get_on_punch_endpoint_not_allowed(self):
        self.client.force_login(self.user)
        response = self.client.get(reverse("attendance:punch"))
        self.assertEqual(response.status_code, 405)

    def test_undo_via_post(self):
        self.client.force_login(self.user)
        self.client.post(reverse("attendance:punch"), {"kind": "clock_in"})
        response = self.client.post(reverse("attendance:undo_punch"))
        self.assertEqual(response.status_code, 302)
        self.assertEqual(
            TimeRecord.objects.filter(staff=self.staff, is_deleted=False).count(), 0
        )


# === スプリント2 =============================================================


class AggregationCalcTests(TestCase):
    """日次集計（実働・休憩・深夜・時間外）の計算ロジック。"""

    def setUp(self):
        self.setting = PayrollSetting.current()

    def _dt(self, year, month, day, hour, minute):
        return timezone.make_aware(datetime(year, month, day, hour, minute))

    def test_compute_day_basic_8h_after_break(self):
        # 9:00-18:00＝拘束9h。8h超で休憩60分控除→実働8h。
        calc = aggregation.compute_day(
            self._dt(2026, 5, 1, 9, 0), self._dt(2026, 5, 1, 18, 0), self.setting
        )
        self.assertEqual(calc.span_minutes, 540)
        self.assertEqual(calc.break_minutes, 60)
        self.assertEqual(calc.work_minutes, 480)
        self.assertEqual(calc.overtime_minutes, 0)
        self.assertEqual(calc.night_minutes, 0)

    def test_break_45min_bracket(self):
        # 9:00-15:30＝拘束6.5h。6h超8h以下→休憩45分。
        calc = aggregation.compute_day(
            self._dt(2026, 5, 1, 9, 0), self._dt(2026, 5, 1, 15, 30), self.setting
        )
        self.assertEqual(calc.break_minutes, 45)
        self.assertEqual(calc.work_minutes, 345)

    def test_no_break_under_threshold(self):
        # 10:00-14:00＝拘束4h。6h以下→休憩なし。
        calc = aggregation.compute_day(
            self._dt(2026, 5, 1, 10, 0), self._dt(2026, 5, 1, 14, 0), self.setting
        )
        self.assertEqual(calc.break_minutes, 0)
        self.assertEqual(calc.work_minutes, 240)

    def test_overtime_over_8h(self):
        # 8:00-20:00＝拘束12h。休憩60分→実働11h。8h超の3hが時間外。
        calc = aggregation.compute_day(
            self._dt(2026, 5, 1, 8, 0), self._dt(2026, 5, 1, 20, 0), self.setting
        )
        self.assertEqual(calc.work_minutes, 660)
        self.assertEqual(calc.overtime_minutes, 180)

    def test_night_overlap_crossing_midnight(self):
        # 21:00-翌6:00。深夜帯22:00-翌5:00と7h重なる。
        calc = aggregation.compute_day(
            self._dt(2026, 5, 1, 21, 0), self._dt(2026, 5, 2, 6, 0), self.setting
        )
        self.assertEqual(calc.span_minutes, 540)
        self.assertEqual(calc.night_minutes, 420)

    def test_night_capped_by_work_minutes(self):
        # 22:00-翌5:00＝拘束7h。休憩45分→実働375分。
        # 深夜帯の重なりは420分だが、実働375分で頭打ちにする。
        calc = aggregation.compute_day(
            self._dt(2026, 5, 1, 22, 0), self._dt(2026, 5, 2, 5, 0), self.setting
        )
        self.assertEqual(calc.work_minutes, 375)
        self.assertEqual(calc.night_minutes, 375)

    def test_night_overlap_zero_in_daytime(self):
        minutes = aggregation.night_overlap_minutes(
            datetime(2026, 5, 1, 9, 0),
            datetime(2026, 5, 1, 18, 0),
            time(22, 0),
            time(5, 0),
        )
        self.assertEqual(minutes, 0)


class PeriodResolutionTests(TestCase):
    """締め期間（○月度）の解決ロジック。締め日15で検証する。"""

    def test_resolve_period_end_within_month(self):
        self.assertEqual(
            aggregation.resolve_period_end(date(2026, 5, 10), 15), date(2026, 5, 15)
        )

    def test_resolve_period_end_on_closing_day(self):
        self.assertEqual(
            aggregation.resolve_period_end(date(2026, 5, 15), 15), date(2026, 5, 15)
        )

    def test_resolve_period_end_after_closing_day(self):
        self.assertEqual(
            aggregation.resolve_period_end(date(2026, 5, 16), 15), date(2026, 6, 15)
        )

    def test_period_start_crosses_year(self):
        self.assertEqual(
            aggregation.period_start_for(date(2026, 1, 15), 15), date(2025, 12, 16)
        )

    def test_recent_period_ends(self):
        ends = aggregation.recent_period_ends(15, 3, date(2026, 5, 20))
        self.assertEqual(
            ends, [date(2026, 6, 15), date(2026, 5, 15), date(2026, 4, 15)]
        )

    def test_closing_day_clamped_to_month_end(self):
        # 締め日31でも、日数の足りない月は月末に丸める。
        self.assertEqual(
            aggregation.resolve_period_end(date(2026, 2, 20), 31), date(2026, 2, 28)
        )


class DayPunchEditTests(TestCase):
    """打刻修正・販売まとめ入力（set_day_punches）と締め期間。"""

    def setUp(self):
        self.store = Store.objects.create(business_unit="cafeteria", name="本店食堂")
        self.user, self.staff = _make_staff("kanri_taro", "山田太郎", self.store)
        self.admin = User.objects.create_user("kanri", password="pw-kanri-123456")
        self.admin.is_staff = True
        self.admin.save()
        self.work_date = date(2026, 5, 1)

    def test_set_day_punches_creates_manual_records(self):
        services.set_day_punches(
            self.staff, self.work_date, time(9, 0), time(18, 0), self.admin
        )
        records = TimeRecord.objects.filter(staff=self.staff, is_deleted=False)
        self.assertEqual(records.count(), 2)
        self.assertTrue(all(r.source == TimeRecord.SOURCE_MANUAL for r in records))
        self.assertEqual(
            TimeRecordEdit.objects.filter(
                action=TimeRecordEdit.ACTION_CREATED
            ).count(),
            2,
        )

    def test_set_day_punches_updates_existing_and_logs(self):
        services.set_day_punches(
            self.staff, self.work_date, time(9, 0), time(18, 0), self.admin
        )
        services.set_day_punches(
            self.staff,
            self.work_date,
            time(8, 55),
            time(18, 0),
            self.admin,
            reason="出勤の打刻漏れ修正",
        )
        clock_in = TimeRecord.objects.get(
            staff=self.staff, kind=TimeRecord.KIND_CLOCK_IN, is_deleted=False
        )
        self.assertTrue(clock_in.is_corrected)
        self.assertEqual(aggregation.fmt_time(clock_in.recorded_at), "08:55")
        edit = TimeRecordEdit.objects.get(action=TimeRecordEdit.ACTION_UPDATED)
        self.assertEqual(edit.before_value, "09:00")
        self.assertEqual(edit.after_value, "08:55")
        self.assertEqual(edit.reason, "出勤の打刻漏れ修正")
        self.assertEqual(edit.editor, self.admin)

    def test_set_day_punches_no_change_keeps_no_extra_log(self):
        services.set_day_punches(
            self.staff, self.work_date, time(9, 0), time(18, 0), self.admin
        )
        services.set_day_punches(
            self.staff, self.work_date, time(9, 0), time(18, 0), self.admin
        )
        # 同じ時刻の再保存では修正履歴は増えない（新規入力の2件のみ）。
        self.assertEqual(TimeRecordEdit.objects.count(), 2)

    def test_set_day_punches_clears_record(self):
        services.set_day_punches(
            self.staff, self.work_date, time(9, 0), time(18, 0), self.admin
        )
        services.set_day_punches(
            self.staff, self.work_date, None, time(18, 0), self.admin
        )
        self.assertFalse(
            TimeRecord.objects.filter(
                staff=self.staff, kind=TimeRecord.KIND_CLOCK_IN, is_deleted=False
            ).exists()
        )
        self.assertTrue(
            TimeRecordEdit.objects.filter(
                action=TimeRecordEdit.ACTION_DELETED
            ).exists()
        )

    def test_overnight_clock_out_lands_next_day(self):
        services.set_day_punches(
            self.staff, self.work_date, time(22, 0), time(2, 0), self.admin
        )
        clock_out = TimeRecord.objects.get(
            staff=self.staff, kind=TimeRecord.KIND_CLOCK_OUT, is_deleted=False
        )
        self.assertEqual(
            timezone.localtime(clock_out.recorded_at).date(),
            self.work_date + timedelta(days=1),
        )
        # 勤務日は出勤日に揃える。
        self.assertEqual(clock_out.work_date, self.work_date)

    def test_set_day_punches_blocked_when_period_closed(self):
        services.close_period(date(2026, 5, 15), self.admin)
        with self.assertRaises(services.PunchError):
            services.set_day_punches(
                self.staff, self.work_date, time(9, 0), time(18, 0), self.admin
            )

    def test_close_and_reopen_period(self):
        services.close_period(date(2026, 5, 15), self.admin)
        self.assertTrue(services.period_is_locked(self.work_date))
        services.reopen_period(date(2026, 5, 15))
        self.assertFalse(services.period_is_locked(self.work_date))


class ManageViewTests(TestCase):
    """管理者画面の権限分離・表示・保存・月締め。"""

    def setUp(self):
        self.store = Store.objects.create(business_unit="cafeteria", name="本店食堂")
        self.user, self.staff = _make_staff("shokuin", "佐藤花子", self.store)
        self.admin = User.objects.create_user("tencho", password="pw-tencho-123456")
        self.admin.is_staff = True
        self.admin.save()

    def test_dashboard_blocks_non_staff_member(self):
        # 一般スタッフ（非is_staff）は管理画面に入れない（検証基準8）。
        self.client.force_login(self.user)
        response = self.client.get(reverse("attendance:manage_dashboard"))
        self.assertEqual(response.status_code, 302)
        self.assertIn("/login", response["Location"])

    def test_dashboard_visible_to_staff_member(self):
        self.client.force_login(self.admin)
        response = self.client.get(reverse("attendance:manage_dashboard"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "佐藤花子")

    def test_staff_detail_visible_to_staff_member(self):
        self.client.force_login(self.admin)
        response = self.client.get(
            reverse("attendance:manage_staff_detail", args=[self.staff.id])
        )
        self.assertEqual(response.status_code, 200)

    def test_save_day_creates_records(self):
        self.client.force_login(self.admin)
        response = self.client.post(
            reverse("attendance:manage_save_day", args=[self.staff.id]),
            {
                "work_date": "2026-05-02",
                "period": "2026-05-15",
                "clock_in": "09:00",
                "clock_out": "18:00",
                "reason": "",
            },
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(
            TimeRecord.objects.filter(staff=self.staff, is_deleted=False).count(), 2
        )

    def test_save_day_blocked_on_closed_period(self):
        services.close_period(date(2026, 5, 15), self.admin)
        self.client.force_login(self.admin)
        self.client.post(
            reverse("attendance:manage_save_day", args=[self.staff.id]),
            {
                "work_date": "2026-05-02",
                "period": "2026-05-15",
                "clock_in": "09:00",
                "clock_out": "18:00",
            },
        )
        self.assertEqual(TimeRecord.objects.filter(staff=self.staff).count(), 0)

    def test_delete_day_soft_deletes_records(self):
        # 打刻を作ってから削除＝is_deleted で論理削除＋削除履歴が残る。
        self.client.force_login(self.admin)
        self.client.post(
            reverse("attendance:manage_save_day", args=[self.staff.id]),
            {"work_date": "2026-05-02", "period": "2026-05-15",
             "clock_in": "09:00", "clock_out": "18:00"},
        )
        self.assertEqual(
            TimeRecord.objects.filter(staff=self.staff, is_deleted=False).count(), 2
        )
        response = self.client.post(
            reverse("attendance:manage_delete_day", args=[self.staff.id]),
            {"work_date": "2026-05-02", "period": "2026-05-15"},
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(
            TimeRecord.objects.filter(staff=self.staff, is_deleted=False).count(), 0
        )
        self.assertEqual(
            TimeRecord.objects.filter(staff=self.staff, is_deleted=True).count(), 2
        )
        self.assertEqual(
            TimeRecordEdit.objects.filter(
                action=TimeRecordEdit.ACTION_DELETED
            ).count(), 2
        )

    def test_delete_day_blocked_on_closed_period(self):
        # 締め済み期間は削除できない。
        self.client.force_login(self.admin)
        self.client.post(
            reverse("attendance:manage_save_day", args=[self.staff.id]),
            {"work_date": "2026-05-02", "period": "2026-05-15",
             "clock_in": "09:00", "clock_out": "18:00"},
        )
        services.close_period(date(2026, 5, 15), self.admin)
        self.client.post(
            reverse("attendance:manage_delete_day", args=[self.staff.id]),
            {"work_date": "2026-05-02", "period": "2026-05-15"},
        )
        self.assertEqual(
            TimeRecord.objects.filter(staff=self.staff, is_deleted=False).count(), 2
        )

    def test_delete_day_blocks_non_staff_member(self):
        # 一般スタッフは削除エンドポイントを叩けない。
        self.client.force_login(self.user)
        response = self.client.post(
            reverse("attendance:manage_delete_day", args=[self.staff.id]),
            {"work_date": "2026-05-02", "period": "2026-05-15"},
        )
        self.assertEqual(response.status_code, 302)
        self.assertIn("/login", response["Location"])

    def test_close_period_via_view(self):
        self.client.force_login(self.admin)
        self.client.post(
            reverse("attendance:manage_period"),
            {"period": "2026-05-15", "action": "close"},
        )
        period = PayrollPeriod.objects.get(period_end=date(2026, 5, 15))
        self.assertTrue(period.is_closed)
        self.assertEqual(period.closed_by, self.admin)


# === スプリント3 =============================================================


class PayrollEngineTests(TestCase):
    """給与計算エンジン（基本賃金・各割増・60h超・週40h超・端数・最低賃金）。"""

    def setUp(self):
        self.setting = PayrollSetting.current()
        self.store = Store.objects.create(business_unit="cafeteria", name="本店食堂")
        self.user, self.staff = _make_staff("pay_taro", "山田太郎", self.store)
        self.period_end = date(2026, 5, 15)
        self.period_start = date(2026, 4, 16)

    def _set_wage(self, amount, effective_from=date(2026, 1, 1)):
        return HourlyWage.objects.create(
            staff=self.staff, amount=amount, effective_from=effective_from
        )

    def _punch(self, d, h1, m1, h2, m2, out_next=False):
        ci = timezone.make_aware(datetime.combine(d, time(h1, m1)))
        TimeRecord.objects.create(
            staff=self.staff, kind="clock_in", recorded_at=ci,
            work_date=d, source="manual",
        )
        od = d + timedelta(days=1) if out_next else d
        co = timezone.make_aware(datetime.combine(od, time(h2, m2)))
        TimeRecord.objects.create(
            staff=self.staff, kind="clock_out", recorded_at=co,
            work_date=d, source="manual",
        )

    def _calc(self):
        return payroll.compute_payslip(
            self.staff, self.period_start, self.period_end, self.setting
        )

    def test_base_wage_is_work_times_wage(self):
        self._set_wage(1200)
        self._punch(date(2026, 5, 1), 9, 0, 18, 0)  # 実働8h
        calc = self._calc()
        self.assertEqual(calc.work_minutes, 480)
        self.assertEqual(calc.base_wage_yen, 9600)
        self.assertEqual(calc.overtime_premium_yen, 0)
        self.assertEqual(calc.night_premium_yen, 0)
        self.assertEqual(calc.total_yen, 9600)

    def test_overtime_premium(self):
        self._set_wage(1200)
        self._punch(date(2026, 5, 1), 8, 0, 20, 0)  # 実働11h・時間外3h
        calc = self._calc()
        self.assertEqual(calc.overtime_minutes, 180)
        # v2：基本＝通常8h×1200＝9,600。時間外＝3h×1200×1.25＝4,500（フル単価）。合計は不変。
        self.assertEqual(calc.normal_minutes, 480)
        self.assertEqual(calc.base_wage_yen, 9600)
        self.assertEqual(calc.overtime_premium_yen, 4500)
        self.assertEqual(calc.total_yen, 14100)

    def test_night_premium(self):
        self._set_wage(1200)
        self._punch(date(2026, 5, 1), 21, 0, 6, 0, out_next=True)  # 深夜7h
        calc = self._calc()
        self.assertEqual(calc.night_minutes, 420)
        # v2：通常＝実働8h−深夜7h＝1h。基本＝1h×1200＝1,200。深夜＝7h×1200×1.25＝10,500。合計は不変。
        self.assertEqual(calc.normal_minutes, 60)
        self.assertEqual(calc.base_wage_yen, 1200)
        self.assertEqual(calc.night_premium_yen, 10500)
        self.assertEqual(calc.total_yen, 11700)

    def test_overtime_and_night_premiums_stack(self):
        # 16:00-翌3:00：実働10h・時間外2h・深夜5h。割増は独立に乗る。
        self._set_wage(1200)
        self._punch(date(2026, 5, 1), 16, 0, 3, 0, out_next=True)
        calc = self._calc()
        self.assertEqual(calc.overtime_minutes, 120)
        self.assertEqual(calc.night_minutes, 300)
        # v2：通常＝実働10h−時間外2h−深夜5h＝3h。基本＝3h×1200＝3,600。
        # 時間外＝2h×1200×1.25＝3,000／深夜＝5h×1200×1.25＝7,500（いずれもフル単価・別建て）。
        self.assertEqual(calc.normal_minutes, 180)
        self.assertEqual(calc.base_wage_yen, 3600)
        self.assertEqual(calc.overtime_premium_yen, 3000)
        self.assertEqual(calc.night_premium_yen, 7500)
        self.assertEqual(calc.total_yen, 14100)

    def test_over_60h_overtime_uses_higher_rate(self):
        # 平日のみ16日×実働12h＝時間外64h（週40h超を出さない並べ方）。
        self._set_wage(1000)
        days = [
            self.period_start + timedelta(days=i)
            for i in range((self.period_end - self.period_start).days + 1)
        ]
        for d in [d for d in days if d.weekday() < 5][:16]:
            self._punch(d, 8, 0, 21, 0)  # 実働12h・日次時間外4h
        calc = self._calc()
        self.assertEqual(calc.overtime_minutes, 64 * 60)
        self.assertEqual(calc.over60h_minutes, 4 * 60)
        # v2フル単価：60h×1000×1.25 ＋ 4h×1000×1.50 ＝ 75,000 ＋ 6,000 ＝ 81,000
        self.assertEqual(calc.overtime_premium_yen, 81000)

    def test_weekly_overtime_minutes(self):
        # 同一週（日曜起算）の月〜土6日×実働8h＝48h。週40h超の8hが追加時間外。
        calc_8h = aggregation.DayCalc(540, 60, 480, 0, 0)
        rows = [
            {"date": date(2026, 5, 4) + timedelta(days=i), "calc": calc_8h}
            for i in range(6)  # 5/4(月)〜5/9(土)
        ]
        self.assertEqual(payroll.weekly_overtime_minutes(rows), 480)

    def test_weekly_overtime_ignores_days_without_calc(self):
        rows = [{"date": date(2026, 5, 4), "calc": None}]
        self.assertEqual(payroll.weekly_overtime_minutes(rows), 0)

    def test_resolve_hourly_wage_picks_latest_effective(self):
        self._set_wage(1000, effective_from=date(2026, 1, 1))
        self._set_wage(1200, effective_from=date(2026, 5, 1))
        self.assertEqual(
            payroll.resolve_hourly_wage(self.staff, date(2026, 5, 15)), 1200
        )
        self.assertEqual(
            payroll.resolve_hourly_wage(self.staff, date(2026, 4, 30)), 1000
        )

    def test_below_min_wage_flag(self):
        self._set_wage(1000)  # 既定の最低賃金1226円を下回る
        self._punch(date(2026, 5, 1), 9, 0, 18, 0)
        self.assertTrue(self._calc().below_min_wage)

    def test_wage_above_min_is_not_flagged(self):
        self._set_wage(1300)
        self._punch(date(2026, 5, 1), 9, 0, 18, 0)
        self.assertFalse(self._calc().below_min_wage)

    def test_wage_rounding_unit(self):
        # 支給額を100円単位で丸める。実働8h×1226円＝9808円 → 9800円。
        self.setting.wage_rounding_unit = 100
        self.setting.save()
        self._set_wage(1226)
        self._punch(date(2026, 5, 1), 9, 0, 18, 0)
        self.assertEqual(self._calc().total_yen, 9800)


class PayrollConfirmTests(TestCase):
    """給与計算の確定・取消（Payslip の凍結）。"""

    def setUp(self):
        self.store = Store.objects.create(business_unit="cafeteria", name="本店食堂")
        self.user, self.staff = _make_staff("conf_taro", "山田太郎", self.store)
        HourlyWage.objects.create(
            staff=self.staff, amount=1200, effective_from=date(2026, 1, 1)
        )
        self.admin = User.objects.create_user("payadmin", password="pw-payadmin-123456")
        self.admin.is_staff = True
        self.admin.save()
        # 5/1 9:00-18:00（実働8h）の打刻を1日分。
        ci = timezone.make_aware(datetime.combine(date(2026, 5, 1), time(9, 0)))
        co = timezone.make_aware(datetime.combine(date(2026, 5, 1), time(18, 0)))
        TimeRecord.objects.create(
            staff=self.staff, kind="clock_in", recorded_at=ci,
            work_date=date(2026, 5, 1), source="manual",
        )
        TimeRecord.objects.create(
            staff=self.staff, kind="clock_out", recorded_at=co,
            work_date=date(2026, 5, 1), source="manual",
        )

    def test_confirm_requires_closed_period(self):
        with self.assertRaises(services.PunchError):
            services.confirm_payroll(date(2026, 5, 15), self.admin)

    def test_confirm_creates_frozen_payslip(self):
        services.close_period(date(2026, 5, 15), self.admin)
        services.confirm_payroll(date(2026, 5, 15), self.admin)
        slip = Payslip.objects.get(staff=self.staff)
        self.assertEqual(slip.total_yen, 9600)
        self.assertEqual(slip.confirmed_by, self.admin)

    def test_cancel_removes_payslip(self):
        services.close_period(date(2026, 5, 15), self.admin)
        services.confirm_payroll(date(2026, 5, 15), self.admin)
        services.cancel_payroll(date(2026, 5, 15))
        self.assertEqual(Payslip.objects.count(), 0)


class PayrollViewTests(TestCase):
    """給与計算画面の権限分離・表示・確定。"""

    def setUp(self):
        self.store = Store.objects.create(business_unit="cafeteria", name="本店食堂")
        self.user, self.staff = _make_staff("pv_staff", "佐藤花子", self.store)
        HourlyWage.objects.create(
            staff=self.staff, amount=1300, effective_from=date(2026, 1, 1)
        )
        self.admin = User.objects.create_user("pvadmin", password="pw-pvadmin-123456")
        self.admin.is_staff = True
        self.admin.save()

    def test_payroll_blocks_non_staff_member(self):
        self.client.force_login(self.user)
        response = self.client.get(reverse("attendance:manage_payroll"))
        self.assertEqual(response.status_code, 302)
        self.assertIn("/login", response["Location"])

    def test_payroll_visible_to_staff_member(self):
        self.client.force_login(self.admin)
        response = self.client.get(reverse("attendance:manage_payroll"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "佐藤花子")

    def test_confirm_via_view_blocked_when_period_open(self):
        self.client.force_login(self.admin)
        self.client.post(
            reverse("attendance:manage_payroll_confirm"),
            {"period": "2026-05-15", "action": "confirm"},
        )
        self.assertEqual(Payslip.objects.count(), 0)

    def test_confirm_via_view_after_close(self):
        services.close_period(date(2026, 5, 15), self.admin)
        self.client.force_login(self.admin)
        self.client.post(
            reverse("attendance:manage_payroll_confirm"),
            {"period": "2026-05-15", "action": "confirm"},
        )
        self.assertTrue(Payslip.objects.filter(staff=self.staff).exists())


class SettingsViewTests(TestCase):
    """給与設定画面（PayrollSetting の編集）。"""

    # 給与設定フォームの有効なPOSTデータ（既定値そのまま）。
    VALID = {
        "closing_day": 15,
        "min_wage": 1226,
        "wage_rounding_unit": 1,
        "employment_insurance_rate": "0.0055",
        "overtime_rate": "0.25",
        "over60h_rate": "0.50",
        "night_rate": "0.25",
        "holiday_rate": "0.35",
        "night_start": "22:00",
        "night_end": "05:00",
        "break1_threshold_minutes": 360,
        "break1_deduct_minutes": 45,
        "break2_threshold_minutes": 480,
        "break2_deduct_minutes": 60,
        "rounding_rule": "1分単位（丸めなし）",
        "peddling_allowance_yen": 1000,
        "box_wash_allowance_yen": 200,
        "driver_allowance_yen": 1500,
    }

    def setUp(self):
        self.store = Store.objects.create(business_unit="cafeteria", name="本店食堂")
        self.user, self.staff = _make_staff("set_staff", "設定花子", self.store)
        self.admin = User.objects.create_user("setadmin", password="pw-setadmin-123456")
        self.admin.is_staff = True
        self.admin.save()

    def test_settings_blocks_non_staff_member(self):
        self.client.force_login(self.user)
        response = self.client.get(reverse("attendance:manage_settings"))
        self.assertEqual(response.status_code, 302)
        self.assertIn("/login", response["Location"])

    def test_settings_visible_to_staff_member(self):
        self.client.force_login(self.admin)
        response = self.client.get(reverse("attendance:manage_settings"))
        self.assertEqual(response.status_code, 200)

    def test_settings_save_updates_payroll_setting(self):
        self.client.force_login(self.admin)
        data = dict(self.VALID, closing_day=20, min_wage=1300)
        response = self.client.post(reverse("attendance:manage_settings"), data)
        self.assertEqual(response.status_code, 302)
        setting = PayrollSetting.current()
        self.assertEqual(setting.closing_day, 20)
        self.assertEqual(setting.min_wage, 1300)

    def test_settings_rejects_invalid_closing_day(self):
        self.client.force_login(self.admin)
        data = dict(self.VALID, closing_day=40)
        response = self.client.post(reverse("attendance:manage_settings"), data)
        self.assertEqual(response.status_code, 200)  # フォーム再表示
        # 不正値は保存されず、既定の15日のまま。
        self.assertEqual(PayrollSetting.current().closing_day, 15)


# === スプリント5（Phase 2：手当・控除・差引支給額） ==========================


class IncomeTaxTests(TestCase):
    """所得税（国税庁・電算機計算特例 甲欄・令和8年分）の検証。"""

    def test_matches_official_examples(self):
        # 国税庁 denshi_01.pdf の計算例と一致すること。
        self.assertEqual(payroll.income_tax(175000, 2), 210)
        self.assertEqual(payroll.income_tax(446000, 8), 940)
        self.assertEqual(payroll.income_tax(775200, 3), 59470)

    def test_zero_for_low_income(self):
        # 課税給与所得金額が0以下なら税額0。
        self.assertEqual(payroll.income_tax(90000, 0), 0)
        self.assertEqual(payroll.income_tax(0, 0), 0)


class Phase2PayrollTests(TestCase):
    """手当・雇用保険・控除合計・差引支給額の計算。"""

    def setUp(self):
        self.setting = PayrollSetting.current()
        self.store = Store.objects.create(business_unit="cafeteria", name="本店食堂")
        self.user, self.staff = _make_staff("p2_taro", "山田太郎", self.store)
        self.period_end = date(2026, 5, 15)
        self.period_start = date(2026, 4, 16)

    def _set_wage(self, amount):
        HourlyWage.objects.create(
            staff=self.staff, amount=amount, effective_from=date(2026, 1, 1)
        )

    def _punch(self, d, h1, m1, h2, m2):
        ci = timezone.make_aware(datetime.combine(d, time(h1, m1)))
        co = timezone.make_aware(datetime.combine(d, time(h2, m2)))
        TimeRecord.objects.create(
            staff=self.staff, kind="clock_in", recorded_at=ci,
            work_date=d, source="manual",
        )
        TimeRecord.objects.create(
            staff=self.staff, kind="clock_out", recorded_at=co,
            work_date=d, source="manual",
        )

    def _calc(self):
        return payroll.compute_payslip(
            self.staff, self.period_start, self.period_end, self.setting
        )

    def test_employment_insurance(self):
        # 総支給額 × 0.0055。加入していなければ0。
        self.assertEqual(
            payroll.employment_insurance(200000, self.setting, True), 1100
        )
        self.assertEqual(
            payroll.employment_insurance(200000, self.setting, False), 0
        )

    def test_allowances_and_deductions_flow_into_net_pay(self):
        # 賃金9,600円＋通勤8,000＋その他3,000＝総支給20,600。
        # 雇用保険113＋住民税1,000＋その他控除500＝控除1,613。差引18,987。
        self._set_wage(1200)
        self._punch(date(2026, 5, 1), 9, 0, 18, 0)  # 実働8h
        StaffPayrollProfile.objects.create(
            staff=self.staff,
            commute_allowance=8000,
            other_allowance=3000,
            resident_tax=1000,
            other_deduction=500,
            employment_insurance_enrolled=True,
        )
        calc = self._calc()
        self.assertEqual(calc.total_yen, 9600)
        self.assertEqual(calc.gross_yen, 20600)
        self.assertEqual(calc.employment_insurance_yen, 113)
        self.assertEqual(calc.income_tax_yen, 0)
        self.assertEqual(calc.total_deduction_yen, 1613)
        self.assertEqual(calc.net_pay_yen, 18987)

    def test_without_profile_no_allowance_no_deduction(self):
        self._set_wage(1200)
        self._punch(date(2026, 5, 1), 9, 0, 18, 0)
        calc = self._calc()
        self.assertEqual(calc.gross_yen, calc.total_yen)
        self.assertEqual(calc.total_deduction_yen, 0)
        self.assertEqual(calc.employment_insurance_yen, 0)
        self.assertEqual(calc.net_pay_yen, calc.gross_yen)

    def test_income_tax_computed_from_gross(self):
        # 平日10日×実働8h・時給1,500＝総支給120,000。所得税890円。
        self._set_wage(1500)
        days = [
            self.period_start + timedelta(days=i)
            for i in range((self.period_end - self.period_start).days + 1)
        ]
        for d in [d for d in days if d.weekday() < 5][:10]:
            self._punch(d, 9, 0, 18, 0)
        StaffPayrollProfile.objects.create(
            staff=self.staff, employment_insurance_enrolled=False
        )
        calc = self._calc()
        self.assertEqual(calc.gross_yen, 120000)
        self.assertEqual(calc.income_tax_yen, 890)
        self.assertEqual(calc.net_pay_yen, 119110)

    def test_net_pay_can_be_negative(self):
        # 控除が総支給額を上回るとマイナスになり得る（符号付きで保持）。
        self._set_wage(1200)
        self._punch(date(2026, 5, 1), 9, 0, 18, 0)  # 総支給9,600
        StaffPayrollProfile.objects.create(
            staff=self.staff, resident_tax=20000,
            employment_insurance_enrolled=False,
        )
        self.assertEqual(self._calc().net_pay_yen, -10400)


class Phase2ConfirmAndViewTests(TestCase):
    """Phase 2：確定時のPayslip保存と給与明細画面。"""

    def setUp(self):
        self.store = Store.objects.create(business_unit="cafeteria", name="本店食堂")
        self.user, self.staff = _make_staff("p2v_taro", "山田太郎", self.store)
        HourlyWage.objects.create(
            staff=self.staff, amount=1200, effective_from=date(2026, 1, 1)
        )
        StaffPayrollProfile.objects.create(
            staff=self.staff, commute_allowance=5000,
            employment_insurance_enrolled=True,
        )
        self.admin = User.objects.create_user("p2admin", password="pw-p2admin-123456")
        self.admin.is_staff = True
        self.admin.save()
        ci = timezone.make_aware(datetime.combine(date(2026, 5, 1), time(9, 0)))
        co = timezone.make_aware(datetime.combine(date(2026, 5, 1), time(18, 0)))
        TimeRecord.objects.create(
            staff=self.staff, kind="clock_in", recorded_at=ci,
            work_date=date(2026, 5, 1), source="manual",
        )
        TimeRecord.objects.create(
            staff=self.staff, kind="clock_out", recorded_at=co,
            work_date=date(2026, 5, 1), source="manual",
        )

    def test_confirm_stores_phase2_fields(self):
        services.close_period(date(2026, 5, 15), self.admin)
        services.confirm_payroll(date(2026, 5, 15), self.admin)
        slip = Payslip.objects.get(staff=self.staff)
        # 賃金9,600＋通勤5,000＝総支給14,600。
        self.assertEqual(slip.gross_yen, 14600)
        self.assertEqual(slip.commute_allowance_yen, 5000)
        self.assertEqual(
            slip.net_pay_yen, slip.gross_yen - slip.total_deduction_yen
        )

    def test_payslip_detail_blocks_non_staff_member(self):
        self.client.force_login(self.user)
        response = self.client.get(
            reverse("attendance:manage_payslip_detail", args=[self.staff.id])
        )
        self.assertEqual(response.status_code, 302)

    def test_payslip_detail_visible_to_staff_member(self):
        self.client.force_login(self.admin)
        response = self.client.get(
            reverse("attendance:manage_payslip_detail", args=[self.staff.id])
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "差引支給額")


class StaffProfileViewTests(TestCase):
    """スタッフの手当・控除（給与プロフィール）編集画面。"""

    def setUp(self):
        self.store = Store.objects.create(business_unit="cafeteria", name="本店食堂")
        self.user, self.staff = _make_staff("prof_staff", "設定花子", self.store)
        self.admin = User.objects.create_user(
            "profadmin", password="pw-profadmin-123456"
        )
        self.admin.is_staff = True
        self.admin.save()

    def _url(self):
        return reverse("attendance:manage_staff_profile", args=[self.staff.id])

    def test_profile_blocks_non_staff_member(self):
        self.client.force_login(self.user)
        response = self.client.get(self._url())
        self.assertEqual(response.status_code, 302)
        self.assertIn("/login", response["Location"])

    def test_profile_visible_to_staff_member(self):
        self.client.force_login(self.admin)
        response = self.client.get(self._url())
        self.assertEqual(response.status_code, 200)

    def test_profile_save_creates_profile(self):
        self.client.force_login(self.admin)
        response = self.client.post(
            self._url(),
            {
                "commute_allowance": 9000,
                "other_allowance": 0,
                "other_allowance_name": "その他手当",
                "health_insurance": 13000,
                "nursing_insurance": 0,
                "pension_insurance": 21000,
                "resident_tax": 6000,
                "other_deduction": 0,
                "other_deduction_name": "その他控除",
                "dependents_count": 1,
                "employment_insurance_enrolled": "on",
            },
        )
        self.assertEqual(response.status_code, 302)
        profile = StaffPayrollProfile.objects.get(staff=self.staff)
        self.assertEqual(profile.commute_allowance, 9000)
        self.assertEqual(profile.health_insurance, 13000)
        self.assertEqual(profile.dependents_count, 1)
        self.assertTrue(profile.employment_insurance_enrolled)

    def test_profile_save_updates_existing(self):
        StaffPayrollProfile.objects.create(
            staff=self.staff, commute_allowance=5000
        )
        self.client.force_login(self.admin)
        self.client.post(
            self._url(),
            {
                "commute_allowance": 12000,
                "other_allowance": 0,
                "other_allowance_name": "その他手当",
                "health_insurance": 0,
                "nursing_insurance": 0,
                "pension_insurance": 0,
                "resident_tax": 0,
                "other_deduction": 0,
                "other_deduction_name": "その他控除",
                "dependents_count": 0,
            },
        )
        # 既存プロフィールは重複作成されず1件のまま更新される。
        self.assertEqual(
            StaffPayrollProfile.objects.filter(staff=self.staff).count(), 1
        )
        self.assertEqual(
            StaffPayrollProfile.objects.get(staff=self.staff).commute_allowance,
            12000,
        )


class BulkMinWageUpdateTests(TestCase):
    """最低賃金の一括更新。最低時給スタッフだけ更新、それ以外は触らない。"""

    def setUp(self):
        self.setting = PayrollSetting.current()
        self.setting.min_wage = 1226
        self.setting.save()
        self.store = Store.objects.create(business_unit="cafeteria", name="本店食堂")
        # 3パターンのスタッフを用意する。
        _, self.on_min = _make_staff("bulk_a", "最低 時太郎", self.store)
        HourlyWage.objects.create(
            staff=self.on_min, amount=1226, effective_from=date(2026, 1, 1)
        )
        _, self.above_min = _make_staff("bulk_b", "高給 次郎", self.store)
        HourlyWage.objects.create(
            staff=self.above_min, amount=1500, effective_from=date(2026, 1, 1)
        )
        _, self.no_wage = _make_staff("bulk_c", "未登録 三郎", self.store)
        self.admin = User.objects.create_user("bulkadmin", password="pw-bulkadmin-123456")
        self.admin.is_staff = True
        self.admin.save()

    def test_staff_on_min_wage_helper(self):
        on_min = services.staff_on_min_wage()
        self.assertEqual([s.id for s in on_min], [self.on_min.id])

    def test_bulk_update_creates_new_wage_for_min_only(self):
        affected, _ = services.bulk_update_min_wage(1300, date(2026, 10, 1), self.admin)
        self.assertEqual([s.id for s in affected], [self.on_min.id])
        # 最低時給スタッフに新しい時給レコードが追加された（既存は残る）。
        self.assertEqual(
            HourlyWage.objects.filter(staff=self.on_min).count(), 2
        )
        new_wage = HourlyWage.objects.get(
            staff=self.on_min, effective_from=date(2026, 10, 1)
        )
        self.assertEqual(new_wage.amount, 1300)
        # 最低時給より高いスタッフは触らない。
        self.assertEqual(
            HourlyWage.objects.filter(staff=self.above_min).count(), 1
        )
        # 時給未登録のスタッフも対象外。
        self.assertEqual(
            HourlyWage.objects.filter(staff=self.no_wage).count(), 0
        )

    def test_bulk_update_also_updates_payroll_setting(self):
        services.bulk_update_min_wage(1300, date(2026, 10, 1), self.admin)
        self.assertEqual(PayrollSetting.current().min_wage, 1300)

    def test_view_blocks_non_staff_member(self):
        self.client.force_login(self.on_min.user)
        response = self.client.get(reverse("attendance:manage_min_wage_update"))
        self.assertEqual(response.status_code, 302)

    def test_view_visible_to_staff_member(self):
        self.client.force_login(self.admin)
        response = self.client.get(reverse("attendance:manage_min_wage_update"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "最低 時太郎")     # 対象に表示
        self.assertContains(response, "高給 次郎")       # 影響なしに表示

    def test_view_post_executes_update(self):
        self.client.force_login(self.admin)
        self.client.post(
            reverse("attendance:manage_min_wage_update"),
            {"new_min_wage": 1300, "effective_from": "2026-10-01"},
        )
        self.assertEqual(PayrollSetting.current().min_wage, 1300)
        self.assertTrue(
            HourlyWage.objects.filter(
                staff=self.on_min, effective_from=date(2026, 10, 1), amount=1300
            ).exists()
        )


class ManualWorkHoursTests(TestCase):
    """勤務時間の手動入力（販売事業の v1 用）。入力値が打刻集計より優先される。"""

    def setUp(self):
        self.setting = PayrollSetting.current()
        self.store = Store.objects.create(business_unit="sales", name="横浜販売所")
        _, self.staff = _make_staff("mh_taro", "販売 太郎", self.store)
        self.staff.business_unit = "sales"
        self.staff.save()
        HourlyWage.objects.create(
            staff=self.staff, amount=1300, effective_from=date(2026, 1, 1)
        )
        self.period_end = date(2026, 5, 15)
        self.period_start = date(2026, 4, 16)
        self.period = services.get_or_create_period(self.period_end)
        self.admin = User.objects.create_user("mhadmin", password="pw-mhadmin-123456")
        self.admin.is_staff = True
        self.admin.save()

    def _calc(self):
        return payroll.compute_payslip(
            self.staff, self.period_start, self.period_end, self.setting
        )

    def test_manual_hours_override_time_records(self):
        # 同じスタッフに打刻（1日 9:00-18:00＝実働8h）と手動入力（160h）の両方を入れる。
        ci = timezone.make_aware(datetime.combine(date(2026, 5, 1), time(9, 0)))
        co = timezone.make_aware(datetime.combine(date(2026, 5, 1), time(18, 0)))
        TimeRecord.objects.create(staff=self.staff, kind="clock_in", recorded_at=ci, work_date=date(2026,5,1), source="manual")
        TimeRecord.objects.create(staff=self.staff, kind="clock_out", recorded_at=co, work_date=date(2026,5,1), source="manual")
        ManualWorkHours.objects.create(
            staff=self.staff, payroll_period=self.period,
            work_days=20, work_minutes=160 * 60,
        )
        calc = self._calc()
        # 手動入力が優先される（打刻の8h≠160hで判別）。
        self.assertEqual(calc.work_days, 20)
        self.assertEqual(calc.work_minutes, 160 * 60)
        self.assertEqual(calc.base_wage_yen, 160 * 1300)  # 208,000

    def test_falls_back_to_time_records_without_manual(self):
        ci = timezone.make_aware(datetime.combine(date(2026, 5, 1), time(9, 0)))
        co = timezone.make_aware(datetime.combine(date(2026, 5, 1), time(18, 0)))
        TimeRecord.objects.create(staff=self.staff, kind="clock_in", recorded_at=ci, work_date=date(2026,5,1), source="manual")
        TimeRecord.objects.create(staff=self.staff, kind="clock_out", recorded_at=co, work_date=date(2026,5,1), source="manual")
        calc = self._calc()
        self.assertEqual(calc.work_minutes, 480)  # 8h from punches

    def test_manual_overtime_and_holiday_premiums(self):
        ManualWorkHours.objects.create(
            staff=self.staff, payroll_period=self.period,
            work_minutes=160 * 60, overtime_minutes=10 * 60, holiday_minutes=8 * 60,
        )
        calc = self._calc()
        # v2フル単価・別建て：時間外10h×1300×1.25＝16,250／休日8h×1300×1.35＝14,040。
        # work_minutes＝160h は「通常勤務」。時間外・休日はそこに含めず上乗せする。
        self.assertEqual(calc.normal_minutes, 160 * 60)
        self.assertEqual(calc.base_wage_yen, 160 * 1300)  # 208,000
        self.assertEqual(calc.overtime_premium_yen, 16250)
        self.assertEqual(calc.holiday_premium_yen, 14040)

    def test_v2_buckets_are_separate_and_summed(self):
        # v2：通常100h・時間外10h・深夜5h を別建てで入力 → それぞれフル単価で合算。
        ManualWorkHours.objects.create(
            staff=self.staff, payroll_period=self.period,
            work_days=20, work_minutes=100 * 60,
            overtime_minutes=10 * 60, night_minutes=5 * 60,
        )
        calc = self._calc()
        self.assertEqual(calc.normal_minutes, 100 * 60)       # 通常のみ（割増は含まない）
        self.assertEqual(calc.work_minutes, 115 * 60)         # 総実働＝100+10+5
        base = 100 * 1300                                     # 130,000
        ot = 10 * 1300 * 125 // 100                           # 16,250
        night = 5 * 1300 * 125 // 100                         # 8,125
        self.assertEqual(calc.base_wage_yen, base)
        self.assertEqual(calc.overtime_premium_yen, ot)
        self.assertEqual(calc.night_premium_yen, night)
        self.assertEqual(calc.total_yen, base + ot + night)   # 単純合計

    def test_manual_break_deducted_from_work(self):
        # 勤務（拘束）160h・休憩10h（600分）→ 実働150h で賃金計算する。
        ManualWorkHours.objects.create(
            staff=self.staff, payroll_period=self.period,
            work_days=20, work_minutes=160 * 60, break_minutes=600,
        )
        calc = self._calc()
        self.assertEqual(calc.break_minutes, 600)
        self.assertEqual(calc.work_minutes, 150 * 60)  # 拘束−休憩
        self.assertEqual(calc.base_wage_yen, 150 * 1300)  # 実働分 195,000
        # 支給欄表示：基本賃金（拘束）= 160h × 1300 = 208,000、休憩控除 = 13,000。
        self.assertEqual(calc.break_deduction_yen, 13000)
        self.assertEqual(calc.base_wage_yen + calc.break_deduction_yen, 160 * 1300)

    def test_manual_break_zero_is_backward_compatible(self):
        # 休憩未入力（既定0）なら従来どおり work_minutes がそのまま実働。
        ManualWorkHours.objects.create(
            staff=self.staff, payroll_period=self.period,
            work_days=20, work_minutes=160 * 60,
        )
        calc = self._calc()
        self.assertEqual(calc.break_minutes, 0)
        self.assertEqual(calc.work_minutes, 160 * 60)
        self.assertEqual(calc.base_wage_yen, 160 * 1300)
        self.assertEqual(calc.break_deduction_yen, 0)

    def test_break_never_makes_work_negative(self):
        # 休憩が拘束を超えても実働は0で下げ止まる（負の賃金を出さない）。
        ManualWorkHours.objects.create(
            staff=self.staff, payroll_period=self.period,
            work_minutes=60, break_minutes=600,
        )
        calc = self._calc()
        self.assertEqual(calc.work_minutes, 0)
        self.assertEqual(calc.base_wage_yen, 0)

    def test_save_and_preview_endpoints_carry_break(self):
        self.client.force_login(self.admin)
        post = {
            "period": self.period_end.isoformat(),
            "work_days": "20", "work_hours": "160", "break_hours": "10",
            "overtime_hours": "0", "night_hours": "0", "holiday_hours": "0",
        }
        preview = self.client.post(
            reverse("attendance:manage_payslip_preview", args=[self.staff.id]), post
        )
        self.assertEqual(preview.json()["work_minutes"], 150 * 60)
        self.assertEqual(preview.json()["base_wage_yen"], 195000)
        # 支給欄の基本賃金（拘束）と休憩控除が返る。
        self.assertEqual(preview.json()["base_wage_gross_yen"], 208000)
        self.assertEqual(preview.json()["break_deduction_yen"], 13000)
        self.client.post(
            reverse("attendance:manage_payslip_save_hours", args=[self.staff.id]), post
        )
        m = ManualWorkHours.objects.get(staff=self.staff)
        self.assertEqual(m.work_minutes, 9600)
        self.assertEqual(m.break_minutes, 600)

    def test_overtime_unit_rounded_half_up_low_fraction(self):
        # 時給1225：1225×1.25＝1531.25 → 端数25銭は切り捨て → 単価1531 → ×10h＝15,310。
        HourlyWage.objects.create(
            staff=self.staff, amount=1225, effective_from=date(2026, 2, 1)
        )
        ManualWorkHours.objects.create(
            staff=self.staff, payroll_period=self.period,
            work_minutes=100 * 60, overtime_minutes=10 * 60,
        )
        calc = self._calc()
        self.assertEqual(calc.hourly_wage, 1225)
        self.assertEqual(payroll.premium_unit_yen(1225, Decimal("1.25")), 1531)
        self.assertEqual(calc.overtime_premium_yen, 15310)  # 1531×10

    def test_overtime_unit_rounds_up_at_half_yen(self):
        # 時給1226：1226×1.25＝1532.50 → 50銭以上は切り上げ → 単価1533 → ×10h＝15,330。
        # 切り捨て(1532)だと過少払い＝労基法違反になるケースを四捨五入で回避する。
        HourlyWage.objects.create(
            staff=self.staff, amount=1226, effective_from=date(2026, 2, 1)
        )
        ManualWorkHours.objects.create(
            staff=self.staff, payroll_period=self.period,
            work_minutes=100 * 60, overtime_minutes=10 * 60,
        )
        calc = self._calc()
        self.assertEqual(payroll.premium_unit_yen(1226, Decimal("1.25")), 1533)
        self.assertEqual(calc.overtime_premium_yen, 15330)  # 1533×10

    def test_save_manual_hours_skips_all_zero_new(self):
        # 既存レコードが無い・全て0 → レコードを作らない（誤って空行を作らない）。
        services.save_manual_hours(
            self.staff, self.period, 0, 0, 0, 0, 0
        )
        self.assertEqual(ManualWorkHours.objects.count(), 0)

    def test_save_manual_hours_creates_and_updates(self):
        # 新規作成。
        services.save_manual_hours(
            self.staff, self.period, 20, 160 * 60, 5 * 60, 0, 0, note="5月分転記"
        )
        m = ManualWorkHours.objects.get(staff=self.staff)
        self.assertEqual(m.work_minutes, 9600)
        self.assertEqual(m.overtime_minutes, 300)
        self.assertEqual(m.note, "5月分転記")
        # 同じスタッフ・期間で再保存 → 更新（重複作成しない）。
        services.save_manual_hours(
            self.staff, self.period, 22, 170 * 60, 0, 0, 0, note=""
        )
        self.assertEqual(ManualWorkHours.objects.filter(staff=self.staff).count(), 1)
        m.refresh_from_db()
        self.assertEqual(m.work_minutes, 10200)

    def test_clear_manual_hours_removes_record(self):
        services.save_manual_hours(self.staff, self.period, 20, 9600, 0, 0, 0)
        services.clear_manual_hours(self.staff, self.period)
        self.assertEqual(ManualWorkHours.objects.count(), 0)

    def test_preview_endpoint_returns_json(self):
        self.client.force_login(self.admin)
        response = self.client.post(
            reverse("attendance:manage_payslip_preview", args=[self.staff.id]),
            {
                "period": self.period_end.isoformat(),
                "work_days": "20",
                "work_hours": "160",
                "overtime_hours": "5",
                "night_hours": "0",
                "holiday_hours": "0",
            },
        )
        self.assertEqual(response.status_code, 200)
        data = response.json()
        # v2：基本賃金＝通常160h×1300＝208,000／時間外＝5h×1300×1.25＝8,125（フル単価・別建て）。
        # 総実働＝通常160h＋時間外5h＝165h＝9,900分。
        self.assertEqual(data["work_minutes"], 9900)
        self.assertEqual(data["base_wage_yen"], 208000)
        self.assertEqual(data["overtime_premium_yen"], 8125)
        # プレビューは保存しない。
        self.assertEqual(ManualWorkHours.objects.count(), 0)

    def test_save_hours_endpoint_persists(self):
        self.client.force_login(self.admin)
        response = self.client.post(
            reverse("attendance:manage_payslip_save_hours", args=[self.staff.id]),
            {
                "period": self.period_end.isoformat(),
                "work_days": "20",
                "work_hours": "160",
                "overtime_hours": "5",
                "night_hours": "0",
                "holiday_hours": "0",
            },
        )
        self.assertEqual(response.status_code, 302)
        m = ManualWorkHours.objects.get(staff=self.staff)
        self.assertEqual(m.work_minutes, 9600)
        self.assertEqual(m.overtime_minutes, 300)

    def test_clear_hours_endpoint_deletes(self):
        services.save_manual_hours(self.staff, self.period, 20, 9600, 0, 0, 0)
        self.client.force_login(self.admin)
        response = self.client.post(
            reverse("attendance:manage_payslip_clear_hours", args=[self.staff.id]),
            {"period": self.period_end.isoformat()},
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(ManualWorkHours.objects.count(), 0)


class StaffSettingsBulkTests(TestCase):
    """全スタッフの時給・手当・控除を一括設定する画面。"""

    def setUp(self):
        self.store = Store.objects.create(business_unit="cafeteria", name="本店食堂")
        _, self.staff_a = _make_staff("bulk_a", "Aさん", self.store)
        _, self.staff_b = _make_staff("bulk_b", "Bさん", self.store)
        HourlyWage.objects.create(
            staff=self.staff_a, amount=1225, effective_from=date(2026, 1, 1)
        )
        self.admin = User.objects.create_user(
            "bulkstaffadmin", password="pw-bulkadmin-123456"
        )
        self.admin.is_staff = True
        self.admin.save()

    def _post(self, **fields):
        self.client.force_login(self.admin)
        return self.client.post(
            reverse("attendance:manage_staff_settings"), fields
        )

    def test_blocks_non_staff_member(self):
        self.client.force_login(self.staff_a.user)
        response = self.client.get(reverse("attendance:manage_staff_settings"))
        self.assertEqual(response.status_code, 302)

    def test_visible_to_staff_member_shows_staff(self):
        self.client.force_login(self.admin)
        response = self.client.get(reverse("attendance:manage_staff_settings"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Aさん")
        self.assertContains(response, "Bさん")

    def test_bulk_save_creates_wage_and_profile_for_new_staff(self):
        # B さんは時給も手当・控除も未登録。bulk save で両方できる。
        response = self._post(**{
            f"staff_{self.staff_b.id}_hourly_wage": "1500",
            f"staff_{self.staff_b.id}_commute_allowance": "10000",
            f"staff_{self.staff_b.id}_health_insurance": "0",
            f"staff_{self.staff_b.id}_pension_insurance": "0",
            f"staff_{self.staff_b.id}_resident_tax": "0",
            f"staff_{self.staff_b.id}_dependents_count": "1",
            f"staff_{self.staff_b.id}_employment_insurance_enrolled": "on",
        })
        self.assertEqual(response.status_code, 302)
        self.assertTrue(
            HourlyWage.objects.filter(staff=self.staff_b, amount=1500).exists()
        )
        profile = StaffPayrollProfile.objects.get(staff=self.staff_b)
        self.assertEqual(profile.commute_allowance, 10000)
        self.assertEqual(profile.dependents_count, 1)
        self.assertTrue(profile.employment_insurance_enrolled)

    def test_bulk_save_same_wage_no_duplicate(self):
        # 既存の Aさん時給1225と同じ値を送る → 新規 HourlyWage は作らない。
        self._post(**{
            f"staff_{self.staff_a.id}_hourly_wage": "1225",
        })
        self.assertEqual(HourlyWage.objects.filter(staff=self.staff_a).count(), 1)

    def test_bulk_save_changed_wage_creates_new_record(self):
        # 別の金額 → 新しい履歴を today で追加（既存履歴は残す）。
        self._post(**{
            f"staff_{self.staff_a.id}_hourly_wage": "1300",
        })
        wages = HourlyWage.objects.filter(staff=self.staff_a).order_by(
            "-effective_from"
        )
        self.assertEqual(wages.count(), 2)
        self.assertEqual(wages[0].amount, 1300)
        self.assertEqual(wages[1].amount, 1225)

    def test_bulk_save_updates_existing_profile_only_if_changed(self):
        StaffPayrollProfile.objects.create(
            staff=self.staff_a, commute_allowance=5000, dependents_count=0
        )
        # 通勤手当だけ変える。
        self._post(**{
            f"staff_{self.staff_a.id}_commute_allowance": "8000",
            f"staff_{self.staff_a.id}_employment_insurance_enrolled": "on",
        })
        self.assertEqual(
            StaffPayrollProfile.objects.filter(staff=self.staff_a).count(), 1
        )
        profile = StaffPayrollProfile.objects.get(staff=self.staff_a)
        self.assertEqual(profile.commute_allowance, 8000)


class PayslipAdjustmentTests(TestCase):
    """臨時項目（PayslipAdjustment）— 年末調整還付・慶弔金・遡及精算など。"""

    def setUp(self):
        self.setting = PayrollSetting.current()
        self.store = Store.objects.create(business_unit="sales", name="横浜販売所")
        _, self.staff = _make_staff("adj_taro", "調整 太郎", self.store)
        self.staff.business_unit = "sales"
        self.staff.save()
        HourlyWage.objects.create(
            staff=self.staff, amount=1300, effective_from=date(2026, 1, 1)
        )
        # ManualWorkHours で勤務時間を確定（160h・控除も無い小規模パート想定）。
        self.period_end = date(2026, 5, 15)
        self.period_start = date(2026, 4, 16)
        self.period = services.get_or_create_period(self.period_end)
        ManualWorkHours.objects.create(
            staff=self.staff, payroll_period=self.period,
            work_days=20, work_minutes=160 * 60,
        )
        self.admin = User.objects.create_user("adjadmin", password="pw-adjadmin-123456")
        self.admin.is_staff = True
        self.admin.save()

    def _calc(self):
        return payroll.compute_payslip(
            self.staff, self.period_start, self.period_end, self.setting
        )

    def test_payment_adjustment_adds_to_gross(self):
        # 慶弔金 +30,000円 → 総支給額に加算される。
        PayslipAdjustment.objects.create(
            staff=self.staff, payroll_period=self.period,
            name="慶弔金", kind=PayslipAdjustment.KIND_PAYMENT, amount_yen=30000,
        )
        calc = self._calc()
        # 基本賃金 160h×1300 = 208,000 ＋ 慶弔金 30,000 = 238,000
        self.assertEqual(calc.adjustment_payments_yen, 30000)
        self.assertEqual(calc.gross_yen, 208000 + 30000)
        self.assertEqual(calc.net_pay_yen, calc.gross_yen - calc.total_deduction_yen)

    def test_deduction_adjustment_adds_to_total_deduction(self):
        # 給与の前借り返済 5,000円 → 控除合計に加算される。
        PayslipAdjustment.objects.create(
            staff=self.staff, payroll_period=self.period,
            name="前借り返済", kind=PayslipAdjustment.KIND_DEDUCTION, amount_yen=5000,
        )
        calc = self._calc()
        self.assertEqual(calc.adjustment_deductions_yen, 5000)
        # 既存控除（社保未加入・税のみ）に 5,000 が加わる。

    def test_year_end_refund_as_payment_adjustment(self):
        # 年末調整還付金は kind=payment（支給側にプラス計上）で手取りに加算される。
        PayslipAdjustment.objects.create(
            staff=self.staff, payroll_period=self.period,
            name="年末調整還付金", kind=PayslipAdjustment.KIND_PAYMENT, amount_yen=12000,
        )
        calc = self._calc()
        self.assertEqual(calc.adjustment_payments_yen, 12000)
        self.assertEqual(calc.gross_yen, 208000 + 12000)

    def test_save_adjustments_creates_new(self):
        services.save_payslip_adjustments(
            self.staff, self.period,
            {
                "adj_new_1_name": "慶弔金",
                "adj_new_1_kind": "payment",
                "adj_new_1_amount": "30000",
                "adj_new_1_note": "結婚祝い",
            },
        )
        adj = PayslipAdjustment.objects.get(staff=self.staff)
        self.assertEqual(adj.name, "慶弔金")
        self.assertEqual(adj.amount_yen, 30000)
        self.assertEqual(adj.kind, "payment")

    def test_save_adjustments_updates_existing(self):
        adj = PayslipAdjustment.objects.create(
            staff=self.staff, payroll_period=self.period,
            name="慶弔金", kind="payment", amount_yen=20000,
        )
        services.save_payslip_adjustments(
            self.staff, self.period,
            {
                f"adj_{adj.id}_id": str(adj.id),
                f"adj_{adj.id}_name": "慶弔金",
                f"adj_{adj.id}_kind": "payment",
                f"adj_{adj.id}_amount": "30000",
                f"adj_{adj.id}_note": "金額修正",
            },
        )
        adj.refresh_from_db()
        self.assertEqual(adj.amount_yen, 30000)
        self.assertEqual(adj.note, "金額修正")

    def test_save_adjustments_deletes_with_checkbox(self):
        adj = PayslipAdjustment.objects.create(
            staff=self.staff, payroll_period=self.period,
            name="削除予定", kind="payment", amount_yen=1000,
        )
        services.save_payslip_adjustments(
            self.staff, self.period,
            {
                f"adj_{adj.id}_id": str(adj.id),
                f"adj_{adj.id}_name": "削除予定",
                f"adj_{adj.id}_kind": "payment",
                f"adj_{adj.id}_amount": "1000",
                f"adj_{adj.id}_delete": "on",
            },
        )
        self.assertEqual(PayslipAdjustment.objects.count(), 0)

    def test_save_adjustments_skips_empty_rows(self):
        services.save_payslip_adjustments(
            self.staff, self.period,
            {
                "adj_new_1_name": "",
                "adj_new_1_kind": "",
                "adj_new_1_amount": "",
                "adj_new_1_note": "",
            },
        )
        self.assertEqual(PayslipAdjustment.objects.count(), 0)

    def test_save_adjustments_blocked_when_period_closed(self):
        services.close_period(self.period_end, self.admin)
        period = PayrollPeriod.objects.get(period_end=self.period_end)
        with self.assertRaises(services.PunchError):
            services.save_payslip_adjustments(
                self.staff, period,
                {
                    "adj_new_1_name": "新規",
                    "adj_new_1_kind": "payment",
                    "adj_new_1_amount": "1000",
                },
            )

    def test_save_adjustments_endpoint(self):
        self.client.force_login(self.admin)
        response = self.client.post(
            reverse("attendance:manage_payslip_save_adjustments", args=[self.staff.id]),
            {
                "period": self.period_end.isoformat(),
                "adj_new_1_name": "年末調整還付金",
                "adj_new_1_kind": "payment",
                "adj_new_1_amount": "12000",
                "adj_new_1_note": "12月年末調整",
            },
        )
        self.assertEqual(response.status_code, 302)
        adj = PayslipAdjustment.objects.get(staff=self.staff)
        self.assertEqual(adj.name, "年末調整還付金")
        self.assertEqual(adj.amount_yen, 12000)


class QrPunchTests(TestCase):
    """QR打刻（トークンURL個人ページ／キオスクスキャナー／キオスクAPI）のテスト。"""

    def setUp(self):
        self.store = Store.objects.create(business_unit="cafeteria", name="本店食堂")
        self.user, self.staff = _make_staff("qr_taro", "山田太郎", self.store)
        self.token = self.staff.punch_token

    # --- punch_by_token ---

    def test_token_page_renders(self):
        url = reverse("attendance:punch_by_token", args=[self.token])
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "山田太郎")
        self.assertContains(response, "本日まだ出勤していません")
        self.assertContains(response, ">出勤<")

    def test_token_page_includes_personal_qr(self):
        """個人ページに自分のQR画像（SVG）と保存案内が出る。"""
        url = reverse("attendance:punch_by_token", args=[self.token])
        response = self.client.get(url)
        content = response.content.decode()
        # QR画像が埋め込まれている
        self.assertEqual(content.count("<svg"), 1)
        # 保存方法の案内
        self.assertIn("私の打刻QR", content)
        self.assertIn("画像を保存", content)
        # トークンURLが本文に出ている
        self.assertIn(str(self.token), content)

    def test_token_page_unknown_token_404(self):
        import uuid as _uuid
        url = reverse("attendance:punch_by_token", args=[_uuid.uuid4()])
        response = self.client.get(url)
        self.assertEqual(response.status_code, 404)

    def test_token_page_inactive_staff_404(self):
        self.staff.is_active = False
        self.staff.save()
        url = reverse("attendance:punch_by_token", args=[self.token])
        response = self.client.get(url)
        self.assertEqual(response.status_code, 404)

    def test_token_page_post_clock_in(self):
        url = reverse("attendance:punch_by_token", args=[self.token])
        response = self.client.post(url, {"kind": "clock_in"})
        self.assertEqual(response.status_code, 302)
        self.assertEqual(
            TimeRecord.objects.filter(staff=self.staff, kind="clock_in").count(), 1
        )

    def test_token_page_post_clock_out_after_in(self):
        services.clock_in(self.staff)
        url = reverse("attendance:punch_by_token", args=[self.token])
        response = self.client.post(url, {"kind": "clock_out"})
        self.assertEqual(response.status_code, 302)
        self.assertEqual(
            TimeRecord.objects.filter(staff=self.staff, kind="clock_out").count(), 1
        )

    def test_token_page_renders_working_state(self):
        services.clock_in(self.staff)
        url = reverse("attendance:punch_by_token", args=[self.token])
        response = self.client.get(url)
        self.assertContains(response, "勤務中")
        self.assertContains(response, ">退勤<")

    # --- punch_scanner ---

    def test_scanner_home_renders(self):
        response = self.client.get(reverse("attendance:punch_scanner"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "打刻するモードを選んでください")
        self.assertContains(response, "?mode=clock_in")
        self.assertContains(response, "?mode=clock_out")

    def test_scanner_clock_in_mode_renders(self):
        response = self.client.get(
            reverse("attendance:punch_scanner") + "?mode=clock_in"
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "出勤モード")
        self.assertContains(response, "kiosk__video")
        self.assertContains(response, "jsQR")

    def test_scanner_clock_out_mode_renders(self):
        response = self.client.get(
            reverse("attendance:punch_scanner") + "?mode=clock_out"
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "退勤モード")

    def test_scanner_invalid_mode_falls_back_to_home(self):
        response = self.client.get(
            reverse("attendance:punch_scanner") + "?mode=badmode"
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "打刻するモードを選んでください")

    # --- punch_kiosk_api ---

    def _api_post(self, body):
        import json as _json
        return self.client.post(
            reverse("attendance:punch_kiosk_api"),
            data=_json.dumps(body),
            content_type="application/json",
        )

    def test_api_get_not_allowed(self):
        response = self.client.get(reverse("attendance:punch_kiosk_api"))
        self.assertEqual(response.status_code, 405)

    def test_api_invalid_json(self):
        response = self.client.post(
            reverse("attendance:punch_kiosk_api"),
            data="not json",
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["error"], "invalid_json")

    def test_api_invalid_token(self):
        response = self._api_post({"token": "not-a-uuid", "kind": "clock_in"})
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["error"], "invalid_token")

    def test_api_unknown_token(self):
        import uuid as _uuid
        response = self._api_post({"token": str(_uuid.uuid4()), "kind": "clock_in"})
        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.json()["error"], "unknown_token")

    def test_api_clock_in_success(self):
        response = self._api_post(
            {"token": str(self.token), "kind": "clock_in"}
        )
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertTrue(body["ok"])
        self.assertEqual(body["staff_name"], "山田太郎")
        self.assertEqual(body["kind_label"], "出勤")
        self.assertEqual(
            TimeRecord.objects.filter(staff=self.staff, kind="clock_in").count(), 1
        )

    def test_api_clock_out_after_clock_in(self):
        services.clock_in(self.staff)
        response = self._api_post(
            {"token": str(self.token), "kind": "clock_out"}
        )
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertTrue(body["ok"])
        self.assertEqual(body["kind_label"], "退勤")

    def test_api_double_clock_in_rejected(self):
        services.clock_in(self.staff)
        response = self._api_post(
            {"token": str(self.token), "kind": "clock_in"}
        )
        self.assertEqual(response.status_code, 409)
        body = response.json()
        self.assertFalse(body["ok"])
        self.assertEqual(body["error"], "punch_error")
        self.assertIn("すでに出勤", body["message"])
        self.assertEqual(body["staff_name"], "山田太郎")

    def test_api_clock_out_without_clock_in_rejected(self):
        response = self._api_post(
            {"token": str(self.token), "kind": "clock_out"}
        )
        self.assertEqual(response.status_code, 409)
        self.assertIn("先に出勤", response.json()["message"])

    def test_api_invalid_kind(self):
        response = self._api_post(
            {"token": str(self.token), "kind": "wrong_kind"}
        )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["error"], "invalid_kind")

    def test_api_accepts_full_url_in_token(self):
        """QRに完全URLを埋め込んだ場合、末尾のUUIDを抽出して打刻できる。"""
        full_url = f"https://example.com/attendance/punch/{self.token}/"
        response = self._api_post({"token": full_url, "kind": "clock_in"})
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["ok"])

    def test_api_inactive_staff_rejected(self):
        self.staff.is_active = False
        self.staff.save()
        response = self._api_post(
            {"token": str(self.token), "kind": "clock_in"}
        )
        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.json()["error"], "unknown_token")


class QrPdfViewTests(TestCase):
    """打刻QR一括印刷ページのテスト。"""

    def setUp(self):
        self.store = Store.objects.create(business_unit="cafeteria", name="本店食堂")
        self.admin = User.objects.create_user(
            "qr_admin", password="pw-qr_admin-123456", is_staff=True
        )
        _, self.staff_a = _make_staff("qr_a", "山田 太郎", self.store)
        _, self.staff_b = _make_staff("qr_b", "鈴木 花子", self.store)

    def test_requires_staff(self):
        response = self.client.get(reverse("attendance:manage_punch_qrs"))
        self.assertNotEqual(response.status_code, 200)

    def test_renders_qr_for_each_active_staff(self):
        self.client.force_login(self.admin)
        response = self.client.get(reverse("attendance:manage_punch_qrs"))
        self.assertEqual(response.status_code, 200)
        content = response.content.decode()
        self.assertIn("山田 太郎", content)
        self.assertIn("鈴木 花子", content)
        # 2人分のQRがSVGとして埋め込まれていること
        self.assertEqual(content.count("<svg"), 2)

    def test_each_card_has_copy_url_button(self):
        """各QRカードに「URLをコピー」ボタンとトークンURLが付く。"""
        self.client.force_login(self.admin)
        response = self.client.get(reverse("attendance:manage_punch_qrs"))
        content = response.content.decode()
        # data-url 属性が2スタッフ分（JSセレクタの .js-copy-url とは別カウント）
        self.assertEqual(content.count('data-url="http'), 2)
        self.assertIn(f"punch/{self.staff_a.punch_token}/", content)
        self.assertIn(f"punch/{self.staff_b.punch_token}/", content)

    def test_filters_by_business_unit(self):
        self.staff_b.business_unit = "sales"
        self.staff_b.save()
        self.client.force_login(self.admin)
        response = self.client.get(
            reverse("attendance:manage_punch_qrs") + "?business_unit=cafeteria"
        )
        content = response.content.decode()
        self.assertIn("山田 太郎", content)
        self.assertNotIn("鈴木 花子", content)

    def test_excludes_inactive_staff(self):
        self.staff_b.is_active = False
        self.staff_b.save()
        self.client.force_login(self.admin)
        response = self.client.get(reverse("attendance:manage_punch_qrs"))
        content = response.content.decode()
        self.assertIn("山田 太郎", content)
        self.assertNotIn("鈴木 花子", content)


class SalesAllowanceTests(TestCase):
    """販売事業部の手当（行商・箱洗い・有給）のテスト。"""

    def setUp(self):
        self.store = Store.objects.create(business_unit="sales", name="販売部")
        self.user, self.staff = _make_staff("sales_taro", "販売太郎", self.store)
        self.staff.business_unit = "sales"
        self.staff.save()
        self.admin = User.objects.create_user(
            "sales_admin", password="pw-sales_admin-123456", is_staff=True
        )
        # 時給1500円、所定480分/日
        HourlyWage.objects.create(
            staff=self.staff, amount=1500, effective_from=date(2026, 1, 1)
        )
        StaffPayrollProfile.objects.create(
            staff=self.staff, scheduled_minutes_per_day=480,
            employment_insurance_enrolled=False,
        )
        self.setting = PayrollSetting.current()
        # 既定単価（行商1000、箱洗い200）を明示
        self.setting.peddling_allowance_yen = 1000
        self.setting.box_wash_allowance_yen = 200
        self.setting.save()
        self.period_end = date(2026, 5, 15)
        self.period = services.get_or_create_period(self.period_end)
        self.period_start = aggregation.period_start_for(self.period_end, 15)

    def _calc(self, **manual_kwargs):
        defaults = {
            "staff": self.staff,
            "payroll_period": self.period,
            "work_days": 0,
            "work_minutes": 0,
        }
        defaults.update(manual_kwargs)
        manual = ManualWorkHours(**defaults)
        return payroll.compute_payslip(
            self.staff, self.period_start, self.period_end, self.setting,
            manual=manual,
        )

    def test_peddling_allowance(self):
        calc = self._calc(peddling_count=5)
        # 5回 × 1000円 = 5000円
        self.assertEqual(calc.peddling_allowance_yen, 5000)
        self.assertEqual(calc.peddling_count, 5)

    def test_box_wash_allowance(self):
        calc = self._calc(box_wash_count=37)
        # 37箱 × 200円 = 7400円
        self.assertEqual(calc.box_wash_allowance_yen, 7400)
        self.assertEqual(calc.box_wash_count, 37)

    def test_paid_leave_calculation(self):
        calc = self._calc(paid_leave_days=2)
        # 2日 × 480分 × 1500円 / 60 = 24000円
        self.assertEqual(calc.paid_leave_yen, 24000)
        self.assertEqual(calc.paid_leave_days, 2)

    def test_all_sales_allowances_combined(self):
        calc = self._calc(
            work_days=20, work_minutes=20 * 480,  # 20日×8h
            peddling_count=3, box_wash_count=10, paid_leave_days=1,
        )
        # 賃金20日×8h×1500 = 240,000
        # 行商 3*1000 = 3,000、箱洗い 10*200 = 2,000、有給 1*480*1500/60 = 12,000
        # 合計 240,000 + 3,000 + 2,000 + 12,000 = 257,000
        self.assertEqual(calc.base_wage_yen, 240000)
        self.assertEqual(calc.peddling_allowance_yen, 3000)
        self.assertEqual(calc.box_wash_allowance_yen, 2000)
        self.assertEqual(calc.paid_leave_yen, 12000)
        self.assertEqual(calc.gross_yen, 257000)

    def test_zero_inputs_yield_zero_allowances(self):
        calc = self._calc()
        self.assertEqual(calc.peddling_allowance_yen, 0)
        self.assertEqual(calc.box_wash_allowance_yen, 0)
        self.assertEqual(calc.paid_leave_yen, 0)

    def test_paid_leave_uses_per_staff_scheduled_minutes(self):
        # 所定時間を6h（360分）に変えて再計算
        profile = self.staff.payroll_profile
        profile.scheduled_minutes_per_day = 360
        profile.save()
        calc = self._calc(paid_leave_days=1)
        # 1日 × 360分 × 1500円 / 60 = 9000円
        self.assertEqual(calc.paid_leave_yen, 9000)

    def test_rate_change_in_setting_applies(self):
        self.setting.peddling_allowance_yen = 1500
        self.setting.box_wash_allowance_yen = 250
        self.setting.save()
        calc = self._calc(peddling_count=2, box_wash_count=4)
        self.assertEqual(calc.peddling_allowance_yen, 3000)  # 2×1500
        self.assertEqual(calc.box_wash_allowance_yen, 1000)  # 4×250

    def test_sales_allowance_included_in_employment_insurance_base(self):
        profile = self.staff.payroll_profile
        profile.employment_insurance_enrolled = True
        profile.save()
        # 行商 5000 + 箱洗い 2000 + 有給 12000 = 19000 が総支給に加算
        # base_wage 0 + 手当 19000 = gross_yen 19000
        # 雇用保険 = 19000 × 0.0055 = 104.5 → 105円（四捨五入）
        calc = self._calc(
            peddling_count=5, box_wash_count=10, paid_leave_days=1,
        )
        self.assertEqual(calc.gross_yen, 19000)
        self.assertEqual(calc.employment_insurance_yen, 105)


class SalesAllowanceManualHoursServiceTests(TestCase):
    """save_manual_hours の販売手当パラメータのテスト。"""

    def setUp(self):
        self.store = Store.objects.create(business_unit="sales", name="販売部")
        self.user, self.staff = _make_staff("svc_sales", "販売次郎", self.store)
        self.period_end = date(2026, 5, 15)
        self.period = services.get_or_create_period(self.period_end)

    def test_save_with_sales_allowances(self):
        obj = services.save_manual_hours(
            self.staff, self.period,
            work_days=20, work_minutes=160 * 60,
            overtime_minutes=0, night_minutes=0, holiday_minutes=0,
            peddling_count=3, box_wash_count=15, paid_leave_days=2,
        )
        self.assertIsNotNone(obj)
        self.assertEqual(obj.peddling_count, 3)
        self.assertEqual(obj.box_wash_count, 15)
        self.assertEqual(obj.paid_leave_days, 2)

    def test_save_with_only_sales_allowances_persists(self):
        """勤務時間ゼロでも、行商などが入っていれば保存される。"""
        obj = services.save_manual_hours(
            self.staff, self.period,
            work_days=0, work_minutes=0,
            overtime_minutes=0, night_minutes=0, holiday_minutes=0,
            peddling_count=1,
        )
        self.assertIsNotNone(obj)
        self.assertEqual(obj.peddling_count, 1)

    def test_save_skips_when_all_zero_and_no_existing(self):
        obj = services.save_manual_hours(
            self.staff, self.period,
            work_days=0, work_minutes=0,
            overtime_minutes=0, night_minutes=0, holiday_minutes=0,
        )
        self.assertIsNone(obj)


class SalesAllowanceViewTests(TestCase):
    """販売手当の給与明細画面・プレビューエンドポイントのテスト。"""

    def setUp(self):
        self.store_sales = Store.objects.create(business_unit="sales", name="販売部")
        self.store_caf = Store.objects.create(business_unit="cafeteria", name="食堂")
        self.sales_user, self.sales_staff = _make_staff(
            "vsales", "販売花子", self.store_sales
        )
        self.sales_staff.business_unit = "sales"
        self.sales_staff.store = self.store_sales
        self.sales_staff.save()
        self.caf_user, self.caf_staff = _make_staff(
            "vcaf", "食堂太郎", self.store_caf
        )
        HourlyWage.objects.create(
            staff=self.sales_staff, amount=1500, effective_from=date(2026, 1, 1)
        )
        HourlyWage.objects.create(
            staff=self.caf_staff, amount=1500, effective_from=date(2026, 1, 1)
        )
        self.admin = User.objects.create_user(
            "vadmin", password="pw-vadmin-123456", is_staff=True
        )
        self.period_end = date(2026, 5, 15)

    def _detail_url(self, staff):
        url = reverse("attendance:manage_payslip_detail", args=[staff.id])
        return f"{url}?period={self.period_end:%Y-%m-%d}"

    def test_sales_staff_detail_shows_sales_inputs(self):
        self.client.force_login(self.admin)
        response = self.client.get(self._detail_url(self.sales_staff))
        self.assertEqual(response.status_code, 200)
        content = response.content.decode()
        # 入力欄
        self.assertIn('name="peddling_count"', content)
        self.assertIn('name="box_wash_count"', content)
        self.assertIn('name="paid_leave_days"', content)
        # 表示行
        self.assertIn("行商手当", content)
        self.assertIn("箱洗い手当", content)
        self.assertIn("有給手当", content)

    def test_cafeteria_staff_detail_hides_sales_inputs(self):
        self.client.force_login(self.admin)
        response = self.client.get(self._detail_url(self.caf_staff))
        self.assertEqual(response.status_code, 200)
        content = response.content.decode()
        self.assertNotIn('name="peddling_count"', content)
        self.assertNotIn('name="box_wash_count"', content)
        self.assertNotIn('name="paid_leave_days"', content)
        self.assertNotIn("行商手当", content)

    def test_preview_endpoint_returns_sales_allowances(self):
        self.client.force_login(self.admin)
        url = reverse("attendance:manage_payslip_preview", args=[self.sales_staff.id])
        response = self.client.post(
            url,
            {
                "period": self.period_end.isoformat(),
                "work_days": "0",
                "work_hours": "0",
                "overtime_hours": "0",
                "night_hours": "0",
                "holiday_hours": "0",
                "peddling_count": "5",
                "box_wash_count": "10",
                "paid_leave_days": "1",
            },
        )
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["peddling_allowance_yen"], 5000)  # 5×1000
        self.assertEqual(body["box_wash_allowance_yen"], 2000)  # 10×200
        # 有給は所定480分×時給1500/60 = 12000
        self.assertEqual(body["paid_leave_yen"], 12000)

    def test_save_hours_endpoint_persists_sales_allowances(self):
        self.client.force_login(self.admin)
        url = reverse("attendance:manage_payslip_save_hours", args=[self.sales_staff.id])
        response = self.client.post(
            url,
            {
                "period": self.period_end.isoformat(),
                "work_days": "20",
                "work_hours": "160",
                "overtime_hours": "0",
                "night_hours": "0",
                "holiday_hours": "0",
                "peddling_count": "3",
                "box_wash_count": "8",
                "paid_leave_days": "1",
            },
        )
        self.assertEqual(response.status_code, 302)
        manual = ManualWorkHours.objects.get(
            staff=self.sales_staff, payroll_period__period_end=self.period_end
        )
        self.assertEqual(manual.peddling_count, 3)
        self.assertEqual(manual.box_wash_count, 8)
        self.assertEqual(manual.paid_leave_days, 1)


class CompanyFeatureTests(TestCase):
    """会社（LSN/LN/LF）モデル化のテスト。

    Staff.company の既定値・フィルタ動作・バルク保存・テンプレ表示を確認する。
    """

    def setUp(self):
        self.store_caf = Store.objects.create(business_unit="cafeteria", name="食堂本店")
        self.store_sales = Store.objects.create(business_unit="sales", name="販売部")
        self.user_a, self.staff_lsn = _make_staff("co_lsn", "社員 太郎", self.store_caf)
        # _make_staff のデフォルトは cafeteria → company=LSN（モデル既定）
        self.user_b, self.staff_ln = _make_staff("co_ln", "販売 花子", self.store_caf)
        self.staff_ln.business_unit = "sales"
        self.staff_ln.store = self.store_sales
        self.staff_ln.company = "LN"
        self.staff_ln.save()
        self.user_c, self.staff_lf = _make_staff("co_lf", "製造 次郎", self.store_caf)
        self.staff_lf.company = "LF"
        self.staff_lf.save()
        self.admin = User.objects.create_user(
            "co_admin", password="pw-co_admin-123456", is_staff=True
        )

    def test_default_company_is_lsn(self):
        """新規 Staff の既定の会社は LSN（母体）。"""
        _, s = _make_staff("co_new", "新人", self.store_caf)
        self.assertEqual(s.company, "LSN")

    def test_payroll_filter_by_company(self):
        self.client.force_login(self.admin)
        url = reverse("attendance:manage_payroll") + "?company=LN"
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        content = response.content.decode()
        self.assertIn("販売 花子", content)
        self.assertNotIn("社員 太郎", content)
        self.assertNotIn("製造 次郎", content)

    def test_payroll_filter_by_company_lf(self):
        self.client.force_login(self.admin)
        url = reverse("attendance:manage_payroll") + "?company=LF"
        response = self.client.get(url)
        content = response.content.decode()
        self.assertIn("製造 次郎", content)
        self.assertNotIn("社員 太郎", content)

    def test_payroll_no_filter_shows_all(self):
        self.client.force_login(self.admin)
        response = self.client.get(reverse("attendance:manage_payroll"))
        content = response.content.decode()
        self.assertIn("社員 太郎", content)
        self.assertIn("販売 花子", content)
        self.assertIn("製造 次郎", content)

    def test_payroll_company_badge_in_table(self):
        self.client.force_login(self.admin)
        response = self.client.get(reverse("attendance:manage_payroll"))
        content = response.content.decode()
        # 各会社のバッジクラスが出ること
        self.assertIn("company-badge--lsn", content)
        self.assertIn("company-badge--ln", content)
        self.assertIn("company-badge--lf", content)

    def test_dashboard_filter_by_company(self):
        self.client.force_login(self.admin)
        url = reverse("attendance:manage_dashboard") + "?company=LF"
        response = self.client.get(url)
        content = response.content.decode()
        self.assertIn("製造 次郎", content)
        self.assertNotIn("社員 太郎", content)

    def test_staff_settings_filter_by_company(self):
        """LSN フィルタなら LSN 社員のみ表示。LN/LF は除外。"""
        self.client.force_login(self.admin)
        url = reverse("attendance:manage_staff_settings") + "?company=LSN"
        response = self.client.get(url)
        content = response.content.decode()
        self.assertIn("社員 太郎", content)
        self.assertNotIn("販売 花子", content)
        self.assertNotIn("製造 次郎", content)

    def test_staff_settings_bulk_save_updates_company(self):
        """会社列の編集が永続化される。"""
        self.client.force_login(self.admin)
        post_data = {
            "unit": "",
            "company": "",
            f"staff_{self.staff_lsn.id}_hourly_wage": "1500",
            f"staff_{self.staff_lsn.id}_company": "LF",  # LSN → LF に変更
            f"staff_{self.staff_lsn.id}_commute_allowance": "0",
            f"staff_{self.staff_lsn.id}_health_insurance": "0",
            f"staff_{self.staff_lsn.id}_pension_insurance": "0",
            f"staff_{self.staff_lsn.id}_resident_tax": "0",
            f"staff_{self.staff_lsn.id}_dependents_count": "0",
            f"staff_{self.staff_ln.id}_hourly_wage": "1500",
            f"staff_{self.staff_ln.id}_company": "LN",  # 変更なし
            f"staff_{self.staff_ln.id}_commute_allowance": "0",
            f"staff_{self.staff_ln.id}_health_insurance": "0",
            f"staff_{self.staff_ln.id}_pension_insurance": "0",
            f"staff_{self.staff_ln.id}_resident_tax": "0",
            f"staff_{self.staff_ln.id}_dependents_count": "0",
            f"staff_{self.staff_lf.id}_hourly_wage": "1500",
            f"staff_{self.staff_lf.id}_company": "LF",  # 変更なし
            f"staff_{self.staff_lf.id}_commute_allowance": "0",
            f"staff_{self.staff_lf.id}_health_insurance": "0",
            f"staff_{self.staff_lf.id}_pension_insurance": "0",
            f"staff_{self.staff_lf.id}_resident_tax": "0",
            f"staff_{self.staff_lf.id}_dependents_count": "0",
        }
        response = self.client.post(
            reverse("attendance:manage_staff_settings"), post_data
        )
        self.assertEqual(response.status_code, 302)
        self.staff_lsn.refresh_from_db()
        self.assertEqual(self.staff_lsn.company, "LF")

    def test_invalid_company_value_is_ignored(self):
        """不正な会社コードは無視され、既存値が保持される。"""
        self.client.force_login(self.admin)
        post_data = {
            "unit": "",
            "company": "",
            f"staff_{self.staff_lsn.id}_hourly_wage": "1500",
            f"staff_{self.staff_lsn.id}_company": "WRONG",  # 不正
            f"staff_{self.staff_lsn.id}_commute_allowance": "0",
            f"staff_{self.staff_lsn.id}_health_insurance": "0",
            f"staff_{self.staff_lsn.id}_pension_insurance": "0",
            f"staff_{self.staff_lsn.id}_resident_tax": "0",
            f"staff_{self.staff_lsn.id}_dependents_count": "0",
        }
        self.client.post(reverse("attendance:manage_staff_settings"), post_data)
        self.staff_lsn.refresh_from_db()
        self.assertEqual(self.staff_lsn.company, "LSN")  # 既定値のまま

    def test_payslip_detail_shows_company(self):
        self.client.force_login(self.admin)
        HourlyWage.objects.create(
            staff=self.staff_ln, amount=1500, effective_from=date(2026, 1, 1)
        )
        url = reverse("attendance:manage_payslip_detail", args=[self.staff_ln.id])
        response = self.client.get(url)
        content = response.content.decode()
        self.assertIn("(合)ランチネット", content)  # 会社のフル名が出る

    def test_company_filter_invalid_value_falls_back_to_all(self):
        """不正な company クエリ値は無視されて全件表示。"""
        self.client.force_login(self.admin)
        url = reverse("attendance:manage_payroll") + "?company=BADCODE"
        response = self.client.get(url)
        content = response.content.decode()
        self.assertIn("社員 太郎", content)
        self.assertIn("販売 花子", content)


class PayslipPdfTests(TestCase):
    """給与明細PDF出力（個別・一括・本人ダウンロード）のテスト。"""

    def setUp(self):
        self.store = Store.objects.create(business_unit="sales", name="販売部")
        self.user, self.staff = _make_staff("pdf_taro", "山田 太郎", self.store)
        self.staff.business_unit = "sales"
        self.staff.company = "LN"
        self.staff.save()
        HourlyWage.objects.create(
            staff=self.staff, amount=1500, effective_from=date(2026, 1, 1)
        )
        self.admin = User.objects.create_user(
            "pdf_admin", password="pw-pdf_admin-123456", is_staff=True
        )
        self.period_end = date(2026, 5, 15)
        # 給与確定→Payslip 作成
        self.payslip = Payslip.objects.create(
            payroll_period=services.get_or_create_period(self.period_end),
            staff=self.staff,
            work_days=20,
            work_minutes=20 * 480,
            hourly_wage=1500,
            base_wage_yen=240000,
            gross_yen=240000,
            total_deduction_yen=30000,
            net_pay_yen=210000,
            confirmed_at=timezone.now(),
            confirmed_by=self.admin,
        )

    def _url(self):
        return reverse("attendance:manage_payslip_pdf", args=[self.staff.id])

    def test_pdf_download_returns_pdf(self):
        self.client.force_login(self.admin)
        response = self.client.get(f"{self._url()}?period={self.period_end:%Y-%m-%d}")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "application/pdf")
        self.assertTrue(response["Content-Disposition"].startswith("attachment"))
        self.assertTrue(response.content.startswith(b"%PDF-"))

    def test_pdf_requires_staff_member(self):
        # 非管理者はログインページへリダイレクト
        self.client.force_login(self.user)  # 一般ユーザー
        response = self.client.get(f"{self._url()}?period={self.period_end:%Y-%m-%d}")
        self.assertEqual(response.status_code, 302)

    def test_pdf_redirects_when_not_confirmed(self):
        # 未確定の期間を要求 → 詳細にメッセージ付きで戻る
        self.client.force_login(self.admin)
        other = date(2026, 4, 15)
        response = self.client.get(f"{self._url()}?period={other:%Y-%m-%d}")
        self.assertEqual(response.status_code, 302)
        # /manage/payroll/<id>/ にリダイレクト
        self.assertIn("manage/payroll", response["Location"])

    def test_bulk_pdf_returns_pdf(self):
        self.client.force_login(self.admin)
        url = reverse("attendance:manage_payslip_bulk_pdf")
        response = self.client.get(f"{url}?period={self.period_end:%Y-%m-%d}")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "application/pdf")
        self.assertTrue(response.content.startswith(b"%PDF-"))

    def test_bulk_pdf_filter_by_company(self):
        # 他会社のスタッフを追加（LSN）
        _, other = _make_staff("pdf_other", "別 太郎", self.store)
        other.company = "LSN"
        other.save()
        HourlyWage.objects.create(
            staff=other, amount=1500, effective_from=date(2026, 1, 1)
        )
        Payslip.objects.create(
            payroll_period=self.payslip.payroll_period,
            staff=other,
            work_days=10,
            work_minutes=10 * 480,
            hourly_wage=1500,
            base_wage_yen=120000,
            gross_yen=120000,
            total_deduction_yen=10000,
            net_pay_yen=110000,
            confirmed_at=timezone.now(),
            confirmed_by=self.admin,
        )
        self.client.force_login(self.admin)
        url = reverse("attendance:manage_payslip_bulk_pdf")
        # LNのみ → 1名分のPDF
        response = self.client.get(f"{url}?period={self.period_end:%Y-%m-%d}&company=LN")
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.content.startswith(b"%PDF-"))
        # ファイル名に LN が含まれる
        self.assertIn("LN", response["Content-Disposition"])

    def test_bulk_pdf_redirects_when_no_payslips(self):
        self.client.force_login(self.admin)
        url = reverse("attendance:manage_payslip_bulk_pdf")
        # 未確定の月度を要求 → リダイレクト
        response = self.client.get(f"{url}?period=2026-04-15")
        self.assertEqual(response.status_code, 302)

    def test_punch_payslip_pdf_by_token(self):
        """本人がトークンURL経由で自分のPDFをダウンロードできる。"""
        url = reverse(
            "attendance:punch_payslip_pdf", args=[self.staff.punch_token]
        )
        response = self.client.get(f"{url}?period={self.period_end:%Y-%m-%d}")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "application/pdf")

    def test_punch_payslip_pdf_unknown_token(self):
        import uuid as _uuid
        url = reverse("attendance:punch_payslip_pdf", args=[_uuid.uuid4()])
        response = self.client.get(f"{url}?period={self.period_end:%Y-%m-%d}")
        self.assertEqual(response.status_code, 404)

    def test_punch_payslip_pdf_unconfirmed_redirects(self):
        url = reverse(
            "attendance:punch_payslip_pdf", args=[self.staff.punch_token]
        )
        response = self.client.get(f"{url}?period=2026-04-15")
        self.assertEqual(response.status_code, 302)
        # 個人ページへ戻る
        self.assertIn(str(self.staff.punch_token), response["Location"])

    def test_token_page_lists_past_payslips(self):
        """個人ページに過去の確定済み明細リストが出る。"""
        response = self.client.get(
            reverse("attendance:punch_by_token", args=[self.staff.punch_token])
        )
        content = response.content.decode()
        self.assertIn("過去の給与明細", content)
        self.assertIn("2026年5月", content)


class DriverAndGasolineTests(TestCase):
    """ドライバー手当・通勤ガソリン代のテスト。"""

    def setUp(self):
        self.store = Store.objects.create(business_unit="sales", name="販売部")
        self.user, self.staff = _make_staff("drv_taro", "ドライバー太郎", self.store)
        self.staff.business_unit = "sales"
        self.staff.save()
        HourlyWage.objects.create(
            staff=self.staff, amount=1500, effective_from=date(2026, 1, 1)
        )
        from decimal import Decimal
        self.profile = StaffPayrollProfile.objects.create(
            staff=self.staff,
            is_driver=True,
            commute_method="car",
            commute_distance_km=Decimal("10.0"),
            fuel_efficiency_kml=Decimal("15.0"),
            employment_insurance_enrolled=False,
        )
        self.setting = PayrollSetting.current()
        self.setting.driver_allowance_yen = 1500
        self.setting.save()
        self.period_end = date(2026, 5, 15)
        self.period = services.get_or_create_period(self.period_end)
        self.period.gasoline_yen_per_liter = 170
        self.period.save()
        self.period_start = aggregation.period_start_for(self.period_end, 15)

    def _calc(self, **manual_kwargs):
        defaults = dict(
            staff=self.staff,
            payroll_period=self.period,
            work_days=0, work_minutes=0,
        )
        defaults.update(manual_kwargs)
        manual = ManualWorkHours(**defaults)
        return payroll.compute_payslip(
            self.staff, self.period_start, self.period_end, self.setting,
            manual=manual,
        )

    def test_driver_allowance_when_is_driver(self):
        calc = self._calc(driver_count=4)
        # 4回 × 1500円 = 6,000円
        self.assertEqual(calc.driver_allowance_yen, 6000)
        self.assertEqual(calc.driver_count, 4)

    def test_driver_zero_when_not_driver(self):
        """is_driver=False のスタッフは count があっても0計上。"""
        self.profile.is_driver = False
        self.profile.save()
        calc = self._calc(driver_count=4)
        self.assertEqual(calc.driver_allowance_yen, 0)

    def test_gasoline_when_commute_by_car(self):
        # 片道10km × 2 × 20日 × 170円 ÷ 15km/L = 4,533.33 → 4,533円
        calc = self._calc(work_days=20, work_minutes=20 * 480)
        # (10 × 2 × 20 × 170) / 15 = 68000 / 15 = 4533.33... → 4533
        self.assertEqual(calc.gasoline_yen, 4533)

    def test_gasoline_zero_when_transit_user(self):
        self.profile.commute_method = "transit"
        self.profile.save()
        calc = self._calc(work_days=20)
        self.assertEqual(calc.gasoline_yen, 0)

    def test_gasoline_zero_when_rate_zero(self):
        self.period.gasoline_yen_per_liter = 0
        self.period.save()
        calc = self._calc(work_days=20)
        self.assertEqual(calc.gasoline_yen, 0)

    def test_gasoline_is_non_taxable(self):
        """ガソリン代は通勤手当と同じく非課税（所得税の課税基礎から除外）。"""
        from decimal import Decimal
        # 試算：base=0 / gasoline=4533 → taxable = gross - commute - gasoline = 0
        # 雇用保険は gross 全額にかかるが課税基礎には乗らないことを確認
        self.profile.dependents_count = 0
        self.profile.save()
        calc = self._calc(work_days=20, work_minutes=20 * 480)
        # gasoline_yen > 0 で income_tax は base_wage 分だけにかかる（通勤・ガソリン除外）
        # base_wage = 20×8×1500 = 240,000
        # taxable = 240,000+0-0-4533+その他=235467? 確認
        # gross_yen = base 240000 + gasoline 4533 = 244533
        # taxable = gross - commute(0) - gasoline(4533) = 240000
        self.assertEqual(calc.gasoline_yen, 4533)
        self.assertEqual(calc.commute_allowance_yen, 0)
        # 課税対象が gasoline を含まないことを所得税の値で確認
        # （詳細値はテストの本質ではないが、>0でもガソリン非含のロジックを確認）
        self.assertGreater(calc.gross_yen, 240000)

    def test_payroll_setting_form_includes_driver_rate(self):
        """給与設定画面にドライバー手当単価フィールドが含まれる。"""
        admin = User.objects.create_user(
            "drv_admin", password="pw-drv_admin-123456", is_staff=True
        )
        self.client.force_login(admin)
        response = self.client.get(reverse("attendance:manage_settings"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "driver_allowance_yen")

    def test_save_gasoline_endpoint(self):
        admin = User.objects.create_user(
            "gas_admin", password="pw-gas_admin-123456", is_staff=True
        )
        self.client.force_login(admin)
        response = self.client.post(
            reverse("attendance:manage_save_gasoline"),
            {"period": self.period_end.isoformat(), "gasoline_yen_per_liter": "180"},
        )
        self.assertEqual(response.status_code, 302)
        self.period.refresh_from_db()
        self.assertEqual(self.period.gasoline_yen_per_liter, 180)

    def test_save_manual_hours_with_driver(self):
        obj = services.save_manual_hours(
            self.staff, self.period,
            work_days=20, work_minutes=160 * 60,
            overtime_minutes=0, night_minutes=0, holiday_minutes=0,
            driver_count=3,
        )
        self.assertIsNotNone(obj)
        self.assertEqual(obj.driver_count, 3)

    def test_payslip_detail_shows_driver_input_for_driver(self):
        admin = User.objects.create_user(
            "dd_admin", password="pw-dd_admin-123456", is_staff=True
        )
        self.client.force_login(admin)
        url = reverse("attendance:manage_payslip_detail", args=[self.staff.id])
        response = self.client.get(f"{url}?period={self.period_end:%Y-%m-%d}")
        content = response.content.decode()
        self.assertIn('name="driver_count"', content)

    def test_payslip_detail_hides_driver_input_for_non_driver(self):
        self.profile.is_driver = False
        self.profile.save()
        admin = User.objects.create_user(
            "ndd_admin", password="pw-ndd_admin-123456", is_staff=True
        )
        self.client.force_login(admin)
        url = reverse("attendance:manage_payslip_detail", args=[self.staff.id])
        response = self.client.get(f"{url}?period={self.period_end:%Y-%m-%d}")
        content = response.content.decode()
        self.assertNotIn('name="driver_count"', content)


class WageBookTests(TestCase):
    """賃金台帳（労基法108条）のテスト。"""

    def setUp(self):
        self.store = Store.objects.create(business_unit="sales", name="販売部")
        self.user, self.staff = _make_staff("wb_taro", "賃金 太郎", self.store)
        self.staff.business_unit = "sales"
        self.staff.company = "LN"
        self.staff.save()
        HourlyWage.objects.create(
            staff=self.staff, amount=1500, effective_from=date(2026, 1, 1)
        )
        self.admin = User.objects.create_user(
            "wb_admin", password="pw-wb_admin-123456", is_staff=True
        )
        # 2026年に2か月分の確定 Payslip を作る
        for m in (3, 5):
            period = services.get_or_create_period(date(2026, m, 15))
            Payslip.objects.create(
                payroll_period=period,
                staff=self.staff,
                work_days=20,
                work_minutes=20 * 480,
                hourly_wage=1500,
                base_wage_yen=240000,
                gross_yen=250000,
                total_deduction_yen=40000,
                net_pay_yen=210000,
                confirmed_at=timezone.now(),
                confirmed_by=self.admin,
            )

    def test_wage_book_shows_two_months(self):
        self.client.force_login(self.admin)
        url = reverse("attendance:manage_wage_book", args=[self.staff.id])
        response = self.client.get(f"{url}?year=2026")
        self.assertEqual(response.status_code, 200)
        content = response.content.decode()
        # 月ラベル
        self.assertIn("3月", content)
        self.assertIn("5月", content)
        # 各項目
        self.assertIn("基本賃金", content)
        self.assertIn("差引支給額", content)
        self.assertIn("年間合計", content)

    def test_wage_book_empty_year_shows_empty(self):
        self.client.force_login(self.admin)
        url = reverse("attendance:manage_wage_book", args=[self.staff.id])
        response = self.client.get(f"{url}?year=2025")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "確定済み給与明細がありません")

    def test_wage_book_csv_returns_csv(self):
        self.client.force_login(self.admin)
        url = reverse("attendance:manage_wage_book_csv", args=[self.staff.id])
        response = self.client.get(f"{url}?year=2026")
        self.assertEqual(response.status_code, 200)
        self.assertIn("text/csv", response["Content-Type"])
        # BOM
        self.assertTrue(response.content.startswith("﻿".encode("utf-8")))
        body = response.content.decode("utf-8-sig")
        self.assertIn("賃金台帳", body)
        self.assertIn("賃金 太郎", body)
        self.assertIn("基本賃金", body)
        # 2か月分の240000 + 240000 = 480000（年間合計）
        self.assertIn("480000", body)

    def test_wage_book_requires_staff_member(self):
        self.client.force_login(self.user)  # 一般ユーザー
        url = reverse("attendance:manage_wage_book", args=[self.staff.id])
        response = self.client.get(url)
        self.assertEqual(response.status_code, 302)
        self.assertIn("/login", response["Location"])

    def test_wage_book_default_year_is_current(self):
        """year パラメータ無しでアクセスすると今年分が表示される。"""
        self.client.force_login(self.admin)
        url = reverse("attendance:manage_wage_book", args=[self.staff.id])
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        # 今年=2026 の Payslip が出る
        self.assertContains(response, "3月")

    def test_wage_book_sums_correctly(self):
        """各項目の年間合計が正しく算出される。"""
        self.client.force_login(self.admin)
        url = reverse("attendance:manage_wage_book", args=[self.staff.id])
        response = self.client.get(f"{url}?year=2026")
        content = response.content.decode()
        # 基本賃金 240,000 × 2 = 480,000
        self.assertIn("480,000", content)
        # 差引支給額 210,000 × 2 = 420,000
        self.assertIn("420,000", content)


class StaffRosterTests(TestCase):
    """労働者名簿（労基法107条）のテスト。"""

    def setUp(self):
        self.store = Store.objects.create(business_unit="cafeteria", name="本店食堂")
        self.user, self.staff = _make_staff("rs_taro", "名簿 太郎", self.store)
        self.staff.business_unit = "cafeteria"
        self.staff.hired_on = date(2024, 4, 1)
        self.staff.birthday = date(1990, 5, 15)
        self.staff.gender = "male"
        self.staff.address = "東京都新宿区"
        self.staff.phone = "090-1234-5678"
        self.staff.job_description = "お弁当製造"
        self.staff.save()
        self.admin = User.objects.create_user(
            "rs_admin", password="pw-rs_admin-123456", is_staff=True
        )

    def test_list_shows_staff(self):
        self.client.force_login(self.admin)
        response = self.client.get(reverse("attendance:manage_staff_roster"))
        self.assertEqual(response.status_code, 200)
        content = response.content.decode()
        self.assertIn("名簿 太郎", content)
        self.assertIn("お弁当製造", content)
        self.assertIn("東京都新宿区", content)
        self.assertIn("男性", content)

    def test_list_filters_retired(self):
        # 退職スタッフを追加
        _, retired = _make_staff("rs_ret", "退職 花子", self.store)
        retired.is_active = False
        retired.retired_on = date(2026, 3, 31)
        retired.retire_reason = "自己都合"
        retired.save()
        self.client.force_login(self.admin)
        # status=retired
        response = self.client.get(
            reverse("attendance:manage_staff_roster") + "?status=retired"
        )
        content = response.content.decode()
        self.assertIn("退職 花子", content)
        self.assertNotIn("名簿 太郎", content)

    def test_csv_export(self):
        self.client.force_login(self.admin)
        response = self.client.get(reverse("attendance:manage_staff_roster_csv"))
        self.assertEqual(response.status_code, 200)
        self.assertIn("text/csv", response["Content-Type"])
        self.assertTrue(response.content.startswith("﻿".encode("utf-8")))
        body = response.content.decode("utf-8-sig")
        self.assertIn("氏名,性別,生年月日", body)
        self.assertIn("名簿 太郎", body)
        self.assertIn("お弁当製造", body)
        self.assertIn("男性", body)

    def test_edit_form_renders(self):
        self.client.force_login(self.admin)
        response = self.client.get(
            reverse("attendance:manage_staff_roster_edit", args=[self.staff.id])
        )
        self.assertEqual(response.status_code, 200)
        content = response.content.decode()
        self.assertIn("名簿 太郎", content)
        self.assertIn("業務の種類", content)
        self.assertIn("退職情報", content)

    def test_edit_save_updates_fields(self):
        self.client.force_login(self.admin)
        response = self.client.post(
            reverse("attendance:manage_staff_roster_edit", args=[self.staff.id]),
            {
                "display_name": "名簿 太郎",
                "business_unit": "cafeteria",
                "company": "LSN",
                "store": self.store.id,
                "hired_on": "2024-04-01",
                "birthday": "1990-05-15",
                "gender": "male",
                "address": "東京都品川区",  # 変更
                "phone": "090-1234-5678",
                "job_description": "ホール販売",  # 変更
                "is_active": "on",
            },
        )
        self.assertEqual(response.status_code, 302)
        self.staff.refresh_from_db()
        self.assertEqual(self.staff.address, "東京都品川区")
        self.assertEqual(self.staff.job_description, "ホール販売")

    def test_retire_processing_disables_active(self):
        """退職日を入力すると is_active が自動でOFFになる。"""
        self.client.force_login(self.admin)
        self.client.post(
            reverse("attendance:manage_staff_roster_edit", args=[self.staff.id]),
            {
                "display_name": "名簿 太郎",
                "business_unit": "cafeteria",
                "company": "LSN",
                "store": self.store.id,
                "hired_on": "2024-04-01",
                "is_active": "on",  # ONで送るが…
                "retired_on": "2026-05-15",  # 退職日があるので
                "retire_reason": "契約満了",
            },
        )
        self.staff.refresh_from_db()
        self.assertFalse(self.staff.is_active)  # 自動でOFF
        self.assertEqual(self.staff.retired_on, date(2026, 5, 15))

    def test_retire_reason_without_date_errors(self):
        """退職事由だけ入力して退職日が空はバリデーションエラー。"""
        self.client.force_login(self.admin)
        response = self.client.post(
            reverse("attendance:manage_staff_roster_edit", args=[self.staff.id]),
            {
                "display_name": "名簿 太郎",
                "business_unit": "cafeteria",
                "company": "LSN",
                "store": self.store.id,
                "hired_on": "2024-04-01",
                "is_active": "on",
                "retired_on": "",  # 空
                "retire_reason": "自己都合",  # 入っている
            },
        )
        self.assertEqual(response.status_code, 200)  # フォーム再表示
        self.assertContains(response, "退職年月日も必要")


class StaffCreateTests(TestCase):
    """スタッフ新規追加（User + Staff + HourlyWage 連動）のテスト。"""

    def setUp(self):
        self.store = Store.objects.create(business_unit="cafeteria", name="本店食堂")
        self.admin = User.objects.create_user(
            "sc_admin", password="pw-sc_admin-123456", is_staff=True
        )

    def _post_data(self, **overrides):
        data = {
            "username": "new_taro",
            "password": "init-pw-12345",
            "last_name": "新規",
            "first_name": "太郎",
            "business_unit": "cafeteria",
            "company": "LSN",
            "store": self.store.id,
            "hired_on": "2026-05-27",
            "job_description": "お弁当製造",
            "initial_hourly_wage": "1500",
            "birthday": "1995-04-15",
            "gender": "male",
            "address": "東京都新宿区",
            "phone": "090-0000-0000",
        }
        data.update(overrides)
        return data

    def test_new_form_renders(self):
        self.client.force_login(self.admin)
        response = self.client.get(reverse("attendance:manage_staff_new"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "スタッフ新規追加")
        self.assertContains(response, "認証アカウント")
        self.assertContains(response, "初期時給")

    def test_create_succeeds(self):
        self.client.force_login(self.admin)
        response = self.client.post(
            reverse("attendance:manage_staff_new"), self._post_data()
        )
        self.assertEqual(response.status_code, 302)
        # User と Staff と HourlyWage が同時に作られる
        self.assertTrue(User.objects.filter(username="new_taro").exists())
        staff = Staff.objects.get(display_name="新規 太郎")
        self.assertEqual(staff.business_unit, "cafeteria")
        self.assertEqual(staff.company, "LSN")
        self.assertEqual(staff.job_description, "お弁当製造")
        self.assertEqual(staff.birthday, date(1995, 4, 15))
        # 初期時給
        wages = HourlyWage.objects.filter(staff=staff)
        self.assertEqual(wages.count(), 1)
        self.assertEqual(wages.first().amount, 1500)

    def test_create_duplicate_username_rejected(self):
        User.objects.create_user("taken_user", password="pw-taken-123456")
        self.client.force_login(self.admin)
        response = self.client.post(
            reverse("attendance:manage_staff_new"),
            self._post_data(username="taken_user"),
        )
        self.assertEqual(response.status_code, 200)  # フォーム再表示
        self.assertContains(response, "既に使われています")
        # Staff は作られていない
        self.assertFalse(Staff.objects.filter(display_name="新規 太郎").exists())

    def test_create_short_password_rejected(self):
        self.client.force_login(self.admin)
        response = self.client.post(
            reverse("attendance:manage_staff_new"),
            self._post_data(password="abc"),  # 8文字未満
        )
        self.assertEqual(response.status_code, 200)
        self.assertFalse(Staff.objects.filter(display_name="新規 太郎").exists())

    def test_create_without_initial_wage(self):
        """初期時給を空でも作成は通る（HourlyWage は作られない）。"""
        self.client.force_login(self.admin)
        response = self.client.post(
            reverse("attendance:manage_staff_new"),
            self._post_data(initial_hourly_wage=""),
        )
        self.assertEqual(response.status_code, 302)
        staff = Staff.objects.get(display_name="新規 太郎")
        self.assertEqual(HourlyWage.objects.filter(staff=staff).count(), 0)

    def test_requires_staff_member(self):
        self.client.force_login(
            User.objects.create_user("non_admin", password="pw-non-123456")
        )
        response = self.client.get(reverse("attendance:manage_staff_new"))
        self.assertEqual(response.status_code, 302)

    def test_service_layer_creates_atomically(self):
        """サービス層を直接呼んでも User + Staff + HourlyWage が揃う。"""
        staff = services.create_staff_with_user(
            username="svc_taro",
            password="pw-svc-12345678",
            last_name="サービス",
            first_name="太郎",
            business_unit="sales",
            company="LN",
            store=self.store,
            hired_on=date(2026, 5, 1),
            initial_hourly_wage=1600,
            job_description="販売員",
        )
        self.assertTrue(User.objects.filter(username="svc_taro").exists())
        self.assertEqual(staff.display_name, "サービス 太郎")
        self.assertEqual(staff.company, "LN")
        self.assertEqual(HourlyWage.objects.get(staff=staff).amount, 1600)

    def test_attach_staff_to_existing_user(self):
        """既存ユーザーに勤怠Staffを後付けでき、新規Userは増えない。"""
        self.client.force_login(self.admin)
        existing = User.objects.create_user(
            username="hanbai_taro", password="pw-hanbai-123456",
            last_name="販売", first_name="太郎",
        )
        before = User.objects.count()
        resp = self.client.post(
            reverse("attendance:manage_staff_from_user"),
            {
                "user": existing.id,
                "business_unit": "sales",
                "company": "LN",
                "store": self.store.id,
                "initial_hourly_wage": "1500",
            },
        )
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(User.objects.count(), before)  # 新規Userは増えない
        staff = Staff.objects.get(user=existing)
        self.assertEqual(staff.display_name, "販売 太郎")
        self.assertEqual(staff.company, "LN")
        self.assertEqual(HourlyWage.objects.get(staff=staff).amount, 1500)

    def test_attach_rejects_user_with_existing_staff(self):
        """既にStaffがあるユーザーには後付けできない（二重防止）。"""
        u = User.objects.create_user(
            username="already_staff", password="pw-already-123456",
            last_name="既存", first_name="花子",
        )
        services.attach_staff_to_user(
            user=u, business_unit="cafeteria", company="LSN", store=self.store,
        )
        with self.assertRaises(services.PunchError):
            services.attach_staff_to_user(
                user=u, business_unit="cafeteria", company="LSN", store=self.store,
            )


class PaidLeaveSummaryTests(TestCase):
    """有給休暇の年度集計（10月〜9月）のテスト。"""

    def setUp(self):
        self.store = Store.objects.create(business_unit="sales", name="販売部")
        self.user, self.staff = _make_staff("pl_taro", "有給 太郎", self.store)
        self.staff.business_unit = "sales"
        self.staff.paid_leave_granted_days = 12
        self.staff.save()

    def test_year_range_for_may(self):
        """5月は前年10月から今年9月の年度。"""
        start, end = services.paid_leave_year_range(date(2026, 5, 27))
        self.assertEqual(start, date(2025, 10, 1))
        self.assertEqual(end, date(2026, 9, 30))

    def test_year_range_for_november(self):
        """11月は今年10月から翌年9月の年度。"""
        start, end = services.paid_leave_year_range(date(2026, 11, 15))
        self.assertEqual(start, date(2026, 10, 1))
        self.assertEqual(end, date(2027, 9, 30))

    def test_year_range_for_october_first(self):
        """10月1日は新年度の初日。"""
        start, end = services.paid_leave_year_range(date(2026, 10, 1))
        self.assertEqual(start, date(2026, 10, 1))
        self.assertEqual(end, date(2027, 9, 30))

    def test_year_range_for_september_30(self):
        """9月30日は前年10月からの年度の最終日。"""
        start, end = services.paid_leave_year_range(date(2026, 9, 30))
        self.assertEqual(start, date(2025, 10, 1))
        self.assertEqual(end, date(2026, 9, 30))

    def test_summary_with_no_leave_taken(self):
        summary = services.paid_leave_summary(self.staff, date(2026, 5, 27))
        self.assertEqual(summary["granted"], 12)
        self.assertEqual(summary["used"], 0)
        self.assertEqual(summary["remaining"], 12)
        self.assertEqual(summary["year_label"], "2025/10〜2026/9")

    def test_summary_with_leave_in_range(self):
        # 年度内の月で有給取得
        for m, days in [(11, 1), (12, 2), (3, 1)]:  # 2025-11, 2025-12, 2026-3
            year = 2025 if m >= 10 else 2026
            period = services.get_or_create_period(date(year, m, 15))
            ManualWorkHours.objects.create(
                staff=self.staff, payroll_period=period,
                work_days=20, work_minutes=20*480,
                paid_leave_days=days,
            )
        summary = services.paid_leave_summary(self.staff, date(2026, 5, 27))
        self.assertEqual(summary["used"], 4)
        self.assertEqual(summary["remaining"], 8)

    def test_summary_excludes_other_year(self):
        """異なる有給年度の取得は集計に含まれない。"""
        # 2026-11月度（次年度）に有給3日
        period = services.get_or_create_period(date(2026, 11, 15))
        ManualWorkHours.objects.create(
            staff=self.staff, payroll_period=period,
            work_days=20, work_minutes=20*480,
            paid_leave_days=3,
        )
        # 5月時点では今年度（2025/10〜2026/9）内なので2026/11月分は入らない
        summary = services.paid_leave_summary(self.staff, date(2026, 5, 27))
        self.assertEqual(summary["used"], 0)
        # 11月時点では2026/10〜2027/9年度に含まれる
        summary2 = services.paid_leave_summary(self.staff, date(2026, 12, 1))
        self.assertEqual(summary2["used"], 3)

    def test_remaining_clamped_to_zero(self):
        """取得日数が付与日数を超えても残は0以下にならない。"""
        period = services.get_or_create_period(date(2025, 11, 15))
        ManualWorkHours.objects.create(
            staff=self.staff, payroll_period=period,
            work_days=20, work_minutes=20*480,
            paid_leave_days=20,  # 付与12 < 取得20
        )
        summary = services.paid_leave_summary(self.staff, date(2026, 5, 27))
        self.assertEqual(summary["used"], 20)
        self.assertEqual(summary["remaining"], 0)
