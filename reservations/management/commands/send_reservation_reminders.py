"""前日リマインドの送信（S3-B・FR10）。

毎日1回（前日の夕方など）実行する想定。**翌日が受取日**の「受付済」予約に、LINEで前日リマインドを送る。
`reminder_sent` フラグで二重送信を防ぐ（同日に複数回流れても再送しない）。

使い方：
  manage.py send_reservation_reminders            # 翌日受取分へ送信
  manage.py send_reservation_reminders --date 2026-06-12  # 指定受取日分へ送信（検証用）
  manage.py send_reservation_reminders --dry-run  # 送らず対象だけ表示
"""
from datetime import datetime, timedelta

from django.core.management.base import BaseCommand
from django.utils import timezone

from reservations import line_notify
from reservations.models import Reservation


class Command(BaseCommand):
    help = "翌日受取の予約に前日リマインドを送る"

    def add_arguments(self, parser):
        parser.add_argument("--date", help="受取日(YYYY-MM-DD)。省略時は翌日")
        parser.add_argument("--dry-run", action="store_true", help="送信せず対象だけ表示")

    def handle(self, *args, **options):
        if options.get("date"):
            target = datetime.strptime(options["date"], "%Y-%m-%d").date()
        else:
            target = timezone.localdate() + timedelta(days=1)

        qs = (Reservation.objects
              .filter(pickup_date=target,
                      status=Reservation.STATUS_RECEIVED,
                      reminder_sent=False)
              .select_related("member", "sales_location")
              .prefetch_related("items"))

        total = qs.count()
        self.stdout.write(f"対象：{target} 受取の未送信予約 {total} 件")
        if options.get("dry_run"):
            for r in qs:
                self.stdout.write(f"  - {r.member.name} / {r.sales_location.name} / No.{r.reservation_number}")
            return

        if not line_notify.is_configured():
            self.stdout.write(self.style.WARNING(
                "LINE_CHANNEL_ACCESS_TOKEN 未設定のため送信できません（フラグも更新しません）。"))
            return

        sent = 0
        for r in qs:
            if line_notify.notify_reservation_reminder(r):
                r.reminder_sent = True
                r.save(update_fields=["reminder_sent"])
                sent += 1
        self.stdout.write(self.style.SUCCESS(f"送信完了：{sent}/{total} 件"))
