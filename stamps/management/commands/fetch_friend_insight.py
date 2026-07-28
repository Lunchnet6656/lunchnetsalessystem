"""LINE公式アカウントの友だち統計（Insight: followers）を日次取得して保存する。

仕様: .company/engineering/harness/specs/lunchnetスタンプ友だち分析-Insight連携.md

毎日1回（Heroku Scheduler）実行する想定。**前日ぶん**を取得して
FriendInsightSnapshot に upsert する。友だち数・ブロック数の推移可視化と
ダッシュボード参加率（全友だちベース）の母数に使う。

使い方：
  manage.py fetch_friend_insight                 # 前日ぶんを取得・保存
  manage.py fetch_friend_insight --date 2026-07-27   # 指定日を取得（検証/バックフィル）
  manage.py fetch_friend_insight --days 30       # 前日から遡って30日ぶんを一括バックフィル
  manage.py fetch_friend_insight --dry-run       # 保存せず取得結果だけ表示
"""
from datetime import datetime, timedelta

from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from stamps import line_insight


class Command(BaseCommand):
    help = "LINE Insight(followers) を取得して友だち統計スナップショットを保存する。"

    def add_arguments(self, parser):
        parser.add_argument("--date", help="取得する集計日（YYYY-MM-DD）。既定は前日。")
        parser.add_argument("--days", type=int, help="前日から遡ってこの日数を一括バックフィルする。")
        parser.add_argument("--dry-run", action="store_true", help="保存せず取得結果を表示。")

    def handle(self, *args, **opts):
        # --days：前日から遡って一括バックフィル（1日ずつ save）。
        if opts.get("days"):
            end = timezone.localdate() - timedelta(days=1)
            ok = warn = 0
            for i in range(opts["days"]):
                day = end - timedelta(days=i)
                try:
                    obj = line_insight.save_friend_insight(day)
                except line_insight.InsightError as e:
                    raise CommandError(f"{day}：{e}")
                if obj.is_ready:
                    ok += 1
                else:
                    warn += 1
                    self.stdout.write(self.style.WARNING(f"{day}：集計未確定（{obj.status}）"))
            self.stdout.write(self.style.SUCCESS(
                f"バックフィル完了：保存{ok}日 / 集計待ち{warn}日（{opts['days']}日ぶん）"))
            return

        if opts.get("date"):
            try:
                target = datetime.strptime(opts["date"], "%Y-%m-%d").date()
            except ValueError:
                raise CommandError("--date は YYYY-MM-DD 形式で指定してください。")
        else:
            target = timezone.localdate() - timedelta(days=1)

        try:
            if opts.get("dry_run"):
                d = line_insight.fetch_friend_insight(target)
                self.stdout.write(
                    f"[dry-run] {target} status={d['status']} "
                    f"followers={d['followers']} targeted_reaches={d['targeted_reaches']} "
                    f"blocks={d['blocks']}"
                )
                return
            obj = line_insight.save_friend_insight(target)
        except line_insight.InsightError as e:
            raise CommandError(str(e))

        if not obj.is_ready:
            self.stdout.write(self.style.WARNING(
                f"{target}：集計未確定（status={obj.status}）。翌日以降に再取得してください。"))
        else:
            self.stdout.write(self.style.SUCCESS(
                f"{target}：保存しました 友だち{obj.effective_friends}／"
                f"ブロック{obj.blocks}（累計追加{obj.followers}）"))
