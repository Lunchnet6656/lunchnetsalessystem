"""昼の雨×販売形式の補正と、天気予報の保存のテスト。"""
import io
import json
from datetime import date, timedelta
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.core.management import call_command
from django.test import TestCase
from django.urls import reverse

from sales.models import DailyReport, SalesLocation, WeatherForecastSnapshot
from sales.weather import fetch_lunch_weather, rain_factor, rain_level, wind_factor
from sales.permissions_testing import grant

TODAY = date(2026, 10, 2)  # 金曜。次の営業日は 10/5（月）


def open_meteo_payload(day, hourly_precip, snow=0.0):
    """day の 0〜23時。hourly_precip={時: mm}"""
    times = [f"{day}T{h:02d}:00" for h in range(24)]
    return {"hourly": {
        "time": times,
        "precipitation": [hourly_precip.get(h, 0.0) for h in range(24)],
        "snowfall": [snow if h in (11, 12) else 0.0 for h in range(24)],
        "temperature_2m": [20.0] * 24,
        "apparent_temperature": [18.0] * 24,
    }}


class FakeResponse(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


def fake_urlopen(payload):
    return lambda *a, **k: FakeResponse(json.dumps(payload).encode())


class RainFactorTests(TestCase):
    def test_levels(self):
        self.assertIsNone(rain_level(0.9, 0))
        self.assertEqual(rain_level(1.0, 0), "小雨")
        self.assertEqual(rain_level(3.0, 0), "雨")
        self.assertEqual(rain_level(8.0, 0), "大雨")
        self.assertEqual(rain_level(0.0, 0.2), "大雨")

    def test_indoor_is_never_reduced(self):
        for level in ("小雨", "雨", "大雨"):
            self.assertEqual(rain_factor(level, "室内"), 1.0)
            self.assertEqual(rain_factor(level, "配達"), 1.0)
            self.assertLess(rain_factor(level, "テーブル"), 1.0)
        self.assertEqual(rain_factor("大雨", "テーブル"), 0.60)
        self.assertEqual(rain_factor(None, "テーブル"), 1.0)

    def test_only_11_to_13_counts(self):
        payload = open_meteo_payload("2026-10-05", {9: 10.0, 11: 1.0, 12: 2.5, 13: 10.0})
        with patch("sales.weather.urllib.request.urlopen", fake_urlopen(payload)):
            w = fetch_lunch_weather()["2026-10-05"]
        self.assertEqual(w["precip"], 3.5)  # 朝9時と13時台の雨は入らない
        self.assertEqual(w["level"], "雨")
        self.assertEqual(w["feels"], 18.0)
        self.assertIsNone(w["wind"])        # 風速の無い応答でも他の値は読める

    def test_lunch_wind_is_averaged(self):
        payload = open_meteo_payload("2026-10-05", {})
        payload["hourly"]["wind_speed_10m"] = [9.0 if h in (11, 12) else 1.0 for h in range(24)]
        payload["hourly"]["wind_speed_10m"][12] = 5.0
        with patch("sales.weather.urllib.request.urlopen", fake_urlopen(payload)):
            w = fetch_lunch_weather()["2026-10-05"]
        self.assertEqual(w["wind"], 7.0)

    def test_strong_wind_only_table_and_peddler(self):
        self.assertEqual(wind_factor(6.0, "テーブル"), 0.95)
        self.assertEqual(wind_factor(8.0, "行商"), 0.95)
        self.assertEqual(wind_factor(5.9, "テーブル"), 1.0)
        self.assertEqual(wind_factor(8.0, "車"), 1.0)      # 車は答え合わせで良くならなかった
        self.assertEqual(wind_factor(8.0, "室内"), 1.0)
        self.assertEqual(wind_factor(None, "テーブル"), 1.0)


class SaveForecastCommandTests(TestCase):
    def test_saves_each_day(self):
        payload = open_meteo_payload("2026-10-05", {11: 4.0})
        with patch("sales.weather.urllib.request.urlopen", fake_urlopen(payload)):
            call_command("save_weather_forecast", stdout=io.StringIO())
        self.assertEqual(set(WeatherForecastSnapshot.objects.values_list("area", flat=True)), {"tokyo", "yokohama", "shinyokohama"})
        snap = WeatherForecastSnapshot.objects.get(area="tokyo")
        self.assertEqual(snap.target_date, date(2026, 10, 5))
        self.assertEqual(snap.lunch_precip, 4.0)
        self.assertEqual(snap.lunch_temp, 20.0)
        self.assertIsNone(snap.lunch_wind)

    def test_fetch_failure_is_an_error(self):
        with patch("sales.weather.urllib.request.urlopen", side_effect=OSError("down")):
            with self.assertRaises(Exception):
                call_command("save_weather_forecast", stdout=io.StringIO())
        self.assertEqual(WeatherForecastSnapshot.objects.count(), 0)


class RainForecastFixture(TestCase):
    """3つの店が直近4週の平日すべて100食売れた状態で、天気予報だけ差し替えて食数予測を見る。"""

    def setUp(self):
        cache.clear()
        user = get_user_model().objects.create_user("viewer", password="pw-viewer-123456")
        grant(user, "can_view_performance_by_location")
        self.client.force_login(user)
        SalesLocation.objects.create(no=1, name="テーブルの店", type="テーブル", price_type="A", service_name="")
        SalesLocation.objects.create(no=2, name="ビルの店", type="室内", price_type="A", service_name="")
        SalesLocation.objects.create(no=3, name="横浜の店", type="行商", price_type="A", service_name="",
                                     weather_area="yokohama")
        # 直近4週の平日すべて、どの店も100食売れた（残りあり＝完売補正なし）
        d = TODAY - timedelta(days=28)
        while d < TODAY:
            if d.weekday() < 5:
                for no, name in ((1, "テーブルの店"), (2, "ビルの店"), (3, "横浜の店")):
                    DailyReport.objects.create(date=d, location=name, location_no=no, total_quantity=110,
                                               total_sales_quantity=100, total_remaining=10, total_revenue=0)
            d += timedelta(days=1)

    def wx(self, precip, level, wind=None):
        return {"precip": precip, "snow": 0.0, "temp": 15, "feels": 13, "level": level, "wind": wind}

    def get(self, weather):
        with patch("lunchnetsale.views.timezone.localdate", return_value=TODAY), \
             patch("lunchnetsale.views._fetch_lunch_weather", return_value=weather):
            return self.client.get(reverse("meal_forecast"))

    def monday(self, res):
        return {l["name"]: l for l in res.context["fc"]["days"][0]["locs"]}


class MealForecastRainTests(RainForecastFixture):

    def test_heavy_rain_is_built_into_prediction_only_outdoor(self):
        res = self.get({"tokyo": {"2026-10-05": self.wx(9.0, "大雨")}, "yokohama": {"2026-10-05": self.wx(0.0, None)}})
        locs = self.monday(res)
        self.assertEqual(locs["テーブルの店"]["pred"], 60)
        self.assertEqual(locs["テーブルの店"]["base"], 100)
        self.assertEqual(locs["ビルの店"]["pred"], 100)
        day = res.context["fc"]["days"][0]
        self.assertEqual(day["total"], 260)       # 60 + 100 + 横浜は晴れで100
        self.assertEqual(day["base_total"], 300)
        self.assertContains(res, "雨補正")

    def test_each_store_uses_its_area_forecast(self):
        res = self.get({"tokyo": {"2026-10-05": self.wx(0.0, None)}, "yokohama": {"2026-10-05": self.wx(4.0, "雨")}})
        locs = self.monday(res)
        self.assertEqual(locs["横浜の店"]["pred"], 80)   # 行商・雨 0.80
        self.assertEqual(locs["テーブルの店"]["pred"], 100)
        self.assertEqual(locs["横浜の店"]["area"], "横浜みなと")

    def test_weather_table_shows_all_areas(self):
        res = self.get({"tokyo": {"2026-10-05": self.wx(1.5, "小雨")}})
        table = res.context["fc"]["weather_table"]
        self.assertEqual([r["label"] for r in table], ["東京", "横浜みなと", "新横浜"])
        self.assertEqual(table[0]["days"][0]["level"], "小雨")
        self.assertIsNone(table[1]["days"][0])          # 取れなかった地点

    def test_strong_wind_reduces_table_and_peddler(self):
        res = self.get({"tokyo": {"2026-10-05": self.wx(0.0, None, wind=7.0)},
                        "yokohama": {"2026-10-05": self.wx(0.0, None, wind=7.0)}})
        locs = self.monday(res)
        self.assertEqual(locs["テーブルの店"]["pred"], 95)
        self.assertEqual(locs["テーブルの店"]["wind_pct"], -5)
        self.assertEqual(locs["横浜の店"]["pred"], 95)
        self.assertEqual(locs["ビルの店"]["pred"], 100)
        self.assertEqual(locs["ビルの店"]["wind_pct"], 0)
        self.assertContains(res, "強風補正")
        self.assertContains(res, "補正がなければ 300 食")

    def test_rain_and_wind_together(self):
        res = self.get({"tokyo": {"2026-10-05": self.wx(4.0, "雨", wind=6.5)}})
        locs = self.monday(res)
        self.assertEqual(locs["テーブルの店"]["pred"], 76)     # 雨0.80 × 風0.95
        self.assertEqual(locs["テーブルの店"]["rain_pct"], -20)
        self.assertEqual(locs["テーブルの店"]["wind_pct"], -5)

    def test_calm_day_has_no_wind_mark(self):
        res = self.get({"tokyo": {"2026-10-05": self.wx(0.0, None, wind=3.0)}})
        self.assertEqual(self.monday(res)["テーブルの店"]["pred"], 100)
        self.assertNotContains(res, "強風補正")
        self.assertNotContains(res, "補正がなければ")
        self.assertContains(res, "取得失敗")

    def test_weather_unavailable(self):
        res = self.get({})
        self.assertEqual(res.status_code, 200)
        self.assertFalse(res.context["fc"]["days"][0]["rainy"])

    def test_staff_comment_on_first_day_only(self):
        latest = DailyReport.objects.filter(location="テーブルの店").latest("date")
        latest.food_count_setting = "明日は45でお願いします"
        latest.save()
        res = self.get({})
        days = res.context["fc"]["days"]
        c = {l["name"]: l["comment"] for l in days[0]["locs"]}
        self.assertEqual(c["テーブルの店"]["num"], 45)
        self.assertIsNone(c["ビルの店"])
        self.assertTrue(all(l["comment"] is None for l in days[1]["locs"]))
        self.assertContains(res, "明日は45でお願いします")

    def test_small_adjustment_number_is_not_badged(self):
        latest = DailyReport.objects.filter(location="テーブルの店").latest("date")
        latest.food_count_setting = "10個程増やしてください"
        latest.save()
        c = {l["name"]: l["comment"] for l in self.get({}).context["fc"]["days"][0]["locs"]}
        self.assertIsNone(c["テーブルの店"]["num"])
        self.assertEqual(c["テーブルの店"]["text"], "10個程増やしてください")


class LocationWeatherAreaSettingTests(TestCase):
    def test_area_saved_from_location_settings(self):
        staff = get_user_model().objects.create_user("boss", password="pw-boss-123456", is_staff=True)
        self.client.force_login(staff)
        loc = SalesLocation.objects.create(no=1, name="新横浜", type="室内", price_type="A", service_name="")
        res = self.client.post(reverse("shifts:admin_location_settings"),
                               {f"priority_{loc.id}": "A", f"weather_area_{loc.id}": "shinyokohama"})
        self.assertEqual(res.status_code, 302)
        loc.refresh_from_db()
        self.assertEqual(loc.weather_area, "shinyokohama")
        # 知らない値は無視して元のまま
        self.client.post(reverse("shifts:admin_location_settings"),
                         {f"priority_{loc.id}": "A", f"weather_area_{loc.id}": "osaka"})
        loc.refresh_from_db()
        self.assertEqual(loc.weather_area, "shinyokohama")


class FillReportWeatherSinceTest(TestCase):
    """--since で過去の日計表にも昼の天気を埋める（店ごとの雨の補正の計算に使う）。"""

    def test_backfill_uses_archive_for_old_days(self):
        import datetime as dt
        from django.core.management import call_command
        from sales.models import DailyReport
        old = dt.date(2025, 6, 2)
        DailyReport.objects.create(date=old, location="どこか", total_revenue=0)
        w = {"precip": 4.0, "snow": 0.0, "temp": 20.0, "feels": 19.0, "wind": 2.0}
        with patch("sales.management.commands.fill_report_weather.fetch_lunch_archive",
                   return_value={old.isoformat(): w}) as arc, \
             patch("sales.management.commands.fill_report_weather.fetch_lunch_recent", return_value={}):
            call_command("fill_report_weather", since="2025-06-01", stdout=io.StringIO())
        arc.assert_called()
        r = DailyReport.objects.get(date=old)
        self.assertEqual((r.auto_lunch_precip, r.auto_lunch_wind), (4.0, 2.0))


class AfterRainTests(RainForecastFixture):
    """室内の店は、前の営業日の昼が雨で当日が雨でない日に ×0.97。"""

    def test_indoor_store_drops_the_day_after_rain(self):
        # 金曜（今日）が雨 → 月曜は雨なし。月曜が雨 → 火曜は雨なし
        res = self.get({"tokyo": {"2026-10-02": self.wx(4.0, "雨"), "2026-10-05": self.wx(4.0, "雨"),
                                  "2026-10-06": self.wx(0.0, None)}})
        tue = {l["name"]: l for l in res.context["fc"]["days"][1]["locs"]}
        self.assertEqual(tue["ビルの店"]["pred"], 97)
        self.assertEqual(tue["ビルの店"]["after_rain_pct"], -3)
        self.assertEqual(tue["テーブルの店"]["pred"], 100)        # 外の店は対象外
        self.assertTrue(res.context["fc"]["days"][1]["after_rain"])
        mon = self.monday(res)
        self.assertEqual(mon["ビルの店"]["after_rain_pct"], 0)    # 当日も雨なら対象外
        self.assertContains(res, "雨の翌日−3%")
        self.assertContains(res, "翌−3")

    def test_no_mark_when_previous_day_dry(self):
        res = self.get({"tokyo": {"2026-10-02": self.wx(0.0, None), "2026-10-05": self.wx(0.0, None)}})
        self.assertEqual(self.monday(res)["ビルの店"]["pred"], 100)
        self.assertNotContains(res, "雨の翌日−")

    def test_past_previous_day_uses_the_store_record(self):
        from lunchnetsale.views import _prev_lunch_level
        rows = [{"date": TODAY - timedelta(days=1), "level": "小雨"}]
        # 予測する日の前の営業日が過去なら、その店の日計表の天気を見る
        self.assertEqual(_prev_lunch_level(TODAY, TODAY + timedelta(days=1), "tokyo", {}, rows), "小雨")
        self.assertIsNone(_prev_lunch_level(TODAY, TODAY + timedelta(days=1), "tokyo", {}, []))

    def test_history_rows_carry_previous_level(self):
        from sales.meal_forecast import load_history
        d1, d2 = TODAY - timedelta(days=2), TODAY - timedelta(days=1)
        DailyReport.objects.filter(location="ビルの店", date=d1).update(auto_lunch_precip=4.0)
        rows = {r["date"]: r for r in load_history(TODAY - timedelta(days=30), TODAY)["ビルの店"]}
        self.assertEqual(rows[d2]["prev_level"], "雨")
        self.assertEqual(rows[d1]["level"], "雨")
