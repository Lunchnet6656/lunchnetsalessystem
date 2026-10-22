"""日計表に出す「その売場・その日に使われたクーポン」の割り出し（同じ日のスタンプ＝確定／前回スタンプ＝推定）。"""
import datetime
from unittest import mock

from django.contrib.auth.models import User
from django.test import TestCase
from django.utils import timezone

from reservations.models import LineMember
from sales.models import DailyReport, SalesLocation
from stamps import services
from stamps.models import Reward, RewardTier, StampCard, StampLog

DAY = datetime.date(2026, 10, 22)


def _dt(d, hh, mm):
    return timezone.make_aware(datetime.datetime(d.year, d.month, d.day, hh, mm), timezone.get_current_timezone())


class CouponsUsedAtTest(TestCase):
    def setUp(self):
        RewardTier.ensure_defaults()
        self.tier = RewardTier.objects.order_by("threshold_pt").first()
        self.a = SalesLocation.objects.create(no=1, name="本店", type="室内", price_type="A")
        self.b = SalesLocation.objects.create(no=2, name="二号店", type="室内", price_type="A")
        self.n = 0

    def card(self):
        self.n += 1
        m = LineMember.objects.create(line_user_id=f"U{self.n}", name=f"会員{self.n}")
        return StampCard.objects.create(member=m, started_on=DAY - datetime.timedelta(days=30),
                                        expires_on=DAY + datetime.timedelta(days=60))

    def stamp(self, card, loc, day, hh=12):
        StampLog.objects.create(card=card, location=loc, stamped_on=day, stamped_at=_dt(day, hh, 0))

    def use(self, card, day, hh, mm, threshold=None):
        tier = self.tier
        return Reward.objects.create(
            card=card, tier=tier, threshold_pt=threshold or tier.threshold_pt, kind=tier.kind, label=tier.label,
            issued_at=_dt(day, 9, 0), valid_from=day - datetime.timedelta(days=5),
            expires_on=day + datetime.timedelta(days=30), used_at=_dt(day, hh, mm), status=Reward.STATUS_USED)

    def test_same_day_stamp_is_confirmed_and_previous_stamp_is_estimated(self):
        c1, c2, c3, c4 = self.card(), self.card(), self.card(), self.card()
        self.stamp(c1, self.a, DAY)                                     # 同じ日に本店 → 確定
        self.use(c1, DAY, 11, 42)
        self.stamp(c2, self.a, DAY - datetime.timedelta(days=3))        # 前回は本店 → 推定
        self.use(c2, DAY, 12, 31)
        self.stamp(c3, self.b, DAY - datetime.timedelta(days=1))        # 前回は二号店 → 本店には出ない
        self.use(c3, DAY, 12, 0)
        self.use(c4, DAY, 12, 10)                                       # スタンプ記録なし → どこにも出ない
        self.use(c1, DAY - datetime.timedelta(days=1), 12, 0, threshold=99)   # 前日の利用は出ない

        res = services.coupons_used_at(self.a, DAY)
        self.assertEqual([(r["count"], r["estimated"]) for r in res["rows"]], [(2, 1)])
        self.assertEqual([(i["time"], i["estimated"]) for i in res["items"]], [("11:42", False), ("12:31", True)])
        self.assertTrue(res["has_estimated"])
        self.assertEqual(services.coupons_used_at(self.b, DAY)["rows"][0]["count"], 1)

    def test_location_without_stamps_is_hidden(self):
        self.assertIsNone(services.coupons_used_at(self.a, DAY))
        self.stamp(self.card(), self.a, DAY - datetime.timedelta(days=10))
        self.assertEqual(services.coupons_used_at(self.a, DAY), {"rows": [], "items": [], "has_estimated": False})


class DailyReportDisplayTest(TestCase):
    def setUp(self):
        RewardTier.ensure_defaults()
        self.admin = User.objects.create_user(username="boss", password="pass", is_staff=True)
        self.client.force_login(self.admin)
        self.loc = SalesLocation.objects.create(no=1, name="本店", type="室内", price_type="A",
                                                service_name="なし", service_price=0)
        self.report = DailyReport.objects.create(date=DAY, location="本店", location_no=1, total_revenue=0)

    def test_edit_screen_shows_block_and_survives_errors(self):
        m = LineMember.objects.create(line_user_id="U1", name="会員")
        card = StampCard.objects.create(member=m, started_on=DAY, expires_on=DAY + datetime.timedelta(days=60))
        StampLog.objects.create(card=card, location=self.loc, stamped_on=DAY, stamped_at=_dt(DAY, 12, 0))
        res = self.client.get(f"/daily_report_list/{self.report.pk}/edit/")
        self.assertContains(res, 'id="stamp-coupon-block"')
        self.assertContains(res, "10/22（木）")
        self.assertContains(res, "なし")
        with mock.patch("stamps.services.coupons_used_at", side_effect=RuntimeError("boom")):
            res = self.client.get(f"/daily_report_list/{self.report.pk}/edit/")
        self.assertEqual(res.status_code, 200)
        self.assertContains(res, "クーポンの件数を表示できません")

    def test_hidden_when_location_has_no_stamps(self):
        res = self.client.get(f"/daily_report_list/{self.report.pk}/edit/")
        self.assertNotContains(res, 'id="stamp-coupon-block"')
