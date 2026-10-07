"""日計表の計算部品（sales/daily_report_calc.py）のテスト。"""
import datetime

from django.test import TestCase

from sales.daily_report_calc import (
    EntryRow, edit_fields, grouped, input_fields, legacy_values, price_summary, read_quantities,
    service_mode, signature, totals,
)
from sales.discounts import legacy_lines, save_lines
from sales.models import DailyReport, SalesLocation

DAY = datetime.date(2026, 10, 7)


def location(price_type="A", style="なし", service_price=0, name="カレーセット"):
    return SalesLocation(name="テスト", price_type=price_type, service_name=name if style != "なし" else "なし",
                         service_price=service_price, service_style=style)


class InputFieldsTest(TestCase):
    def test_price_a_location(self):
        fields = input_fields(DAY, location(), [750, 700, 650])
        self.assertEqual([(f.group, f.label, f.unit_amount) for f in fields], [
            ("rice", "なし（▲100円）", -100), ("rice", "追加（＋100円）", 100),
            ("coupon", "750円", -750), ("coupon", "700円", -700), ("coupon", "650円", -650),
            ("refund", "▲50円", -50), ("refund", "▲100円", -100),
        ])

    def test_price_c_location_has_two_coupons(self):
        fields = input_fields(DAY, location(price_type="C"), [700, 600])
        self.assertEqual([f.label for f in fields if f.group == "coupon"], ["700円", "600円"])

    def test_service_tiers(self):
        fields = input_fields(DAY, location(style="サービス", service_price=500), [750, 700, 650])
        service = [(f.label, f.unit_amount) for f in fields if f.group == "service"]
        self.assertEqual(service, [("750円", -250), ("700円", -200), ("650円", -150)])

    def test_service_flat(self):
        fields = input_fields(DAY, location(style="割引", service_price=-100, name="100円引き"), [700])
        service = [(f.name, f.unit_amount) for f in fields if f.group == "service"]
        self.assertEqual(service, [("disc_service_flat", -100)])

    def test_service_mode(self):
        self.assertIsNone(service_mode(location()))
        self.assertEqual(service_mode(location(style="割引")), "flat")
        self.assertEqual(service_mode(location(style="サービス")), "tiers")

    def test_grouped_skips_empty_groups(self):
        groups = grouped(input_fields(DAY, location(), []))
        self.assertEqual([g for g, _, _ in groups], ["rice", "refund"])


class ReadQuantitiesTest(TestCase):
    def test_lines_and_legacy_values(self):
        fields = input_fields(DAY, location(style="サービス", service_price=500), [750, 700, 650, 600])
        post = {"disc_coupon_750": "1", "disc_coupon_650": "2", "disc_coupon_600": "1",
                "disc_service_700": "3", fields[0].name: "1", "disc_coupon_700": "x"}
        lines = read_quantities(fields, post)
        self.assertEqual(sum(l.unit_amount * l.quantity for l in lines), -100 - 750 - 1300 - 600 - 600)
        values = legacy_values(lines, DAY)
        self.assertEqual(values["coupon_type_750"], 1)
        self.assertEqual(values["coupon_type_600"], 2)   # 650円＝旧「600」欄
        self.assertEqual(values["coupon_type_700"], 0)
        self.assertEqual(values["service_type_700"], 3)
        self.assertEqual(values["no_rice_quantity"], 1)
        # 600円のクーポン（C価格）は対応する旧欄がないので書かない（CSVの内訳列に出る）
        self.assertEqual(sum(values.values()), 1 + 2 + 3 + 1)

    def test_signature_changes_with_location(self):
        a = signature(input_fields(DAY, location(), [750, 700, 650]))
        c = signature(input_fields(DAY, location(price_type="C"), [700, 600]))
        self.assertNotEqual(a, c)


class EditFieldsTest(TestCase):
    def test_saved_lines_keep_old_prices(self):
        """2025年4月の日計表は、600円クーポンのまま出す。その日に有効な欄は0で足す。"""
        report = DailyReport.objects.create(date=datetime.date(2025, 4, 10), location="竹芝",
                                            total_revenue=0, coupon_type_600=2)
        save_lines(report, legacy_lines(report))
        fields = edit_fields(report, location(), [700, 600])
        coupons = [(f.label, f.unit_amount, f.quantity) for f in fields if f.group == "coupon"]
        self.assertEqual(coupons, [("700円", -700, 0), ("600円", -600, 2)])

    def test_without_saved_lines_uses_legacy_columns(self):
        report = DailyReport.objects.create(date=DAY, location="KONO", total_revenue=0, coupon_type_700=1)
        fields = edit_fields(report, location(), [750, 700, 650])
        self.assertEqual([f.quantity for f in fields if f.name == "disc_coupon_700"], [1])

    def test_saved_service_unit_is_kept(self):
        report = DailyReport.objects.create(date=DAY, location="店", total_revenue=0,
                                            service_name="0", service_type_700=2)
        save_lines(report, legacy_lines(report))
        fields = edit_fields(report, location(style="サービス", service_price=500), [700])
        self.assertEqual([(f.name, f.unit_amount, f.quantity) for f in fields if f.group == "service"],
                         [("disc_service_700", -700, 2)])  # 今のサービス価格500円ではなく、当時の0円で計算


class TotalsTest(TestCase):
    def test_totals_match_screen_rules(self):
        rows = [EntryRow(1, 750, 10, 5), EntryRow(2, 700, 20, 0), EntryRow(11, 50, 30, 16, is_large=True)]
        fields = input_fields(DAY, location(), [750, 700])
        lines = read_quantities(fields, {"disc_coupon_700": "1"})
        result = totals(rows, others_total=300, lines=lines, payments=[10000, 0, 8000])
        self.assertEqual(result["total_quantity"], 30)          # 大盛りを除く
        self.assertEqual(result["total_sales_quantity"], 25)
        self.assertEqual(result["total_discount"], -700)
        self.assertEqual(result["total_revenue"], 3750 + 14000 + 700 + 300 - 700)
        self.assertEqual(result["sales_difference"], 18000 - 18050)
        self.assertEqual((result["sales_price_quantity_1"], result["sales_price_quantity_2"],
                          result["sales_price_quantity_3"]), (5, 20, 14))

    def test_price_summary_shows_all_prices(self):
        rows = [EntryRow(1, 750, 5, 0), EntryRow(2, 700, 69, 0), EntryRow(10, 650, 3, 0),
                EntryRow(11, 50, 14, 0, is_large=True)]
        summary = price_summary(rows)
        self.assertEqual([(s["price"], s["quantity"], s["is_large"]) for s in summary],
                         [(750, 5, False), (700, 69, False), (650, 3, False), (50, 14, True)])
