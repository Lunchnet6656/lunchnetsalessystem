"""昼どきの天気予報を保存する（Heroku Scheduler：D-2の夜 20:00 と D-1の昼 15:00 JST）。

食数を決めたときに見えていた予報を残しておかないと、あとで「予報で決めたらどうだったか」を
検証するときに実績の天気を使うしかなく、良く見えすぎてしまう。
"""
from django.core.management.base import BaseCommand, CommandError

from sales.models import WeatherForecastSnapshot
from sales.weather import WEATHER_AREAS, fetch_lunch_weather


class Command(BaseCommand):
    help = "Open-Meteo の昼11〜13時の予報（7日分・地点ごと）を保存する"

    def handle(self, *args, **options):
        snapshots, failed = [], []
        for area in WEATHER_AREAS:
            try:
                days = fetch_lunch_weather(area)
            except Exception as e:
                failed.append(f"{area}: {e}")
                continue
            snapshots += [
                WeatherForecastSnapshot(
                    target_date=day, area=area, lunch_precip=w["precip"], lunch_snow=w["snow"],
                    lunch_temp=w["temp"], lunch_feels=w["feels"],
                )
                for day, w in sorted(days.items())
            ]
        WeatherForecastSnapshot.objects.bulk_create(snapshots)
        if failed and not snapshots:
            raise CommandError("天気予報を取得できませんでした: " + " / ".join(failed))
        self.stdout.write(f"{len(snapshots)}件の予報を保存しました" + (f"（取得できなかった地点: {', '.join(failed)}）" if failed else ""))
