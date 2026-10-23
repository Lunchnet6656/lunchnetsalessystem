"""日計表に、その店の地点の昼（11〜13時）の天気の実際の値を添える。

送信のたびに天気を取りに行くと送信が遅くなるので、save_weather_forecast（15時・20時）の最後で
まだ入っていない直近7日分をまとめて埋める。昼が終わっていない今日の分は埋めない。
--since を付けると、その日以降の過去分も過去データ（archive）で埋める（店ごとの雨の補正の計算に使う・一度だけ）。
"""
from datetime import date, timedelta

from django.core.management.base import BaseCommand
from django.utils import timezone

from sales.models import DailyReport, SalesLocation
from sales.weather import DEFAULT_AREA, WEATHER_AREAS, fetch_lunch_archive, fetch_lunch_recent


class Command(BaseCommand):
    help = "日計表の昼の天気（雨量・気温・体感温度・風速・降雪量）を自動で埋める"

    def add_arguments(self, parser):
        parser.add_argument("--since", help="この日（YYYY-MM-DD）以降の過去分も埋める")

    def handle(self, *args, **options):
        now = timezone.localtime()
        today = now.date()
        last_day = today if now.hour >= 14 else today - timedelta(days=1)
        since = date.fromisoformat(options["since"]) if options.get("since") else today - timedelta(days=7)
        targets = list(DailyReport.objects.filter(date__gte=since, date__lte=last_day,
                                                  auto_lunch_precip__isnull=True))
        if not targets:
            self.stdout.write("埋める日計表はありません")
            return
        areas = dict(SalesLocation.objects.values_list("name", "weather_area"))
        cache, filled = {}, 0
        for report in targets:
            area = areas.get(report.location, DEFAULT_AREA)
            if area not in WEATHER_AREAS:
                area = DEFAULT_AREA
            if area not in cache:
                cache[area] = {}
                if since < today - timedelta(days=7):
                    try:
                        cache[area].update(fetch_lunch_archive(area, since, today - timedelta(days=6)))
                    except Exception as e:
                        self.stderr.write(f"{area}: 過去の天気を取得できませんでした: {e}")
                try:
                    cache[area].update(fetch_lunch_recent(area))
                except Exception as e:
                    self.stderr.write(f"{area}: 天気を取得できませんでした: {e}")
            w = cache[area].get(report.date.isoformat())
            if not w:
                continue
            DailyReport.objects.filter(pk=report.pk).update(
                auto_lunch_precip=w["precip"], auto_lunch_snow=w["snow"], auto_lunch_temp=w["temp"],
                auto_lunch_feels=w["feels"], auto_lunch_wind=w["wind"],
            )
            filled += 1
        self.stdout.write(f"{filled}件の日計表に昼の天気を添えました")
