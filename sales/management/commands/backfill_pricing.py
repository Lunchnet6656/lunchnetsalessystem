"""過去の日計表に「保存時点の単価」と「割引明細」を埋め、保存済みの金額と照合する。

    python manage.py backfill_pricing                       # 照合だけ（書き込まない）
    python manage.py backfill_pricing --csv 照合結果.csv     # 一致しない日計表をCSVに出す
    python manage.py backfill_pricing --coupon-650-since 2025-05-12   # 600→650の切り替え日を仮定して照合
    python manage.py backfill_pricing --apply               # 書き込む（営業時間外に）

照合（一致しなくても自動では直さない。保存済みの金額も書き換えない）：
- 割引：旧カラムから作った割引明細の合計 ＝ 保存済みの total_discount か
- 売上：明細の売上合計 ＋ その他売上 ＋ 割引合計 ＝ 保存済みの total_revenue か
- 単価：単価 × 販売数 ＝ 明細の売上 か
何度実行しても同じ結果になる（単価は空の明細だけ埋め、割引明細は作り直す）。
"""
import csv
import datetime
from collections import Counter

from django.core.management.base import BaseCommand
from django.db import transaction

from sales.discounts import (
    COUPON_650_SINCE, SERVICE_FLAT_LABEL, items_on, legacy_lines, save_lines, service_price_of,
    total_discount,
)
from sales.models import DailyReport, DailyReportEntry, ItemQuantity, SalesLocation
from sales.pricing import PriceBook, pattern_of


def _date(text):
    return datetime.datetime.strptime(text, "%Y-%m-%d").date()


