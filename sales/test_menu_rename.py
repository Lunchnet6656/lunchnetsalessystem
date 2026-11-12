"""週の途中のメニュー名の変更（開始日つき）のテスト。"""
import datetime
from decimal import Decimal
from unittest import mock

from django.test import TestCase

from orders.models import Order, OrderItem
from orders.tests import make_customer
from orders.views import _get_products_data_for_date
from sales.menu_names import add_change, name_on, names_on, remove_change
from sales.menu_registry import register_week
from sales.models import DailyReport, DailyReportEntry, MenuProfile, Product, ProductNameChange
from sales.test_menu_registry import NAMES
from sales.test_price_admin import make_user

WEEK = datetime.date(2026, 10, 14)   # 水曜はじまり（〜10/20）
FRI = datetime.date(2026, 10, 16)
TODAY = datetime.date(2026, 10, 15)


def day(n):
    return WEEK + datetime.timedelta(days=n)


class MenuRenameTest(TestCase):
    def setUp(self):
        register_week(WEEK, NAMES)
        self.product = Product.objects.get(week=WEEK.isoformat(), no=2)   # イタリア風ポーク煮込み
        self.user = make_user("honbu", price_master=True)

    def entry(self, d, name):
        report = DailyReport.objects.create(date=d, location=f"広尾{d.day}", total_revenue=0)
        return DailyReportEntry.objects.create(report=report, product_no=2, product=name, quantity=10,
                                               sales_quantity=8, remaining_number=2, total_sales=0)

    def test_name_on_switches_from_start_date(self):
        add_change(self.product, FRI, "イタリア風ポークトマト煮", self.user, TODAY)
        self.assertEqual(name_on(self.product, day(1)), "イタリア風ポーク煮込み")
        self.assertEqual(name_on(self.product, FRI), "イタリア風ポークトマト煮")
        self.assertEqual(name_on(self.product, day(6)), "イタリア風ポークトマト煮")
        self.assertEqual(names_on([self.product], FRI)[self.product.id], "イタリア風ポークトマト煮")
        # 週の頭の名前は変えない（Excelから届いた名前）
        self.product.refresh_from_db()
        self.assertEqual(self.product.name, "イタリア風ポーク煮込み")

    def test_new_name_is_remembered_with_same_rank(self):
        add_change(self.product, FRI, "イタリア風ポークトマト煮", self.user, TODAY)
        profile = MenuProfile.objects.get(name="イタリア風ポークトマト煮")
        self.assertTrue(profile.confirmed)
        self.assertEqual((profile.rank_id, profile.container), (self.product.rank_id, self.product.container_type))

    def test_sent_reports_and_orders_follow_the_change(self):
        before = self.entry(day(1), "イタリア風ポーク煮込み")
        after = self.entry(FRI, "イタリア風ポーク煮込み")
        order = Order.objects.create(customer=make_customer(), order_date=TODAY, delivery_date=day(5))
        item = OrderItem.objects.create(order=order, product=self.product, quantity_regular=3,
                                        unit_price=Decimal(650), subtotal=Decimal(1950))
        self.assertEqual(item.product_name, "イタリア風ポーク煮込み")

        change, entries, orders = add_change(self.product, FRI, "イタリア風ポークトマト煮", self.user, TODAY)
        self.assertEqual((entries, orders), (1, 1))
        before.refresh_from_db(), after.refresh_from_db(), item.refresh_from_db()
        self.assertEqual(before.product, "イタリア風ポーク煮込み")
        self.assertEqual(after.product, "イタリア風ポークトマト煮")
        self.assertEqual(item.product_name, "イタリア風ポークトマト煮")

        # 取消すと元に戻る
        self.assertEqual(remove_change(change, TODAY), (1, 1))
        after.refresh_from_db(), item.refresh_from_db()
        self.assertEqual(after.product, "イタリア風ポーク煮込み")
        self.assertEqual(item.product_name, "イタリア風ポーク煮込み")

    def test_new_order_uses_delivery_date_name(self):
        add_change(self.product, FRI, "イタリア風ポークトマト煮", self.user, TODAY)
        order = Order.objects.create(customer=make_customer(), order_date=TODAY, delivery_date=day(5))
        item = OrderItem.objects.create(order=order, product=self.product, quantity_regular=1,
                                        unit_price=Decimal(650), subtotal=Decimal(650))
        self.assertEqual(item.product_name, "イタリア風ポークトマト煮")
        names = {p["no"]: p["name"] for p in _get_products_data_for_date(day(5))}
        self.assertEqual(names[2], "イタリア風ポークトマト煮")
        names = {p["no"]: p["name"] for p in _get_products_data_for_date(day(1))}
        self.assertEqual(names[2], "イタリア風ポーク煮込み")

    def test_validation(self):
        cases = [
            (WEEK, "新しい名前", "開始日は10/15〜10/20の間で入力してください。"),
            (day(7), "新しい名前", "開始日は10/15〜10/20の間で入力してください。"),
            (None, "新しい名前", "開始日を入力してください。"),
            (FRI, "　", "新しいメニュー名を入力してください。"),
            (FRI, "イタリア風ポーク煮込み", "前日までと同じメニュー名です。"),
        ]
        for start, name, message in cases:
            with self.subTest(message=message), self.assertRaisesMessage(ValueError, message):
                add_change(self.product, start, name, self.user, TODAY)
        rice = Product.objects.get(week=WEEK.isoformat(), no=11)
        with self.assertRaisesMessage(ValueError, "このメニューは名前を変更できません。"):
            add_change(rice, FRI, "特盛ごはん", self.user, TODAY)
        with self.assertRaisesMessage(ValueError, "終了した週は変更できません。"):
            add_change(self.product, FRI, "新しい名前", self.user, day(7))

    def test_resend_with_new_name_keeps_change(self):
        add_change(self.product, FRI, "イタリア風ポークトマト煮", self.user, TODAY)
        names = list(NAMES)
        names[1] = "イタリア風ポークトマト煮"
        register_week(WEEK, names)
        self.product.refresh_from_db()
        self.assertEqual(self.product.name, "イタリア風ポーク煮込み")
        self.assertEqual(name_on(self.product, FRI), "イタリア風ポークトマト煮")

    def test_resend_with_other_menu_drops_change(self):
        add_change(self.product, FRI, "イタリア風ポークトマト煮", self.user, TODAY)
        names = list(NAMES)
        names[1] = "肉じゃが"
        register_week(WEEK, names)
        self.product.refresh_from_db()
        self.assertEqual(self.product.name, "肉じゃが")
        self.assertFalse(ProductNameChange.objects.exists())


