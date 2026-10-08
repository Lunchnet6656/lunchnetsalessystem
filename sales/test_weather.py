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
from sales.weather import fetch_lunch_weather, rain_factor, rain_level

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


class SaveForecastCommandTests(TestCase):
    def test_saves_each_day(self):
        payload = open_meteo_payload("2026-10-05", {11: 4.0})
        with patch("sales.weather.urllib.request.urlopen", fake_urlopen(payload)):
            call_command("save_weather_forecast", stdout=io.StringIO())
        snap = WeatherForecastSnapshot.objects.get()
        self.assertEqual(snap.target_date, date(2026, 10, 5))
        self.assertEqual(snap.lunch_precip, 4.0)
        self.assertEqual(snap.lunch_temp, 20.0)

    def test_fetch_failure_is_an_error(self):
        with patch("sales.weather.urllib.request.urlopen", side_effect=OSError("down")):
            with self.assertRaises(Exception):
                call_command("save_weather_forecast", stdout=io.StringIO())
        self.assertEqual(WeatherForecastSnapshot.objects.count(), 0)


class MealForecastRainTests(TestCase):
    def setUp(self):
        cache.clear()
        user = get_user_model().objects.create_user("viewer", password="pw-viewer-123456")
        self.client.force_login(user)
        SalesLocation.objects.create(no=1, name="テーブルの店", type="テーブル", price_type="A", service_name="")
        SalesLocation.objects.create(no=2, name="ビルの店", type="室内", price_type="A", service_name="")
        # 直近4週の平日すべて、どちらも100食売れた（残りあり＝完売補正なし）
        d = TODAY - timedelta(days=28)
        while d < TODAY:
            if d.weekday() < 5:
                for no, name in ((1, "テーブルの店"), (2, "ビルの店")):
                    DailyReport.objects.create(date=d, location=name, location_no=no, total_quantity=110,
                                               total_sales_quantity=100, total_remaining=10, total_revenue=0)
            d += timedelta(days=1)

    def get(self, weather):
        with patch("lunchnetsale.views.timezone.localdate", return_value=TODAY), \
             patch("lunchnetsale.views._fetch_lunch_weather", return_value=weather):
            return self.client.get(reverse("meal_forecast"))

    def monday(self, res):
        return res.context["fc"]["days"][0]

    def test_heavy_rain_reduces_only_outdoor(self):
        res = self.get({"2026-10-05": {"precip": 9.0, "snow": 0.0, "temp": 15, "feels": 13, "level": "大雨"}})
        day = self.monday(res)
        adj = {l["name"]: l["adj"] for l in day["locs"]}
        self.assertEqual(adj["テーブルの店"], 60)
        self.assertEqual(adj["ビルの店"], 100)
        self.assertEqual(day["adj_total"], 160)
        self.assertContains(res, "室内は減らしません")
        self.assertContains(res, "テーブル 0.60倍")

    def test_light_rain_also_shows_guidance(self):
        day = self.monday(self.get({"2026-10-05": {"precip": 1.5, "snow": 0.0, "temp": 15, "feels": 13, "level": "小雨"}}))
        self.assertEqual(day["rain"], "小雨")
        self.assertEqual({l["name"]: l["adj"] for l in day["locs"]}["テーブルの店"], 85)

    def test_no_rain_no_guidance(self):
        res = self.get({"2026-10-05": {"precip": 0.2, "snow": 0.0, "temp": 15, "feels": 13, "level": None}})
        self.assertIsNone(self.monday(res)["rain"])
        self.assertNotContains(res, "雨の日の目安")

    def test_weather_unavailable(self):
        res = self.get({})
        self.assertEqual(res.status_code, 200)
        self.assertIsNone(self.monday(res)["rain"])
