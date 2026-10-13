"""S5：価格の一元化（価格パターン・値段の種類のマスタ化、顧客別価格表、注文の値段をサーバーで決める）。
仕様: .company/engineering/harness/specs/lunchnetsale-価格の一元化-要件と画面設計.md §10
"""
import datetime
import importlib
from unittest import mock

from django.apps import apps
from django.test import TestCase

from orders.models import Customer, Order
from sales.models import (
    ClassifyRule, MenuProfile, PriceChangeLog, PricePattern, PriceRank, PriceTable, PriceTableCell, Product,
    SalesLocation,
)
from sales.pricing import PriceBook, pattern_of
from sales.test_price_admin import cell_post, make_user

TODAY = datetime.date(2026, 10, 7)   # 水曜。初期データの価格表は 2026-10-01 から


def rank(name):
    return PriceRank.objects.get(name=name)


def custom_customer(name="テュフ", **kw):
    return Customer.objects.create(customer_type="B2B", company_name=name, price_type="CUSTOM",
                                   bento_type="REGULAR", **kw)


def customer_table(customer, valid_from, prices):
    table = PriceTable.objects.create(customer=customer, valid_from=valid_from)
    for name, price in prices.items():
        PriceTableCell.objects.create(table=table, rank=rank(name), pattern="", price=price)
    return table


class InitialDataTest(TestCase):
    def test_patterns_and_roles(self):
        self.assertEqual(list(PricePattern.objects.values_list("code", "name")),
                         [("A", "価格A"), ("B", "価格B"), ("C", "価格C")])
        self.assertEqual(rank("特選").role, "star")
        self.assertEqual(rank("通常").role, "normal")
        self.assertEqual(rank("大盛り").role, "large")
        self.assertEqual(rank("お手頃").role, "")

    def test_custom_customer_migrated_with_otegoro_same_as_normal(self):
        """独自価格の3欄を顧客別価格表に移す。お手頃は★なしと同じ金額（決定 D3）。"""
        customer = custom_customer(custom_price_normal=700, custom_price_star=800, custom_price_large=50)
        Customer.objects.create(customer_type="B2B", company_name="A客", price_type="A")
        module = importlib.import_module("sales.migrations.0091_price_patterns_initial")
        module.forward(apps, None)
        table = PriceTable.objects.get(customer=customer)
        self.assertEqual(table.valid_from, datetime.date(2020, 1, 1))
        cells = {c.rank.name: c.price for c in table.cells.select_related("rank")}
        self.assertEqual(cells, {"特選": 800, "通常": 700, "お手頃": 700, "大盛り": 50})
        self.assertEqual(PriceTable.objects.filter(customer__isnull=False).count(), 1)
        module.forward(apps, None)   # 2回流しても増えない
        self.assertEqual(PriceTable.objects.filter(customer=customer).count(), 1)


class CustomerPriceBookTest(TestCase):
    def setUp(self):
        self.customer = custom_customer()
        customer_table(self.customer, datetime.date(2020, 1, 1), {"特選": 800, "通常": 700, "お手頃": 700, "大盛り": 50})
        customer_table(self.customer, datetime.date(2026, 11, 1), {"特選": 850, "通常": 750, "お手頃": 700, "大盛り": 50})

    def test_switch_by_delivery_date(self):
        product = Product(rank=rank("通常"), name="生姜焼き")
        self.assertEqual(PriceBook(datetime.date(2026, 10, 31), self.customer).customer_price(product), 700)
        self.assertEqual(PriceBook(datetime.date(2026, 11, 1), self.customer).customer_price(product), 750)

    def test_menu_without_rank_uses_role(self):
        book = PriceBook(datetime.date(2026, 10, 7), self.customer)
        self.assertEqual(book.customer_price(Product(name="★カキフライ")), 800)
        self.assertEqual(book.customer_price(Product(name="唐揚げ")), 700)
        self.assertEqual(book.customer_price(Product(name="大盛りごはん")), 50)

    def test_rename_rank_keeps_legacy_menu_price(self):
        """種類の名前を変えても、種類のない古いメニューの値段は変わらない（役割で引く）。"""
        r = rank("特選")
        r.name = "★特選"
        r.save()
        self.assertEqual(PriceBook(datetime.date(2026, 10, 7), self.customer).customer_price(Product(name="★エビ")), 800)

    def test_missing_is_none(self):
        new_rank = PriceRank.objects.create(name="プレミアム", sort_order=9)
        book = PriceBook(datetime.date(2026, 10, 7), self.customer)
        self.assertIsNone(book.customer_price(Product(rank=new_rank, name="和牛")))

    def test_pattern_customer_uses_shared_table(self):
        b_customer = Customer.objects.create(customer_type="B2B", company_name="B客", price_type="B")
        book = PriceBook(datetime.date(2026, 10, 7), b_customer)
        self.assertEqual(book.customer_price(Product(rank=rank("通常"), name="生姜焼き")), 650)


