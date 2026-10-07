"""移行コマンド backfill_pricing のテスト。"""
import datetime
import io

from django.core.management import call_command
from django.test import TestCase

from sales.models import DailyReport, DailyReportEntry


class BackfillPricingTest(TestCase):
    def setUp(self):
        self.ok = DailyReport.objects.create(
            date=datetime.date(2026, 10, 7), location="広尾",
            coupon_type_750=1, extra_rice_quantity=1,
            total_discount=-650, total_revenue=6350,  # 7000 + 0 - 650
        )
        DailyReportEntry.objects.create(
            report=self.ok, product_no=1, product="唐揚げ", quantity=12,
            sales_quantity=10, remaining_number=2, total_sales=7000,
        )
        self.bad = DailyReport.objects.create(
            date=datetime.date(2025, 4, 10), location="竹芝",
            coupon_type_600=1, total_discount=-650, total_revenue=-650,  # 当時は600円のはず
        )

    def run_command(self, *args):
        out = io.StringIO()
        call_command("backfill_pricing", *args, stdout=out)
        return out.getvalue()

    def test_dry_run_writes_nothing(self):
        out = self.run_command()
        self.assertIn("照合のみ", out)
        self.assertIn("割引が一致しない 1 件", out)
        self.assertIn("2025-04: 1", out)
        self.assertIsNone(DailyReportEntry.objects.get(report=self.ok).unit_price)
        self.assertEqual(self.ok.discount_lines.count(), 0)

    def test_switch_date_option(self):
        out = self.run_command("--coupon-650-since", "2025-04-01")
        self.assertIn("割引が一致しない 0 件", out)

    def test_apply_is_idempotent_and_keeps_stored_totals(self):
        self.run_command("--apply")
        self.run_command("--apply")
        self.assertEqual(DailyReportEntry.objects.get(report=self.ok).unit_price, 700)
        self.assertEqual(self.ok.discount_lines.count(), 2)
        self.bad.refresh_from_db()
        self.assertEqual(int(self.bad.total_discount), -650)  # 一致しなくても保存済みの値は書き換えない
