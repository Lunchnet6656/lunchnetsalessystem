"""本日の出店状況（公開ページ用 status.json）を生成する。

公開ページ status.lunchnetsalessystem.com は Cloudflare Pages の静的サイトで、
このコマンドが吐く status.json だけを読む。Heroku は安価プランで同時接続が約20しかなく、
お客様の一斉アクセスを直接受けると社内システムごと落ちるため、お客様トラフィックは
Heroku に一切かけない（生成だけ Heroku、配信は Cloudflare）構成にしている。
"""
import json
from datetime import date
from pathlib import Path

import jpholiday
from django.conf import settings
from django.core.management.base import BaseCommand
from django.db.models import Sum
from django.utils import timezone

from sales.models import ItemQuantity, SalesLocation

WEEKDAY_JP = "月火水木金土日"


def is_business_day(d: date) -> bool:
    # 平日かつ祝日でない日のみ営業（土日祝は全店休み）
    return d.weekday() < 5 and not jpholiday.is_holiday(d)


def build_status(today: date | None = None) -> dict:
    today = today or timezone.localdate()
    weekday = WEEKDAY_JP[today.weekday()]
    generated_at = timezone.localtime().isoformat(timespec="seconds")

    if not is_business_day(today):
        return {
            "date": today.isoformat(),
            "weekday": weekday,
            "business_day": False,
            "generated_at": generated_at,
            "all_unregistered": False,
            "locations": [],
        }

    # excluded_from_shift=社内シフトから外す ／ excluded_from_public_status=公開ページから外す
    # （配達のように「シフト管理には残すが対外的に出店ではない」拠点を消すための別フラグ）
    locations = list(
        SalesLocation.objects
        .filter(excluded_from_shift=False, excluded_from_public_status=False)
        .order_by("no")
    )
    target_date = today.isoformat()  # ItemQuantity.target_date は "YYYY-MM-DD" 文字列（本番DB実データで確認・2026-05-13）
    totals = {
        row["sales_location"]: row["total"]
        for row in (
            ItemQuantity.objects.filter(target_date=target_date)
            .values("sales_location")
            .annotate(total=Sum("quantity"))
        )
    }

    rows = []
    open_count = 0
    base_open_count = 0  # all_unregistered 判定は override 反映前の素の集計で行う
    for loc in locations:
        total = totals.get(loc.id) or 0
        base_status = "open" if total >= 1 else "closed"
        if base_status == "open":
            base_open_count += 1

        # today_override は当日に設定されたものだけ有効。stale な値は無視
        # （publish_status_json が毎朝8:00にクリアするが、保険として日付チェック）
        if loc.today_override and loc.today_override_date == today:
            status = loc.today_override  # "sold_out" or "closed"
        else:
            status = base_status

        if status == "open":
            open_count += 1
        rows.append({"no": loc.no, "name": loc.name, "status": status})

    return {
        "date": today.isoformat(),
        "weekday": weekday,
        "business_day": True,
        "generated_at": generated_at,
        # 営業日なのに全拠点0＝持参数が朝までに登録されていない可能性。
        # ページ側はこれを見て「全店お休み」と断定せず「準備中」と表示する。
        # （overrideが入っていても素の集計が0なら未登録扱い＝ページは「準備中」を表示）
        "all_unregistered": base_open_count == 0,
        "locations": rows,
    }


class Command(BaseCommand):
    help = "本日の出店状況を集計して status.json を生成する（公開ページ用）"

    def add_arguments(self, parser):
        parser.add_argument(
            "--output",
            default=str(Path(settings.BASE_DIR) / "status.json"),
            help="出力先パス（デフォルト: <BASE_DIR>/status.json）",
        )
        # NOTE: --stdout / --stderr は BaseCommand が予約済みなので使わない
        parser.add_argument(
            "--echo",
            action="store_true",
            help="ファイルに書かず標準出力に JSON を出す",
        )

    def handle(self, *args, **options):
        data = build_status()
        text = json.dumps(data, ensure_ascii=False, indent=2) + "\n"

        if options["echo"]:
            self.stdout.write(text)
            return

        out = Path(options["output"])
        out.write_text(text, encoding="utf-8")

        open_count = sum(1 for loc in data["locations"] if loc["status"] == "open")
        sold_out_count = sum(1 for loc in data["locations"] if loc["status"] == "sold_out")
        closed_count = len(data["locations"]) - open_count - sold_out_count
        self.stdout.write(
            f"[generate_status_json] {data['date']} ({data['weekday']}) "
            f"business_day={data['business_day']} open={open_count} "
            f"sold_out={sold_out_count} closed={closed_count} "
            f"all_unregistered={data['all_unregistered']} -> {out}"
        )