@mock.patch("django.utils.timezone.localdate", return_value=TODAY)
class PatternScreenTest(TestCase):
    def setUp(self):
        self.client.force_login(make_user("honbu", price_master=True))

    def test_add_pattern_then_register_column_and_assign(self, _):
        res = self.client.post("/prices/patterns/", {"action": "add", "name": "価格D"}, follow=True)
        self.assertContains(res, "価格パターン『価格D』を追加しました。")
        d = PricePattern.objects.get(name="価格D")
        self.assertEqual(d.code, "D")
        # 値段が登録されるまで販売所には設定できない
        from sales.price_admin import assignable_patterns
        self.assertNotIn(d, assignable_patterns(TODAY))
        # 新規作成の画面に D 列が空欄で出る
        res = self.client.get("/prices/new/")
        self.assertContains(res, "価格D")
        # D 列を入れずに確認 → エラー
        res = self.client.post("/prices/confirm/", cell_post(valid_from="2026-10-07"))
        self.assertContains(res, "値段は1円以上の整数で入力してください。")
        data = cell_post(valid_from="2026-10-07")
        for r in PriceRank.objects.all():
            data[f"cell_{r.id}_D"] = "777" if r.is_bento else "60"
        data["checked"] = "1"
        res = self.client.post("/prices/register/", data, follow=True)
        self.assertContains(res, "からの価格表を登録しました。")
        self.assertIn(d, assignable_patterns(TODAY))
        loc = SalesLocation.objects.create(no=99, name="新店", type="移動", price_type="D", service_name="なし")
        self.assertEqual(pattern_of(loc), "D")
        self.assertEqual(PriceBook(TODAY).price(Product(rank=rank("通常"), name="x"), "D"), 777)

    def test_end_blocked_while_used(self, _):
        b = PricePattern.objects.get(code="B")
        loc = SalesLocation.objects.create(no=98, name="B店", type="移動", price_type="B", service_name="なし")
        res = self.client.post("/prices/patterns/", {"action": "end", "id": b.pk}, follow=True)
        self.assertContains(res, "は終了できません。")
        self.assertTrue(PricePattern.objects.get(pk=b.pk).active)
        loc.price_type = "A"
        loc.save()
        res = self.client.post("/prices/patterns/", {"action": "end", "id": b.pk}, follow=True)
        self.assertContains(res, "価格パターン『価格B』を終了しました。")
        # 次の新規作成から B 列が消える。今の価格表の表示には残る
        self.assertNotContains(self.client.get("/prices/new/"), "cell_1_B")
        self.assertContains(self.client.get("/prices/"), "価格B")

    def test_rename_and_log(self, _):
        a = PricePattern.objects.get(code="A")
        self.client.post("/prices/patterns/", {"action": "rename", "id": a.pk, "name": "通常価格"})
        self.assertEqual(PricePattern.objects.get(pk=a.pk).name, "通常価格")
        self.assertTrue(PriceChangeLog.objects.filter(kind="price_master", summary__contains="通常価格").exists())
        res = self.client.post("/prices/patterns/", {"action": "add", "name": "通常価格"}, follow=True)
        self.assertContains(res, "すでに登録されています")

    def test_location_screen_rejects_pattern_without_prices(self, _):
        PricePattern.objects.create(code="D", name="価格D", sort_order=4)
        loc = SalesLocation.objects.create(no=97, name="既存店", type="移動", price_type="A", service_name="なし")
        res = self.client.post("/location_list", {
            "location[0][id]": loc.pk, "location[0][no]": "97", "location[0][name]": "既存店",
            "location[0][type]": "移動", "location[0][price_type]": "D", "location[0][service_name]": "なし",
            "location[0][service_style]": "なし",
        }, follow=True)
        self.assertEqual(SalesLocation.objects.get(pk=loc.pk).price_type, "A")
        self.assertContains(res, "価格タイプは変更しませんでした")


