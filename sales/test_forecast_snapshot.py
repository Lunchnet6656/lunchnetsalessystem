"""食数予測の数字の記録（save_meal_forecast）のテスト。"""
import io
import json
from datetime import date, timedelta
from unittest.mock import patch

from django.core.cache import cache
from django.core.management import call_command
from django.test import TestCase

from sales.models import DailyReport, ForecastSnapshot, QuantityDraft, SalesLocation

TODAY = date(2026, 10, 2)   # 金曜。次の営業日は 10/5（月）


class ForecastSnapshotTests(TestCase):
    def setUp(self):
        cache.clear()
        SalesLocation.objects.create(no=1, name="外の店", type="車", price_type="A", service_name="")
        SalesLocation.objects.create(no=2, name="ビルの店", type="室内", price_type="A", service_name="")
        d = TODAY - timedelta(days=28)
        while d < TODAY:
            if d.weekday() < 5:
                for no, name in ((1, "外の店"), (2, "ビルの店")):
                    DailyReport.objects.create(date=d, location=name, location_no=no, total_quantity=105,
                                               total_sales_quantity=100, total_remaining=5, total_revenue=0)
            d += timedelta(days=1)
        QuantityDraft.objects.create(target_date=date(2026, 10, 5), location="ビルの店", quantity=120)

    def run_cmd(self, weather):
        with patch("lunchnetsale.views.timezone.localdate", return_value=TODAY), \
             patch("sales.management.commands.save_meal_forecast.timezone.localdate", return_value=TODAY), \
             patch("lunchnetsale.views._fetch_lunch_weather", return_value=weather):
            call_command("save_meal_forecast", stdout=io.StringIO())

    def test_saves_what_the_page_shows(self):
        rain = {"precip": 4.0, "snow": 0.0, "temp": 15, "feels": 13, "level": "雨"}
        self.run_cmd({"tokyo": {"2026-10-05": rain}})
        rows = ForecastSnapshot.objects.filter(target_date=date(2026, 10, 5))
        snap = {r.location: r for r in rows}
        self.assertEqual(snap["外の店"].pred, 85)       # 車・雨 0.85
        self.assertEqual(snap["外の店"].base, 100)
        self.assertEqual(snap["外の店"].rain_level, "雨")
        self.assertEqual(snap["ビルの店"].pred, 100)    # 室内は減らさない
        self.assertEqual(snap["ビルの店"].draft, 120)
        self.assertIsNone(snap["外の店"].draft)
        # 3営業日 × 2店
        self.assertEqual(ForecastSnapshot.objects.count(), 6)

    def test_each_run_is_kept(self):
        self.run_cmd({})
        self.run_cmd({})
        self.assertEqual(ForecastSnapshot.objects.filter(target_date=date(2026, 10, 5), location="外の店").count(), 2)

    def test_weather_job_also_saves_forecast(self):
        times = [f"2026-10-05T{h:02d}:00" for h in range(24)]
        payload = {"hourly": {"time": times, "precipitation": [0.0] * 24, "snowfall": [0.0] * 24,
                              "temperature_2m": [20.0] * 24, "apparent_temperature": [18.0] * 24}}

        class Resp(io.BytesIO):
            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

        with patch("sales.weather.urllib.request.urlopen", lambda *a, **k: Resp(json.dumps(payload).encode())), \
             patch("lunchnetsale.views.timezone.localdate", return_value=TODAY), \
             patch("sales.management.commands.save_meal_forecast.timezone.localdate", return_value=TODAY):
            call_command("save_weather_forecast", stdout=io.StringIO())
        self.assertTrue(ForecastSnapshot.objects.exists())

    def test_forecast_failure_does_not_break_weather_save(self):
        from sales.models import WeatherForecastSnapshot
        times = [f"2026-10-05T{h:02d}:00" for h in range(24)]
        payload = {"hourly": {"time": times, "precipitation": [0.0] * 24, "snowfall": [0.0] * 24,
                              "temperature_2m": [20.0] * 24, "apparent_temperature": [18.0] * 24}}

        class Resp(io.BytesIO):
            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

        with patch("sales.weather.urllib.request.urlopen", lambda *a, **k: Resp(json.dumps(payload).encode())), \
             patch("lunchnetsale.views._meal_forecast", side_effect=RuntimeError("boom")):
            call_command("save_weather_forecast", stdout=io.StringIO(), stderr=io.StringIO())
        self.assertTrue(WeatherForecastSnapshot.objects.exists())
        self.assertFalse(ForecastSnapshot.objects.exists())
