"""S1-4：受注の値段（注文画面に渡す値段・顧客別価格表）を sales/pricing.py に切り替えた回帰テスト。"""
from datetime import date

from django.test import TestCase

from orders.views import _get_products_data_for_date, customer_price_table
from sales.models import PriceRank, Product


def make(week, no, name, a, b, c, rank=None):
    return Product.objects.create(week=week, no=no, name=name, price_A=a, price_B=b, price_C=c, rank=rank)


class CustomerPriceTableTest(TestCase):
    def setUp(self):
        # 9/30週（水）と、その次の 10/7週
        make("2026-09-30", 1, "★カキフライ", 700, 650, 600)
        make("2026-09-30", 2, "唐揚げ", 650, 600, 500)
        make("2026-09-30", 11, "大盛りごはん", 50, 50, 50)
        make("2026-10-07", 1, "★エビチリ", 750, 700, 700)
        make("2026-10-07", 2, "生姜焼き", 700, 650, 600)
        make("2026-10-07", 11, "大盛りごはん", 50, 50, 50)

    def test_uses_week_of_target_date(self):
        """期間の最終日の週を使う。以前は週の文字比較がずれて、日付に関係なく一番新しい週を使っていた。"""
        table = customer_price_table(date(2026, 10, 2))  # 9/30週の金曜
        self.assertEqual(table["A"], {"star": 700, "normal": 650, "large": 50})
        self.assertEqual(table["C"]["normal"], 500)

    def test_next_week(self):
        table = customer_price_table(date(2026, 10, 8))
        self.assertEqual(table["A"], {"star": 750, "normal": 700, "large": 50})

    def test_rank_decides_column_and_price(self):
        """種類が入っているメニューは、名前の★ではなく種類で列を決め、価格表の値段を使う。お手頃は列に出さない。"""
        Product.objects.filter(week="2026-10-07").delete()
        make("2026-10-07", 1, "ビーフシチュー", 0, 0, 0, rank=PriceRank.objects.get(name="特選"))
        make("2026-10-07", 9, "五目チャーハン", 0, 0, 0, rank=PriceRank.objects.get(name="お手頃"))
        make("2026-10-07", 10, "生姜焼き", 0, 0, 0, rank=PriceRank.objects.get(name="通常"))
        make("2026-10-07", 11, "大盛りごはん", 0, 0, 0, rank=PriceRank.objects.get(name="大盛り"))
        table = customer_price_table(date(2026, 10, 8))
        self.assertEqual(table["A"], {"star": 750, "normal": 700, "large": 50})
        self.assertEqual(table["B"], {"star": 700, "normal": 650, "large": 50})


class ProductsDataForDateTest(TestCase):
    def test_without_rank_prices_unchanged(self):
        make("2026-10-07", 2, "生姜焼き", 700, 650, 600)
        data = _get_products_data_for_date(date(2026, 10, 8))
        self.assertEqual((data[0]["price_A"], data[0]["price_B"], data[0]["price_C"]), (700, 650, 600))

    def test_with_rank_uses_price_table_by_delivery_date(self):
        make("2026-10-07", 10, "五目チャーハン", 0, 0, 0, rank=PriceRank.objects.get(name="お手頃"))
        data = _get_products_data_for_date(date(2026, 10, 8))
        self.assertEqual((data[0]["price_A"], data[0]["price_B"], data[0]["price_C"]), (650, 650, 600))
        self.assertEqual(data[0]["rank"], "お手頃")