@mock.patch("django.utils.timezone.localdate", return_value=TODAY)
class RankScreenTest(TestCase):
    def setUp(self):
        self.client.force_login(make_user("honbu", price_master=True))

    def test_add_rank_and_confirm_shows_customers_missing(self, _):
        customer = custom_customer()
        customer_table(customer, datetime.date(2020, 1, 1), {"特選": 800, "通常": 700, "お手頃": 700, "大盛り": 50})
        self.client.post("/prices/ranks/", {"action": "add", "name": "プレミアム", "is_bento": "1"})
        premium = rank("プレミアム")
        data = cell_post()
        for p in "ABC":
            data[f"cell_{premium.id}_{p}"] = "900"
        res = self.client.post("/prices/confirm/", data)
        self.assertContains(res, "顧客別の価格表で未設定の顧客があります（1社）")
        self.assertContains(res, "テュフ：プレミアム")
        self.assertContains(self.client.get("/prices/customers/"), "値段が未設定の顧客があります（1社）")

    def test_end_rank_blocked_by_menu_and_basic_role(self, _):
        otegoro = rank("お手頃")
        res = self.client.post("/prices/ranks/", {"action": "end", "id": otegoro.pk}, follow=True)
        if MenuProfile.objects.filter(rank=otegoro).exists() or ClassifyRule.objects.filter(rank=otegoro).exists():
            self.assertContains(res, "は終了できません。")
        res = self.client.post("/prices/ranks/", {"action": "end", "id": rank("特選").pk}, follow=True)
        self.assertContains(res, "基本の種類のため終了できません。")
        self.assertTrue(rank("特選").active)

    def test_end_unused_rank(self, _):
        extra = PriceRank.objects.create(name="季節限定", sort_order=9)
        res = self.client.post("/prices/ranks/", {"action": "end", "id": extra.pk}, follow=True)
        self.assertContains(res, "値段の種類『季節限定』を終了しました。")
        self.assertFalse(PriceRank.objects.get(pk=extra.pk).active)


