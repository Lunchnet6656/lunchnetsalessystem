"""次の営業日分の持参数が届いていなければ、しょうへいのLINEへ知らせる。

Heroku Scheduler で毎日 19:30 JST（10:30 UTC）に実行。持参数の最終決定は
「次の営業日の前の営業日」の16〜19時なので、今日が営業日のときだけ動く
（金曜は月曜分、連休前は連休明け分を見る）。
"""
from django.conf import settings
from django.core.management.base import BaseCommand
from django.utils import timezone

from reservations.line_notify import push_text
from sales.item_quantity_import import is_business_day, next_business_day
from sales.models import ItemQuantityUpload

WEEKDAY_JP = "月火水木金土日"


def build_message(target_date, last_failed):
    label = f"{target_date.month}/{target_date.day}({WEEKDAY_JP[target_date.weekday()]})"
    if last_failed:
        lines = [
            f"【持参数 未登録】{label}分の最後の送信が止められています。",
            "直してから振分表の[確定して送る]をもう一度押してください。",
        ]
    else:
        lines = [
            f"【持参数 未受信】{label}分の持参数がまだアプリに届いていません。",
            "最終決定が終わったら、振分表の[確定して送る]を押してください。",
        ]
    if last_failed:
        lines += ["", f"{timezone.localtime(last_failed.received_at):%H:%M} の送信は止めました："]
        lines += [f"・{e}" for e in last_failed.errors[:5]]
    return "\n".join(lines)


class Command(BaseCommand):
    help = "次の営業日分の持参数が未受信ならLINEで通知する（19:30 JST 実行）"

    def add_arguments(self, parser):
        parser.add_argument("--dry-run", action="store_true", help="送らずに内容だけ表示")

    def handle(self, *args, **options):
        today = timezone.localdate()
        if not is_business_day(today):
            self.stdout.write(f"{today} は営業日ではないためスキップ")
            return

        target = next_business_day(today)
        # 最後の送信で判定する。一度届いていても、その後の確定し直しが止められていたら知らせたい
        latest = ItemQuantityUpload.objects.filter(target_date=target).first()
        if latest and latest.ok:
            self.stdout.write(f"{target} 分は受信済み")
            return

        message = build_message(target, latest)
        if options["dry_run"]:
            self.stdout.write(message)
            return
        if push_text(settings.OWNER_LINE_USER_ID, message):
            self.stdout.write(f"{target} 分の未受信を通知しました")
        else:
            self.stderr.write("LINE通知に失敗しました（OWNER_LINE_USER_ID / LINE_CHANNEL_ACCESS_TOKEN を確認）")