@mock.patch("django.utils.timezone.localdate", return_value=TODAY)
class MenuRenameScreenTest(TestCase):
    def setUp(self):
        register_week(WEEK, NAMES)
        self.product = Product.objects.get(week=WEEK.isoformat(), no=2)
        self.client.force_login(make_user("honbu", price_master=True))
        self.url = "/menus/week/2026-10-14/"

    def test_add_and_delete_on_week_screen(self, _):
        res = self.client.post(self.url, {"action": "rename_add", "product": self.product.id,
                                          "start_date": "2026-10-16", "name": "イタリア風ポークトマト煮"})
        self.assertRedirects(res, self.url, fetch_redirect_response=False)
        res = self.client.get(self.url)
        self.assertContains(res, "No.2を10/16から『イタリア風ポークトマト煮』に変更しました。")
        self.assertContains(res, "10/16〜 イタリア風ポークトマト煮")
        change = ProductNameChange.objects.get()

        res = self.client.post(self.url, {"action": "rename_delete", "change_id": change.id}, follow=True)
        self.assertContains(res, "取消しました")
        self.assertFalse(ProductNameChange.objects.exists())

    def test_error_message(self, _):
        res = self.client.post(self.url, {"action": "rename_add", "product": self.product.id,
                                          "start_date": "2026-10-14", "name": "新しい名前"}, follow=True)
        self.assertContains(res, "開始日は10/15〜10/20の間で入力してください。")
        self.assertFalse(ProductNameChange.objects.exists())

    def test_staff_menu_list_shows_change(self, _):
        ProductNameChange.objects.create(product=self.product, start_date=FRI, name="イタリア風ポークトマト煮")
        self.client.force_login(make_user("hanbai", staff=False))   # 販売員（メニューリストは権限の既定ON）
        res = self.client.get("/menus/list/?week=2026-10-14")
        self.assertContains(res, "10/16〜 イタリア風ポークトマト煮")
