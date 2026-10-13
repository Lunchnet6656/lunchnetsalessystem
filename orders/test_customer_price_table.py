"""S1-4：受注の値段（注文画面に渡す値段・顧客別価格表）を sales/pricing.py に切り替えた回帰テスト。"""
from datetime import date

from django.test import TestCase

from orders.views import _get_products_data_for_date
from sales.models import PriceRank, Product


def make(week, no, name, a, b, c, rank=None):
    return Product.objects.create(week=week, no=no, name=name, price_A=a, price_B=b, price_C=c, rank=rank)


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