class Command(BaseCommand):
    help = "過去の日計表に単価と割引明細を埋め、保存済みの金額と照合する（既定は照合のみ）"

    def add_arguments(self, parser):
        parser.add_argument("--apply", action="store_true", help="単価と割引明細を書き込む")
        parser.add_argument("--csv", help="一致しない日計表を書き出すCSVのパス")
        parser.add_argument("--since", type=_date, help="この日以降の日計表だけ（YYYY-MM-DD）")
        parser.add_argument("--until", type=_date, help="この日までの日計表だけ（YYYY-MM-DD）")
        parser.add_argument("--coupon-650-since", type=_date, default=COUPON_650_SINCE,
                            help="クーポン・サービスの600円欄を650円として数え始める日")

    def handle(self, *args, **opts):
        reports = DailyReport.objects.order_by("date", "location").prefetch_related("entries")
        if opts["since"]:
            reports = reports.filter(date__gte=opts["since"])
        if opts["until"]:
            reports = reports.filter(date__lte=opts["until"])

        patterns = {loc.name: pattern_of(loc) for loc in SalesLocation.objects.all()}
        books, items_cache = {}, {}
        rows, by_month = [], Counter()
        stats = Counter()

        for report in reports.iterator(chunk_size=200):
            stats["reports"] += 1
            items = items_cache.setdefault(report.date, list(items_on(report.date)))
            stored_discount = int(report.total_discount or 0)
            lines = legacy_lines(report, items, opts["coupon_650_since"], infer_from_total=stored_discount)
            discount = total_discount(report, lines)
            if report.service_type_100 and service_price_of(report) == 0 and any(
                    l.label == SERVICE_FLAT_LABEL and l.unit_amount for l in lines):
                stats["service_inferred"] += 1

            entries = list(report.entries.all())
            filled = self._fill_unit_prices(report, entries, patterns, books, stats)
            sales_total = sum(int(e.total_sales or 0) for e in entries)
            revenue = sales_total + int(report.total_others_sales or 0) + stored_discount
            stored_revenue = int(report.total_revenue or 0)
            bad_units = [e for e in entries if e.unit_price is not None
                         and int(e.unit_price) * int(e.sales_quantity or 0) != int(e.total_sales or 0)]

            problems, kind = [], ""
            if discount != stored_discount:
                problems.append(f"割引 明細{discount} / 保存{stored_discount}")
                if stored_discount == 0 and discount > 0:
                    # 全角「＋」を parse_value が読めず0円で保存していた（2026-03-23 の送信前の記号除去で解消）
                    kind = "説明済み：プラスの割引が0円で保存"
                    stats["discount_plus_zero"] += 1
                    revenue = sales_total + int(report.total_others_sales or 0) + discount
                else:
                    kind = "要確認"
                    stats["discount_mismatch"] += 1
                    by_month[report.date.strftime("%Y-%m")] += 1
            if revenue != stored_revenue:
                problems.append(f"売上 計算{revenue} / 保存{stored_revenue}")
                kind = kind or "要確認"
                stats["revenue_mismatch"] += 1
            if bad_units:
                problems.append("単価×販売数≠売上：" + "、".join(f"No.{e.product_no}" for e in bad_units))
                stats["unit_mismatch"] += 1
            if problems:
                rows.append([report.id, report.date, report.location, kind, " ／ ".join(problems)])

            if opts["apply"]:
                with transaction.atomic():
                    if filled:
                        DailyReportEntry.objects.bulk_update(filled, ["unit_price"])
                    save_lines(report, lines)

        self._print_summary(stats, by_month, opts)
        if opts["csv"]:
            with open(opts["csv"], "w", newline="", encoding="utf-8-sig") as f:
                writer = csv.writer(f)
                writer.writerow(["日計表ID", "日付", "販売所", "区分", "一致しない内容"])
                writer.writerows(rows)
            self.stdout.write(f"一致しない日計表 {len(rows)} 件を {opts['csv']} に書き出しました")

    def _fill_unit_prices(self, report, entries, patterns, books, stats):
        """単価が空の明細に、売上÷販売数（販売数0ならその日の値段）を入れる。入れた明細を返す。"""
        filled = []
        for entry in entries:
            if entry.unit_price is not None:
                continue
            if entry.sales_quantity:
                entry.unit_price = int(entry.total_sales or 0) // int(entry.sales_quantity)
            else:
                entry.unit_price = self._price_on_day(report, entry, patterns, books)
                if entry.unit_price is None:
                    stats["unit_unknown"] += 1
                    continue
            filled.append(entry)
            stats["units_filled"] += 1
        return filled

    def _price_on_day(self, report, entry, patterns, books):
        iq = (ItemQuantity.objects.select_related("product")
              .filter(target_date=report.date.isoformat(), sales_location__name=report.location,
                      product__no=entry.product_no).first())
        pattern = patterns.get(report.location)
        if iq is None or pattern is None:
            return None
        book = books.setdefault(report.date, PriceBook(report.date))
        return book.price(iq.product, pattern)

    def _print_summary(self, stats, by_month, opts):
        mode = "書き込みました" if opts["apply"] else "照合のみ（書き込みなし）"
        self.stdout.write(f"== backfill_pricing：{mode} ==")
        self.stdout.write(f"600円欄を650円として数え始める日：{opts['coupon_650_since']}")
        self.stdout.write(f"日計表 {stats['reports']} 件")
        self.stdout.write(f"  単価を埋めた明細 {stats['units_filled']} 件／単価が決められない明細 {stats['unit_unknown']} 件")
        self.stdout.write(f"  サービス割引の価格を割引合計から逆算 {stats['service_inferred']} 件")
        self.stdout.write(f"  割引：プラスの割引が0円で保存（説明済み） {stats['discount_plus_zero']} 件")
        self.stdout.write(f"  割引が一致しない {stats['discount_mismatch']} 件")
        self.stdout.write(f"  売上が一致しない {stats['revenue_mismatch']} 件")
        self.stdout.write(f"  単価×販売数≠売上 {stats['unit_mismatch']} 件")
        if by_month:
            self.stdout.write("  割引が一致しない件数（月別）：")
            for month, count in sorted(by_month.items()):
                self.stdout.write(f"    {month}: {count}")
