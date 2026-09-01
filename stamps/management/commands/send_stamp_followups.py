"""狙い撃ち配信（お礼／次回後押し／離反フォロー）を日次で送るコマンド。

仕様: .company/engineering/harness/specs/lunchnetsale-スタンプ狙い撃ち配信-要件定義.md
Heroku Scheduler に日次登録して回す。設定は「配信」画面（MessageScenario）で運用する。

  python manage.py send_stamp_followups            # 本日ぶんを判定・送信＋再来の効果更新
  python manage.py send_stamp_followups --dry-run  # 送信せず対象数だけ表示
  python manage.py send_stamp_followups --date 2026-08-20  # 指定日で判定（検証/補完）
"""
from datetime import datetime

from django.core.management.base import BaseCommand
from django.utils import timezone

from stamps import messaging
from stamps.models import MessageScenario


class Command(BaseCommand):
    help = "スタンプ会員へ狙い撃ち配信（お礼/次回後押し/離反フォロー）を送る"

    def add_arguments(self, parser):
        parser.add_argument("--dry-run", action="store_true", help="送信せず対象数のみ表示")
        parser.add_argument("--date", type=str, default=None, help="判定日 YYYY-MM-DD（既定=本日）")

    def handle(self, *args, **options):
        day = None
        if options["date"]:
            try:
                day = datetime.strptime(options["date"], "%Y-%m-%d").date()
            except ValueError:
                self.stderr.write("--date は YYYY-MM-DD で指定してください。")
                return
        day = day or timezone.localdate()
        dry = options["dry_run"]

        summary = messaging.run_followups(today=day, dry_run=dry)
        label = {c[0]: c[1] for c in MessageScenario.KIND_CHOICES}
        head = "[DRY-RUN] " if dry else ""
        for kind, r in summary.items():
            self.stdout.write(
                f"{head}{label.get(kind, kind)}: 対象{r['targets']} "
                f"送信{r['sent']} 対照{r['held_out']} 失敗{r['failed']} スキップ{r['skipped']}")

        if not dry:
            updated = messaging.update_revisits(today=day)
            self.stdout.write(f"効果測定：再来フラグ更新 {updated}件")
