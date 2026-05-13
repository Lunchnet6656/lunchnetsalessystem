"""公開ページ用 status.json 生成ロジックのテスト（generate_status_json）。"""
from datetime import date

from django.test import TestCase

from sales.management.commands.generate_status_json import build_status, is_business_day
from sales.models import ItemQuantity, Product, SalesLocation


def _location(no, name, excluded=False, excluded_public=False):
    return SalesLocation.objects.create(
        no=no, name=name, type="", price_type="", service_name="",
        excluded_from_shift=excluded,
        excluded_from_public_status=excluded_public,
    )


class StatusPageTests(TestCase):
    def test_business_day_detection(self):
        self.assertTrue(is_business_day(date(2026, 5, 12)))   # 火曜
        self.assertFalse(is_business_day(date(2026, 5, 16)))  # 土曜
        self.assertFalse(is_business_day(date(2026, 5, 4)))   # みどりの日（祝日）

    def test_open_and_closed(self):
        shinjuku = _location(1, "新宿")
        shibuya = _location(2, "渋谷")
        _location(3, "除外拠点", excluded=True)
        haitatsu = _location(4, "配達", excluded_public=True)  # シフトには残すが公開ページからは消す
        product = Product.objects.create(no=1, week="20260511", name="からあげ弁当")
        ItemQuantity.objects.create(
            target_date="2026-05-12", target_week="20260511",
            product=product, sales_location=shinjuku, quantity=40,
        )
        ItemQuantity.objects.create(
            target_date="2026-05-12", target_week="20260511",
            product=product, sales_location=shibuya, quantity=0,
        )
        # 配達は持参数が入っていても公開ページからは消えること
        ItemQuantity.objects.create(
            target_date="2026-05-12", target_week="20260511",
            product=product, sales_location=haitatsu, quantity=80,
        )
        data = build_status(today=date(2026, 5, 12))

        self.assertTrue(data["business_day"])
        self.assertFalse(data["all_unregistered"])
        self.assertEqual(data["date"], "2026-05-12")
        self.assertEqual(data["weekday"], "火")
        pairs = [(loc["name"], loc["status"]) for loc in data["locations"]]
        self.assertIn(("新宿", "open"), pairs)
        self.assertIn(("渋谷", "closed"), pairs)
        names = [loc["name"] for loc in data["locations"]]
        self.assertNotIn("除外拠点", names)
        self.assertNotIn("配達", names)

    def test_all_unregistered_on_business_day(self):
        _location(1, "新宿")
        data = build_status(today=date(2026, 5, 12))

        self.assertTrue(data["business_day"])
        self.assertTrue(data["all_unregistered"])
        self.assertEqual([loc["status"] for loc in data["locations"]], ["closed"])

    def test_holiday_returns_no_locations(self):
        _location(1, "新宿")
        data = build_status(today=date(2026, 5, 16))  # 土曜

        self.assertFalse(data["business_day"])
        self.assertEqual(data["locations"], [])
        self.assertFalse(data["all_unregistered"])

    def test_sorted_by_no(self):
        _location(3, "品川")
        _location(1, "新宿")
        _location(2, "渋谷")
        data = build_status(today=date(2026, 5, 12))
        self.assertEqual([loc["no"] for loc in data["locations"]], [1, 2, 3])
