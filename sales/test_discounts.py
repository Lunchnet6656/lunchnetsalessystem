"""割引の計算（sales/discounts.py）のテスト。数字は本番の日計表（2026-10）と同じ形にしてある。"""
import datetime

from django.test import TestCase

from sales.discounts import (
    legacy_base_price, legacy_lines, save_lines, service_price_of, total_discount,
)
from sales.models import DailyReport, DiscountItem


def report(**kwargs):
    defaults = dict(date=datetime.date(2026, 10, 7), location="テスト売り場", total_revenue=0)
    defaults.update(kwargs)
    return DailyReport.objects.create(**defaults)


class LegacyLinesTest(TestCase):
    def test_rice_refund_and_coupons(self):
        r = report(no_rice_quantity=1, extra_rice_quantity=2, discount_50=1,
                   coupon_type_600=1, coupon_type_700=2, coupon_type_750=3)
        lines = legacy_lines(r)
        by_label = {line.label: line for line in lines}
        self.assertEqual(by_label["なし"].unit_amount, -100)
        self.assertEqual(by_label["追加"].unit_amount, 100)
        self.assertEqual(by_label["▲50円"].unit_amount, -50)
        self.assertEqual(by_label["クーポン650円"].unit_amount, -650)  # 欄名は600のまま中身は650円
        self.assertEqual(by_label["クーポン750円"].quantity, 3)
        # -100 + 200 - 50 - 650 - 1400 - 2250
        self.assertEqual(total_discount(r, lines), -4250)

    def test_coupon_600_before_switch(self):
        """2025-05-05 より前の「600円」欄は600円で数える（今の画面の650円で計算し直さない）。"""
        r = report(date=datetime.date(2025, 4, 10), coupon_type_600=2)
        self.assertEqual(total_discount(r, legacy_lines(r)), -1200)

    def test_service_price_stored_in_service_name(self):
        """入力フォームはサービス価格を service_name に入れている（本番：飯田橋 '-100'）。"""
        r = report(service_name="-100", service_price=0, service_type_100=2, extra_rice_quantity=1)
        self.assertEqual(service_price_of(r), -100)
        self.assertEqual(total_discount(r, legacy_lines(r)), -100)  # -200 + 100

    def test_service_bento_tiers(self):
        """本番：店 service_name '0'・サービス700円×7 → -4900（＋ほかの割引）。"""
        r = report(service_name="0", service_type_700=7)
        lines = legacy_lines(r)
        self.assertEqual([(l.label, l.unit_amount) for l in lines], [("サービス700円", -700)])
        self.assertEqual(total_discount(r, lines), -4900)

    def test_service_name_not_numeric_uses_service_price(self):
        r = report(service_name="なし", service_price=500, service_type_750=1)
        self.assertEqual(total_discount(r, legacy_lines(r)), -250)

    def test_ended_item_not_used(self):
        DiscountItem.objects.filter(label="なし").update(valid_to=datetime.date(2026, 9, 30))
        r = report(no_rice_quantity=1)
        self.assertEqual(legacy_lines(r), [])

    def test_legacy_base_price(self):
        self.assertEqual(legacy_base_price("coupon_type_700", datetime.date(2024, 12, 1)), 700)
        self.assertEqual(legacy_base_price("service_type_600", datetime.date(2025, 5, 5)), 650)


class SaveLinesTest(TestCase):
    def test_save_replaces_lines(self):
        r = report(coupon_type_700=2)
        save_lines(r, legacy_lines(r))
        save_lines(r, legacy_lines(r))  # 何度保存しても増えない
        self.assertEqual(r.discount_lines.count(), 1)
        line = r.discount_lines.get()
        self.assertEqual((line.label, line.quantity, line.amount), ("クーポン700円", 2, -1400))