@mock.patch("django.utils.timezone.localdate", return_value=TODAY)
class CustomerScreenTest(TestCase):
    def setUp(self):
        self.client.force_login(make_user("honbu", price_master=True))
        self.customer = custom_customer()
        customer_table(self.customer, datetime.date(2020, 1, 1), {"特選": 800, "通常": 700, "お手頃": 700, "大盛り": 50})

    def post_data(self, valid_from="2026-11-01", **prices):
        values = {"特選": 800, "通常": 700, "お手頃": 700, "大盛り": 50, **prices}
        data = {"valid_from": valid_from, "note": ""}
        for name, price in values.items():
            data[f"cell_{rank(name).id}_"] = str(price)
        return data

    def test_list_shows_current_prices(self, _):
        res = self.client.get("/prices/customers/")
        self.assertContains(res, "テュフ")
        self.assertContains(res, "800")

    def test_new_confirm_register_cancel(self, _):
        res = self.client.get(f"/prices/customers/{self.customer.pk}/new/")
        self.assertContains(res, "テュフ：価格表の新規作成")
        res = self.client.post(f"/prices/customers/{self.customer.pk}/confirm/", self.post_data(通常=750))
        self.assertContains(res, "テュフの値段が次の値段に変更されます。")
        self.assertContains(res, "通常　700円 → 750円")
        res = self.client.post(f"/prices/customers/{self.customer.pk}/register/", self.post_data(通常=750), follow=True)
        self.assertContains(res, "テュフの価格表を登録しました。")
        table = PriceTable.objects.get(customer=self.customer, valid_from=datetime.date(2026, 11, 1))
        self.assertContains(self.client.get("/prices/customers/"), "11/1から（1マス）")
        # 販売所用の価格表とは別（同じ開始日でも登録できる）
        self.assertFalse(PriceTable.objects.filter(customer__isnull=True, valid_from=datetime.date(2026, 11, 1)).exists())
        res = self.client.post(f"/prices/customers/table/{table.pk}/cancel/", follow=True)
        self.assertContains(res, "テュフの価格表を取り消しました。")
        self.assertTrue(PriceChangeLog.objects.filter(kind="customer_price").exists())

    def test_validation(self, _):
        res = self.client.post(f"/prices/customers/{self.customer.pk}/confirm/", self.post_data())
        self.assertContains(res, "現在の価格表と同じ値段です。")
        data = self.post_data(通常=750)
        data[f"cell_{rank('お手頃').id}_"] = ""
        res = self.client.post(f"/prices/customers/{self.customer.pk}/confirm/", data)
        self.assertContains(res, "値段は1円以上の整数で入力してください。")

    def test_non_custom_customer_404(self, _):
        a = Customer.objects.create(customer_type="B2B", company_name="A客", price_type="A")
        self.assertEqual(self.client.get(f"/prices/customers/{a.pk}/new/").status_code, 404)

    def test_permission(self, _):
        self.client.force_login(make_user("staff_only"))
        for url in ("/prices/customers/", "/prices/ranks/", "/prices/patterns/"):
            self.assertRedirects(self.client.get(url), "/dashboard/", fetch_redirect_response=False)


class OrderServerPriceTest(TestCase):
    """注文の値段はサーバーで決める（画面から来た単価は使わない）。"""

    def setUp(self):
        from django.contrib.auth import get_user_model
        self.user = get_user_model().objects.create_user(username="clerk", password="pass")
        self.client.force_login(self.user)
        self.customer = custom_customer()
        customer_table(self.customer, datetime.date(2020, 1, 1), {"特選": 800, "通常": 700, "お手頃": 700, "大盛り": 50})
        self.product = Product.objects.create(week="2026-10-07", no=1, name="生姜焼き", rank=rank("通常"),
                                              price_A=0, price_B=0, price_C=0, container_type="黒容器")
        Product.objects.create(week="2026-10-07", no=11, name="大盛りごはん", rank=rank("大盛り"),
                               price_A=0, price_B=0, price_C=0)

    def post(self, unit_price="1", large="1", regular="2"):
        return self.client.post("/orders/create/", {
            "customer": self.customer.pk, "order_date": "2026-10-07", "delivery_date": "2026-10-08",
            "billing_pattern": "IMMEDIATE", "notes": "", "receipt_memo": "テスト",
            "items-TOTAL_FORMS": "1", "items-INITIAL_FORMS": "0", "items-MIN_NUM_FORMS": "0", "items-MAX_NUM_FORMS": "1000",
            "items-0-product": self.product.pk, "items-0-product_name": "生姜焼き",
            "items-0-quantity": "3", "items-0-quantity_large": large, "items-0-quantity_regular": regular,
            "items-0-quantity_small": "0", "items-0-unit_price": unit_price, "items-0-subtotal": "3",
            "extra_items-TOTAL_FORMS": "0", "extra_items-INITIAL_FORMS": "0",
            "extra_items-MIN_NUM_FORMS": "0", "extra_items-MAX_NUM_FORMS": "1000",
        })

    def test_api_returns_customer_price(self):
        data = self.client.get("/orders/api/products/2026-10-08/", {"customer": self.customer.pk}).json()
        prices = {p["name"]: p["price"] for p in data["products"]}
        self.assertEqual(prices["生姜焼き"], 700)
        self.assertEqual(data["large_extra"], 50)

    def test_tampered_unit_price_is_replaced(self):
        res = self.post(unit_price="1")
        self.assertEqual(res.status_code, 302, getattr(res, "context", None) and res.context["form"].errors)
        item = Order.objects.get().items.get()
        self.assertEqual(item.unit_price, 700)
        self.assertEqual(item.subtotal, 750 + 700 * 2)

    def test_missing_price_blocks_save(self):
        PriceTableCell.objects.filter(table__customer=self.customer, rank=rank("通常")).delete()
        res = self.post()
        self.assertEqual(res.status_code, 200)
        self.assertContains(res, "値段が未設定のため登録できません（生姜焼き）")
        self.assertFalse(Order.objects.exists())


