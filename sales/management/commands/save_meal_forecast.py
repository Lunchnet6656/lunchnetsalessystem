"""食数予測の画面に出る数字を、そのまま記録する。

save_weather_forecast（Scheduler：15時と20時）の最後に呼ばれる。決めた時点の予測を
残しておけば、あとで「予測と結果がどれだけずれたか」を当時の数字で答え合わせできる。
"""
from django.core.management.base import BaseCommand
from django.utils import timezone

from sales.models import ForecastSnapshot


class Command(BaseCommand):
    help = "食数予測（次の3営業日・店ごと）を記録する"

    def handle(self, *args, **options):
        from lunchnetsale.views import _meal_forecast

        fc = _meal_forecast(timezone.localdate())
        rows = []
        for day in fc["days"]:
            for l in day["locs"]:
                rows.append(ForecastSnapshot(
                    target_date=day["date"], location=l["name"],
                    pred=l["pred"], base=l["base"], recent=l["recent"],
                    weekday_pct=l["weekday_pct"], trend_pct=l["trend_pct"],
                    rain_level=l["rain"] or "", rain_pct=l["rain_pct"],
                    draft=l["draft"]["draft"] if l["draft"] else None,
                    comment_num=l["comment"]["num"] if l["comment"] else None,
                ))
        ForecastSnapshot.objects.bulk_create(rows)
        self.stdout.write(f"{len(rows)}件の予測を記録しました（{len(fc['days'])}営業日分）")
