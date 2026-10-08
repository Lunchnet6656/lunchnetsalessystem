"""明日（水曜）から始まる週のメニューが確認済みでなければ、しょうへいのLINEへ知らせる。

Heroku Scheduler で毎日 19:30 JST（10:30 UTC）に実行し、火曜だけ動く（持参数の未受信通知と同じ時刻）。
メニューがまだ届いていない週も知らせる。
仕様: .company/engineering/harness/specs/lunchnetsale-価格と割引のマスタ化-要件定義.md §8.3・§8.6
"""
import datetime

from django.conf import settings
from django.core.management.base import BaseCommand
from django.utils import timezone

from reservations.line_notify import push_text
from sales.menu_registry import LARGE_RICE_NO
from sales.menu_week import week_products
from sales.models import MenuProfile, MenuWeekCheck

NOTIFY_FROM_HOUR = 17   # Scheduler の AM/PM の取り違えで朝に送らないように（持参数の通知と同じ）
TUESDAY = 1


def build_message(week, products, check):
    label = f"{week.month}/{week.day}(水)週"
    if not products:
        return "\n".join([
            f"【メニュー 未受信】{label}のメニューがまだアプリに届いていません。",
            "元のメニュー表を仕上げたら、「メニュー送信.xlsm」の[メニュー送信]を押してください。",
        ])
    names = [p.name for p in products if p.no != LARGE_RICE_NO]   # 大盛りごはんはアプリが足す行なので数えない
    needs = MenuProfile.objects.filter(name__in=names, confirmed=False).count()
    return "\n".join([
        f"【メニュー 未確認】{label}のメニューがまだ確認されていません（要確認 {needs}品）。",
        "LSSの「週のメニュー確認」で種類・容器・値段を確かめて、「確認しました」を押してください。",
    ])


class Command(BaseCommand):
    help = "明日から始まる週のメニューが未確認ならLINEで通知する（火曜 19:30 JST）"

    def add_arguments(self, parser):
        parser.add_argument("--dry-run", action="store_true", help="送らずに内容だけ表示")

    def handle(self, *args, **options):
        now = timezone.localtime()
        if now.hour < NOTIFY_FROM_HOUR:
            self.stdout.write(f"{now:%H:%M} は早いのでスキップ（{NOTIFY_FROM_HOUR}時以降に実行してください）")
            return
        today = timezone.localdate()
        if today.weekday() != TUESDAY:
            self.stdout.write(f"{today} は火曜ではないためスキップ")
            return

        week = today + datetime.timedelta(days=1)
        products = week_products(week)
        check = MenuWeekCheck.objects.filter(week=week).first()
        if products and check and check.is_confirmed:
            self.stdout.write(f"{week} 週は確認済み")
            return
        if products and not any(p.rank_id for p in products):
            self.stdout.write(f"{week} 週は古い登録方法（Excelアップロード）のため対象外")
            return

        message = build_message(week, products, check)
        if options["dry_run"]:
            self.stdout.write(message)
            return
        if push_text(settings.OWNER_LINE_USER_ID, message):
            self.stdout.write(f"{week} 週の未確認を通知しました")
        else:
            self.stderr.write("LINE通知に失敗しました（OWNER_LINE_USER_ID / LINE_CHANNEL_ACCESS_TOKEN を確認）")
