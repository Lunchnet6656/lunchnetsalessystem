"""昼どきの天気予報を保存する（Heroku Scheduler：D-2の夜 20:00 と D-1の昼 15:00 JST）。

食数を決めたときに見えていた予報を残しておかないと、あとで「予報で決めたらどうだったか」を
検証するときに実績の天気を使うしかなく、良く見えすぎてしまう。
"""
from django.core.management.base import BaseCommand, CommandError

from sales.models import WeatherForecastSnapshot
from sales.weather import fetch_lunch_weather


class Command(BaseCommand):
    help = "Open-Meteo の昼11〜13時の予報（7日分）を保存する"

    def handle(self, *args, **options):
        try:
            days = fetch_lunch_weather()
        except Exception as e:
            raise CommandError(f"天気予報を取得できませんでした: {e}")
        WeatherForecastSnapshot.objects.bulk_create([
            WeatherForecastSnapshot(
                target_date=day, lunch_precip=w["precip"], lunch_snow=w["snow"],
                lunch_temp=w["temp"], lunch_feels=w["feels"],
            )
            for day, w in sorted(days.items())
        ])
        self.stdout.write(f"{len(days)}日分の予報を保存しました")
