"""食数予測（店は名前で数える・完売補正・直近5営業日×曜日の癖）のテスト。"""
from datetime import date, time, timedelta
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TestCase, override_settings
from django.urls import reverse

from sales.meal_forecast import bridge_days, estimated_demand, load_history, predict
from sales.models import DailyReport, DayReview, SalesLocation
from sales.permissions_testing import grant

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
        # 12:00完売・11〜13時営業 → 残り時間の割合0.5 × 0.2 = +10%
        self.assertAlmostEqual(estimated_demand(100, 0, time(12), time(11), time(13)), 110)
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
        # 直近の水準＝重い5日（100）7割＋直近20日（80×10・100×10＝90）3割
        self.assertAlmostEqual(p["recent"], 97)
        self.assertLessEqual(p["pred"], 100 * 1.3)

    def test_one_off_days_are_left_out_of_level(self):
        rows = []
        for i, d in enumerate(weekdays_before(TODAY, 25)):
            one_off = i >= 21          # 直近4日は出張で落ちた（日計表で「その日だけの理由」）
            demand = 20 if one_off else 40
            rows.append({"date": d, "wd": d.weekday(), "sold": demand, "rem": 5, "demand": demand,
                         "sold_out": False, "one_off": one_off})
        self.assertAlmostEqual(predict(rows, 0)["recent"], 40)
        for r in rows:
            r["one_off"] = False
        self.assertLess(predict(rows, 0)["recent"], 32)

    def test_bridge_days_are_left_out_of_level(self):
        rows = []
        for i, d in enumerate(weekdays_before(TODAY, 25)):
            bridge = i == 24           # 直近1日は飛び石で落ちた
            demand = 20 if bridge else 40
            rows.append({"date": d, "wd": d.weekday(), "sold": demand, "rem": 5, "demand": demand,
                         "sold_out": False, "bridge": bridge})
        self.assertAlmostEqual(predict(rows, 0)["recent"], 40)

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
        grant(user, "can_view_performance_by_location")
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


class DayReviewExclusionTests(TestCase):
    """その日の振り返りで「予測から外す」出来事があった日は、影響した店のその日を計算に使わない。"""

    def setUp(self):
        self.a = SalesLocation.objects.create(no=1, name="A店", type="車", price_type="A", service_name="")
        self.b = SalesLocation.objects.create(no=2, name="B店", type="室内", price_type="A", service_name="")
        self.days = weekdays_before(TODAY, 10)
        for d in self.days:
            report(d, "A店", 1, 40)
            report(d, "B店", 2, 140)
        self.review = DayReview.objects.create(date=self.days[3], comment="")

    def dates(self, name):
        return {r["date"] for r in load_history(TODAY - timedelta(days=60), TODAY).get(name, [])}

    def test_all_locations_event_drops_the_day_everywhere(self):
        self.review.events.create(kind="stop", all_locations=True)
        self.assertNotIn(self.days[3], self.dates("A店"))
        self.assertNotIn(self.days[3], self.dates("B店"))
        self.assertEqual(len(self.dates("A店")), 9)

    def test_selected_locations_only(self):
        self.review.events.create(kind="reallocation").locations.add(self.a)
        self.assertNotIn(self.days[3], self.dates("A店"))
        self.assertIn(self.days[3], self.dates("B店"))

    def test_event_not_excluded_keeps_the_day(self):
        self.review.events.create(kind="reallocation", exclude=False).locations.add(self.a)
        self.assertIn(self.days[3], self.dates("A店"))

    def test_comment_only_keeps_the_day(self):
        self.review.comment = "新メニューの評判が良かった"
        self.review.save()
        self.assertIn(self.days[3], self.dates("A店"))


class MonthThirdTests(TestCase):
    def test_clear_late_month_weakness_is_flagged(self):
        from sales.meal_forecast import third_effect
        rows = []
        for d in weekdays_before(TODAY, 240):
            demand = 80 if d.day > 20 else 100   # 下旬だけ2割少ない店
            rows.append({"date": d, "wd": d.weekday(), "sold": demand, "rem": 5, "demand": demand, "sold_out": False})
        self.assertLess(third_effect(rows, 2), -0.03)
        self.assertIsNone(third_effect(rows[:50], 2))  # データが少なければ出さない

    def test_no_pattern_no_flag(self):
        from sales.meal_forecast import third_effect
        rows = [{"date": d, "wd": d.weekday(), "sold": 100, "rem": 5, "demand": 100.0, "sold_out": False}
                for d in weekdays_before(TODAY, 240)]
        self.assertIsNone(third_effect(rows, 2))


