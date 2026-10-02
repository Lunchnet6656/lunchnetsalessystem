"""メニュー表向けメニュー実績CSVのテスト。"""
import csv
import io
from datetime import date
from decimal import Decimal
from unittest.mock import patch

from django.core.cache import cache
from django.test import TestCase, override_settings
from django.urls import reverse

from orders.models import Customer, Order, OrderItem
from sales.menu_history import build_menu_history
from sales.models import DailyReport, DailyReportEntry, SalesLocation

TODAY = date(2026, 10, 2)
DISH = "鶏のパイタンあんかけ"


def report(day, location, rows):
    """rows: [(no, name, 持参, 販売, 残)]"""
    r = DailyReport.objects.create(date=day, location=location, total_revenue=0)
    for no, name, q, s, rem in rows:
        DailyReportEntry.objects.create(
            report=r, product_no=no, product=name, quantity=q, sales_quantity=s,
            remaining_number=rem, total_sales=0, sold_out=(rem == 0),
        )


def order(day, name, qty, include=True):
    customer = Customer.objects.create(
        customer_type="B2B", company_name=f"会社{day}{qty}", price_type="A",
        bento_type="REGULAR", include_in_total=include,
    )
    o = Order.objects.create(customer=customer, order_date=day, delivery_date=day)
    OrderItem.objects.create(order=o, product_name=name, quantity_regular=qty, unit_price=Decimal(700), subtotal=Decimal(700 * qty))


@override_settings(MENU_HISTORY_API_KEY="read-key", MENU_HISTORY_EXCLUDED_LOCATIONS=["店"])
class MenuHistoryTests(TestCase):
    def setUp(self):
        cache.clear()
        SalesLocation.objects.create(no=1, name="新木場", type="車", price_type="A", service_name="")
        SalesLocation.objects.create(no=2, name="明電舎", type="配達", price_type="B", service_name="")
        # 1回目（2025-12）：2日・廃棄多め
        for d in (date(2025, 12, 10), date(2025, 12, 11)):
            report(d, "新木場", [(8, DISH, 10, 7, 3)])
        # 2回目（2026-06）：3日。店と配達は数えない
        for d in (date(2026, 6, 3), date(2026, 6, 4), date(2026, 6, 5)):
            report(d, "新木場", [(8, DISH, 20, 20, 0), (11, "大盛りごはん", 5, 5, 0)])
            report(d, "店", [(8, DISH, 50, 10, 40)])
            report(d, "明電舎", [(8, DISH, 30, 30, 0)])
        order(date(2026, 6, 3), DISH, 6)
        order(date(2026, 6, 4), DISH, 3)
        order(date(2026, 6, 4), DISH, 100, include=False)  # 全体注文数に含めない客も別注として数える
        order(date(2025, 12, 1), "別の弁当", 1)  # 受注の記録開始日を 2025-12-01 にする

    def row(self, name=DISH):
        return next(r for r in build_menu_history(TODAY) if r["メニュー"] == name)

    def test_last_run_and_overall(self):
        r = self.row()
        self.assertEqual(r["前回の週"], date(2026, 6, 3))
        self.assertEqual(r["日数"], 3)
        self.assertEqual(r["1日の持参"], 20)
        self.assertEqual(r["1日の販売"], 20)
        self.assertEqual(r["1日の別注"], 36)       # (6+3+100)/3日。「全体注文数に含めない」客も数える
        self.assertEqual(r["1日の合計"], 56)
        self.assertEqual(r["廃棄率"], 0.0)
        self.assertEqual(r["完売率"], 1.0)
        self.assertEqual(r["通算の回数"], 2)
        self.assertEqual(r["通算の平均持参"], 16)  # (20+60)/5日
        self.assertEqual(r["通算の平均販売"], 15)  # (14+60)/5日
        self.assertEqual(r["通算の平均廃棄率"], 0.075)  # 6/80

    def test_large_rice_and_today_excluded(self):
        report(TODAY, "新木場", [(8, DISH, 99, 0, 99)])
        names = [r["メニュー"] for r in build_menu_history(TODAY)]
        self.assertNotIn("大盛りごはん", names)
        self.assertEqual(self.row()["日数"], 3)

    def test_orders_unknown_before_order_system(self):
        OrderItem.objects.filter(product_name="別の弁当").delete()
        order(date(2026, 3, 26), "別の弁当", 1)
        r = self.row()
        self.assertEqual(r["1日の別注"], 36)
        report(date(2026, 9, 1), "新木場", [(1, "古い弁当", 10, 10, 0)])
        self.assertEqual(self.row()["1日の別注"], 36)
        # 受注記録が始まる前の回は「分からない」
        report(date(2026, 1, 7), "新木場", [(2, "サバの竜田揚げ", 10, 9, 1)])
        self.assertIsNone(self.row("サバの竜田揚げ")["1日の別注"])

    def test_csv_endpoint(self):
        with patch("sales.api_views.timezone.localdate", return_value=TODAY):
            res = self.client.get(reverse("api_menu_history"), {"key": "read-key"})
        self.assertEqual(res.status_code, 200)
        text = res.content.decode("utf-8")
        self.assertTrue(text.startswith("﻿"))
        rows = list(csv.reader(io.StringIO(text.lstrip("﻿"))))
        self.assertEqual(rows[0][:3], ["メニュー", "前回の週", "日数"])
        self.assertIn(DISH, [r[0] for r in rows[1:]])

    def test_csv_requires_key(self):
        self.assertEqual(self.client.get(reverse("api_menu_history")).status_code, 401)
        self.assertEqual(self.client.get(reverse("api_menu_history"), {"key": "x"}).status_code, 401)
