"""値段を決める処理（sales/pricing.py）のテスト。"""
import datetime

from django.test import TestCase

from sales.models import PriceRank, PriceTable, PriceTableCell, Product, SalesLocation
from sales.pricing import PriceBook, bento_prices_for, is_bento, pattern_of, price_for


def make_product(name="唐揚げ弁当", no=2, prices=(700, 650, 600), rank=None, week="2026-10-07"):
    return Product.objects.create(
        no=no, week=week, name=name, rank=rank,
        price_A=prices[0], price_B=prices[1], price_C=prices[2],
    )


class PriceForTest(TestCase):
    """初期データ（0084）の価格表 2026-10-01版 を使う。"""

    def setUp(self):
        self.tokusen = PriceRank.objects.get(name="特選")
        self.tsujo = PriceRank.objects.get(name="通常")
        self.otegoro = PriceRank.objects.get(name="お手頃")
        self.oomori = PriceRank.objects.get(name="大盛り")

    def test_rank_empty_uses_legacy_prices(self):
        """種類が空の古いメニューは、今までどおり price_A/B/C を使う（S1で値段が変わらない保証）。"""
        p = make_product(prices=(123, 456, 789))
        day = datetime.date(2026, 10, 7)
        self.assertEqual([price_for(p, x, day) for x in "ABC"], [123, 456, 789])

    def test_rank_uses_price_table(self):
        p = make_product(name="五目チャーハン", rank=self.otegoro, prices=(0, 0, 0))
        day = datetime.date(2026, 10, 7)
        self.assertEqual([price_for(p, x, day) for x in "ABC"], [650, 650, 600])

    def test_before_first_table_falls_back_to_legacy(self):
        """最初の価格表より前の日付は、種類があっても price_A/B/C を使う。"""
        p = make_product(rank=self.tsujo, prices=(650, 600, 500))
        self.assertEqual(price_for(p, "A", datetime.date(2026, 9, 30)), 650)

    def test_mid_week_price_change(self):
        """週の途中（木曜）から新しい版が始まると、同じメニューでも水曜と木曜で値段が変わる。"""
        table = PriceTable.objects.create(valid_from=datetime.date(2026, 11, 5), note="テスト値上げ")
        PriceTableCell.objects.create(table=table, rank=self.tsujo, pattern="A", price=750)
        p = make_product(rank=self.tsujo, week="2026-11-04")
        self.assertEqual(price_for(p, "A", datetime.date(2026, 11, 4)), 700)  # 水曜＝10/1版
        self.assertEqual(price_for(p, "A", datetime.date(2026, 11, 5)), 750)  # 木曜＝11/5版

    def test_missing_cell_falls_back_to_legacy(self):
        table = PriceTable.objects.create(valid_from=datetime.date(2026, 12, 2))
        PriceTableCell.objects.create(table=table, rank=self.tsujo, pattern="A", price=800)
        p = make_product(rank=self.tsujo, prices=(700, 650, 600))
        self.assertEqual(price_for(p, "B", datetime.date(2026, 12, 2)), 650)

    def test_unknown_pattern_is_zero(self):
        p = make_product(rank=self.tsujo)
        self.assertEqual(price_for(p, "D", datetime.date(2026, 10, 7)), 0)

    def test_accepts_string_dates(self):
        p = make_product(rank=self.tsujo)
        self.assertEqual(price_for(p, "A", "2026-10-07"), 700)
        self.assertEqual(price_for(p, "A", "20261007"), 700)

    def test_price_book_reads_table_once(self):
        products = [make_product(no=i, rank=self.tsujo) for i in range(1, 6)]
        book = PriceBook(datetime.date(2026, 10, 7))
        with self.assertNumQueries(0):
            self.assertEqual({book.price(p, "A") for p in products}, {700})

    def test_bento_prices_excludes_large_rice(self):
        products = [
            make_product(name="★カキフライ", no=1, rank=self.tokusen),
            make_product(name="唐揚げ", no=2, rank=self.tsujo),
            make_product(name="五目チャーハン", no=10, rank=self.otegoro),
            make_product(name="大盛りごはん", no=11, rank=self.oomori),
        ]
        day = datetime.date(2026, 10, 7)
        self.assertEqual(bento_prices_for(products, "A", day), [750, 700, 650])
        self.assertEqual(bento_prices_for(products, "C", day), [700, 600])

    def test_is_bento_legacy_by_name(self):
        self.assertFalse(is_bento(make_product(name="大盛りごはん", no=11, prices=(50, 50, 50))))
        self.assertTrue(is_bento(make_product(name="唐揚げ")))


class PatternOfTest(TestCase):
    def test_pattern_normalized(self):
        self.assertEqual(pattern_of(SalesLocation(price_type=" b ")), "B")
        self.assertIsNone(pattern_of(SalesLocation(price_type="")))
        self.assertEqual(pattern_of(SalesLocation(price_type="X"), default="A"), "A")