class RainMultiplierTest(TestCase):
    """店ごとの雨の倍率：雨の日の実績から、形態ごとの雨の補正をどれだけ強める・弱めるか。"""

    def rows(self, rain_ratio, rainy_every=7, n=60):  # 7おき＝雨の曜日が毎回変わる（曜日の癖に吸われない）
        out, d = [], date(2026, 6, 1)
        while len(out) < n:
            if d.weekday() < 5:
                rainy = len(out) % rainy_every == 0 and len(out) >= 10
                sold = 50 * (rain_ratio if rainy else 1)
                out.append({"date": d, "wd": d.weekday(), "sold": sold, "rem": 5, "demand": sold,
                            "sold_out": False, "level": "雨" if rainy else None})
            d += timedelta(days=1)
        return out

    def test_indoor_store_that_sells_more_on_rain(self):
        from sales.meal_forecast import rain_multiplier
        mult, n = rain_multiplier(self.rows(1.2), "室内")
        self.assertGreater(mult, 1.05)
        self.assertGreater(n, 5)

    def test_table_store_weaker_than_format(self):
        from sales.meal_forecast import rain_multiplier
        mult, _ = rain_multiplier(self.rows(0.6), "テーブル")   # 形態の値は0.8
        self.assertLess(mult, 0.9)

    def test_no_rain_days_keeps_format_value(self):
        from sales.meal_forecast import rain_multiplier
        self.assertEqual(rain_multiplier(self.rows(1.0, rainy_every=10**6), "車"), (1.0, 0))


class PlanQuantitiesTest(TestCase):
    """3パターンの持参数：売上UP＝80%点／廃棄率重視＝見込み廃棄率3%以下の最大／現状維持＝いつもの倍率。"""

    def test_three_plans(self):
        from sales.meal_forecast import plan_quantities
        ratios = [0.8, 0.9, 0.95, 1.0, 1.0, 1.05, 1.1, 1.2, 0.85, 1.15]
        plans = plan_quantities(50, ratios, 1.12, 700)
        self.assertEqual(plans["up"]["qty"], 56)   # 需要の80%点＝50×1.11
        self.assertLessEqual(plans["waste"]["waste_rate"], 0.03)
        self.assertLess(plans["waste"]["qty"], plans["up"]["qty"])
        self.assertEqual(plans["keep"]["qty"], 56)
        self.assertAlmostEqual(plans["keep"]["profit"], plans["keep"]["sold"] * 700 - 56 * 350)

    def test_keep_missing(self):
        from sales.meal_forecast import plan_quantities
        self.assertIsNone(plan_quantities(40, [1.0] * 10, None, None)["keep"])

    def test_draft_near(self):
        from lunchnetsale.views import _draft_near
        plans = {"up": {"qty": 60}, "keep": {"qty": 55}, "waste": {"qty": 48}}
        self.assertEqual(_draft_near(54, plans), "≒現状維持")
        self.assertEqual(_draft_near(70, plans), "売上UPより多")
        self.assertEqual(_draft_near(40, plans), "廃棄率重視より少")


class ForecastDatesTest(TestCase):
    """祝日・会社の休みは売らないので、予測する日から飛ばす。"""

    def test_holidays_are_skipped(self):
        from lunchnetsale.views import _meal_forecast
        from sales.models import Holiday
        Holiday.objects.create(date=date(2026, 11, 5))           # 会社の休み
        with patch("lunchnetsale.views._fetch_lunch_weather", return_value={}):
            fc = _meal_forecast(date(2026, 11, 2))               # 11/3 は文化の日
        days = [(d["date"], d["when"]) for d in fc["days"]]
        self.assertEqual(days, [(date(2026, 11, 4), "あさって"), (date(2026, 11, 6), "4日後"), (date(2026, 11, 9), "7日後")])