class CustomerFormInitialPriceTest(TestCase):
    """顧客の登録画面で、独自価格の最初の値段をその場で入れられる（2026-10-13 しょうへい指摘）。"""

    def setUp(self):
        from django.contrib.auth import get_user_model
        self.client.force_login(get_user_model().objects.create_user(username="clerk2", password="pass"))

    def form_data(self, **extra):
        data = {"customer_type": "B2B", "company_name": "新規商事", "price_type": "CUSTOM",
                "invoice_delivery": "MAIL", "bento_type": "REGULAR", "regular_type": "WEEKDAY"}
        data.update(extra)
        return data

    def prices(self, **override):
        values = {"特選": 820, "通常": 720, "お手頃": 680, "大盛り": 60, **override}
        return {f"initial_price_{rank(n).id}": str(v) for n, v in values.items() if v is not None}

    def test_create_custom_customer_with_prices(self):
        res = self.client.post("/orders/customers/create/", self.form_data(**self.prices()))
        self.assertEqual(res.status_code, 302, res.context and res.context["form"].errors)
        customer = Customer.objects.get(company_name="新規商事")
        table = PriceTable.objects.get(customer=customer)
        self.assertEqual({c.rank.name: c.price for c in table.cells.select_related("rank")},
                         {"特選": 820, "通常": 720, "お手頃": 680, "大盛り": 60})
        self.assertEqual(PriceBook(datetime.date(2026, 10, 8), customer)
                         .customer_price(Product(rank=rank("お手頃"), name="チャーハン")), 680)
        self.assertTrue(PriceChangeLog.objects.filter(kind="customer_price", summary__contains="最初の値段").exists())

    def test_prices_required_for_custom(self):
        res = self.client.post("/orders/customers/create/", self.form_data(**self.prices(お手頃=None)))
        self.assertEqual(res.status_code, 200)
        self.assertContains(res, "独自価格の場合、値段を入力してください。")
        self.assertFalse(Customer.objects.filter(company_name="新規商事").exists())

    def test_pattern_customer_needs_no_prices(self):
        res = self.client.post("/orders/customers/create/", self.form_data(price_type="A"))
        self.assertEqual(res.status_code, 302)
        self.assertFalse(PriceTable.objects.filter(customer__company_name="新規商事").exists())

    def test_edit_existing_shows_current_prices_without_inputs(self):
        customer = custom_customer()
        customer_table(customer, datetime.date(2020, 1, 1), {"特選": 800, "通常": 700, "お手頃": 700, "大盛り": 50})
        res = self.client.get(f"/orders/customers/{customer.pk}/edit/")
        self.assertContains(res, "本日時点の値段です。")
        self.assertNotContains(res, "initial_price_")
