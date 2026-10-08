"""食数予測（店は名前で数える・完売補正・直近5営業日×曜日の癖）のテスト。"""
from datetime import date, time, timedelta
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TestCase, override_settings
from django.urls import reverse

from sales.meal_forecast import estimated_demand, load_history, predict
from sales.models import DailyReport, SalesLocation

TODAY = date(2026, 10, 2)  # 金曜


def weekdays_before(day, n):
    out, d = [], day - timedelta(days=1)
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d)
        d -= timedelta(days=1)
    return sorted(out)


def report(d, name, no, sold, rem=5, sold_out=None):
    DailyReport.objects.create(
        date=d, location=name, location_no=no, total_quantity=sold + rem, total_sales_quantity=sold,
        total_remaining=rem, total_revenue=0, sold_out_time=sold_out,
        opening_time=time(11, 0), closing_time=time(13, 0),
    )


class DemandTests(TestCase):
    def test_sold_out_early_is_lifted(self):
        self.assertEqual(estimated_demand(100, 5, None, time(11), time(13)), 100)
        # 12:00完売・11〜13時営業 → 残り時間の割合0.5 × 0.4 = +20%
        self.assertAlmostEqual(estimated_demand(100, 0, time(12), time(11), time(13)), 120)
        # 完売時刻が分からなければ上乗せなし
        self.assertEqual(estimated_demand(100, 0, None, time(11), time(13)), 100)

    def test_recent_level_times_weekday_habit(self):
        rows = []
        for d in weekdays_before(TODAY, 40):
            demand = 120 if d.weekday() == 0 else 100  # 月曜だけ多い店
            rows.append({"date": d, "wd": d.weekday(), "sold": demand, "rem": 5, "demand": demand, "sold_out": False})
        monday = predict(rows, 0)
        tuesday = predict(rows, 1)
        self.assertGreater(monday["weekday_idx"], 1.1)
        self.assertLess(tuesday["weekday_idx"], 1.0)
        self.assertGreater(monday["pred"], tuesday["pred"])

    def test_trend_is_shown_not_multiplied(self):
        rows = []
        for i, d in enumerate(weekdays_before(TODAY, 25)):
            demand = 80 if i < 15 else 100  # 直近10日で上がった
            rows.append({"date": d, "wd": d.weekday(), "sold": demand, "rem": 5, "demand": demand, "sold_out": False})
        p = predict(rows, 0)
        self.assertAlmostEqual(p["trend"], 0.25)
        self.assertAlmostEqual(p["recent"], 100)
        self.assertLessEqual(p["pred"], 100 * 1.3)

    def test_too_little_history(self):
        rows = [{"date": TODAY, "wd": 0, "sold": 10, "rem": 0, "demand": 10, "sold_out": True}] * 3
        self.assertIsNone(predict(rows, 0))


@override_settings(MENU_HISTORY_EXCLUDED_LOCATIONS=["店"])
class RenumberingTests(TestCase):
    """販売場所No.が途中で振り直されても、店の実績が混ざらないこと。"""

    def setUp(self):
        cache.clear()
        # 今のマスタ：A=No.1、B=No.2
        SalesLocation.objects.create(no=1, name="A店", type="車", price_type="A", service_name="")
        SalesLocation.objects.create(no=2, name="B店", type="室内", price_type="A", service_name="")
        days = weekdays_before(TODAY, 30)
        for i, d in enumerate(days):
            # 前半は A=No.2・B=No.1（振り直し前）、後半は A=No.1・B=No.2
            a_no, b_no = (2, 1) if i < 15 else (1, 2)
            report(d, "A店", a_no, 40)
            report(d, "B店", b_no, 140)
        report(days[-1], "店", 36, 500)

    def test_history_grouped_by_name(self):
        hist = load_history(TODAY - timedelta(days=60), TODAY)
        self.assertEqual({r["sold"] for r in hist["A店"]}, {40})
        self.assertEqual({r["sold"] for r in hist["B店"]}, {140})
        self.assertNotIn("店", hist)

    def test_page_shows_each_store_with_its_own_numbers(self):
        user = get_user_model().objects.create_user("viewer", password="pw-viewer-123456")
        self.client.force_login(user)
        with patch("lunchnetsale.views.timezone.localdate", return_value=TODAY), \
             patch("lunchnetsale.views._fetch_lunch_weather", return_value={}):
            res = self.client.get(reverse("meal_forecast"))
        locs = {l["name"]: l for l in res.context["fc"]["days"][0]["locs"]}
        self.assertEqual(locs["A店"]["pred"], 40)
        self.assertEqual(locs["B店"]["pred"], 140)
        self.assertEqual(locs["B店"]["type"], "室内")
        self.assertNotIn("店", locs)
        self.assertContains(res, "直近の流れ")