class BridgeDayTests(TestCase):
    """飛び石（前の日も次の日も休み）の日は全店 −10%。"""

    def test_detect(self):
        from sales.models import Holiday
        Holiday.objects.create(date=date(2026, 12, 29))   # 会社の休み
        days = [date(2026, 11, 2),    # 日曜と文化の日(火)に挟まれた月曜
                date(2026, 11, 9),    # ふつうの月曜
                date(2026, 11, 20),   # 3連休前の金曜（翌月曜は祝日ではない）
                date(2026, 11, 4),    # 祝日の翌日（次の日は平日）
                date(2026, 12, 28)]   # 日曜と会社の休みに挟まれた月曜
        self.assertEqual(bridge_days(days), {date(2026, 11, 2), date(2026, 12, 28)})

    def test_forecast_reduces_all_stores(self):
        from lunchnetsale.views import _meal_forecast
        for name, no, t in (("外の店", 1, "車"), ("ビルの店", 2, "室内")):
            SalesLocation.objects.create(no=no, name=name, type=t, price_type="A", service_name="")
            for d in weekdays_before(date(2026, 10, 30), 20):
                report(d, name, no, 100)
        with patch("lunchnetsale.views._fetch_lunch_weather", return_value={}):
            fc = _meal_forecast(date(2026, 10, 30))      # 金曜。次は 11/2（飛び石）
        day = fc["days"][0]
        self.assertEqual(day["date"], date(2026, 11, 2))
        self.assertTrue(day["bridge"])
        self.assertEqual({l["name"]: (l["pred"], l["base"], l["bridge_pct"]) for l in day["locs"]},
                         {"外の店": (90, 100, -10), "ビルの店": (90, 100, -10)})
        self.assertFalse(fc["days"][1]["bridge"])        # 11/4（水）


@override_settings(MENU_HISTORY_EXCLUDED_LOCATIONS=["店"])
class PageLayoutTests(TestCase):
    """画面は数字・状態・操作だけ。計算の説明は ？ヘルプ「計算方法」1か所に、定数から出す。"""

    def setUp(self):
        import re
        self.re = re
        cache.clear()
        SalesLocation.objects.create(no=1, name="外の店", type="テーブル", price_type="A", service_name="")
        for d in weekdays_before(date(2026, 10, 30), 40):
            report(d, "外の店", 1, 100)
        user = get_user_model().objects.create_user("viewer", password="pw-viewer-123456")
        grant(user, "can_view_performance_by_location")
        self.client.force_login(user)

    def get(self):
        # 10/30（金）の次は 11/2（飛び石）
        with patch("lunchnetsale.views.timezone.localdate", return_value=date(2026, 10, 30)), \
             patch("lunchnetsale.views._fetch_lunch_weather", return_value={}):
            return self.client.get(reverse("meal_forecast"))

    def help_body(self, res):
        html = res.content.decode()
        m = self.re.search(r'<dialog id="hp-help-mf".*?</dialog>', html, self.re.S)
        self.assertIsNotNone(m)
        return m.group(0)

    def test_help_has_every_topic(self):
        body = self.help_body(self.get())
        self.assertIn(">計算方法</h2>", body)
        for word in ("直近の水準", "曜日の癖", "完売", "雨", "強風", "飛び石", "予測から外す", "売上UP", "現状維持",
                     "廃棄率重視", "要確認", "余裕分", "読み方", "最後は現場判断で"):
            self.assertIn(word, body)

    def test_help_numbers_follow_constants(self):
        self.assertIn("全店 ×0.9<", self.help_body(self.get()))
        with patch("sales.meal_forecast.BRIDGE_FACTOR", 0.85), patch("sales.meal_forecast.PLAN_WASTE_TARGET", 0.05), \
             patch("sales.weather.WIND_STRONG", 7.0), patch("lunchnetsale.views.DRAFT_FLAG_MIN", 8):
            res = self.get()
        body = self.help_body(res)
        self.assertIn("全店 ×0.85<", body)
        self.assertIn("見込み廃棄率5%以下", body)
        self.assertIn("7m/s以上", body)
        self.assertIn("8食以上", body)
        self.assertContains(res, "廃棄率重視＝廃棄率5%以下")      # ⓘ も同じ定数から

    def test_no_explanations_on_screen(self):
        res = self.get()
        html = res.content.decode()
        self.assertNotIn('data-bs-toggle="popover"', html)
        self.assertNotIn('class="alert', html)
        main = html[html.index('<div class="container mt-4">'):]
        main = self.re.sub(r'<dialog .*?</dialog>', "", main, flags=self.re.S)
        main = self.re.sub(r'<span id="hp-tip-[^"]*" class="hp-tip" popover>.*?</span><button[^>]*>×</button></span>',
                           "", main, flags=self.re.S)
        main = self.re.sub(r"<script.*?</script>", "", main, flags=self.re.S)
        self.assertNotIn("。", main)

    def test_tips_use_shared_parts(self):
        res = self.get()
        self.assertContains(res, 'popovertarget="hp-tip-mf-plan-1"')
        self.assertContains(res, 'popovertarget="hp-tip-mf-sum-1"')
        self.assertContains(res, "飛び石−10%")
        self.assertContains(res, "補正 −10%")
