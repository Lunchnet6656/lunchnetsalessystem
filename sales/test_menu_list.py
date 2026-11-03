"""販売員用のメニューリスト画面のテスト。"""
import datetime
from unittest import mock

from django.test import TestCase

from sales.menu_registry import register_week
from sales.models import MenuWeekCheck, PriceRank, Product, UserMenuPermission
from sales.test_menu_registry import NAMES
from sales.test_price_admin import make_user

WEEK = datetime.date(2026, 10, 14)
NEXT = datetime.date(2026, 10, 21)
TODAY = datetime.date(2026, 10, 15)
URL = "/menus/list/"


@mock.patch("django.utils.timezone.localdate", return_value=TODAY)
class MenuListScreenTest(TestCase):
    def setUp(self):
        self.user = make_user("hanbai", staff=False)
        self.client.force_login(self.user)
        register_week(WEEK, NAMES)

    def test_everyone_can_open_by_default(self, _):
        self.assertTrue(UserMenuPermission.objects.get(user=self.user).can_view_menu_list)
        res = self.client.get(URL)
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.context["week"], WEEK)
        self.assertEqual(len(res.context["items"]), Product.objects.filter(week="2026-10-14").count())

    def test_without_permission(self, _):
        UserMenuPermission.objects.filter(user=self.user).update(can_view_menu_list=False)
        self.assertRedirects(self.client.get(URL), "/dashboard/", fetch_redirect_response=False)

    def test_rank_and_container(self, _):
        res = self.client.get(URL, {"week": "2026-10-14"})
        self.assertNotContains(res, 'name="loc"')
        for item in res.context["items"]:
            product = Product.objects.get(week="2026-10-14", no=item["no"])
            self.assertEqual(item["rank"], product.rank.name)
            self.assertEqual(item["rank_role"], product.rank.role or "other")
            self.assertEqual(item["container"], product.container_type)
            self.assertNotIn("prices", item)

    def test_unconfirmed_future_week_hidden(self, _):
        register_week(NEXT, NAMES)
        res = self.client.get(URL, {"week": "2026-10-21"})
        self.assertEqual(res.context["week"], WEEK)
        self.assertNotIn(NEXT, [w["week"] for w in res.context["weeks"]])
        MenuWeekCheck.objects.filter(week=NEXT).update(confirmed_at=datetime.datetime(2026, 10, 15, tzinfo=datetime.timezone.utc))
        res = self.client.get(URL, {"week": "2026-10-21"})
        self.assertEqual(res.context["week"], NEXT)
        self.assertEqual(res.context["state"], "upcoming")

    def test_past_legacy_week(self, _):
        Product.objects.create(no=1, week="20260916", name="★昔の弁当", price_A=800, price_C=750, container_type="黒容器など")
        res = self.client.get(URL, {"week": "2026-09-16"})
        item = res.context["items"][0]
        self.assertEqual(item["rank"], PriceRank.objects.get(role="star").name)
        self.assertEqual(item["rank_role"], "star")
        self.assertEqual(item["container"], "黒容器")
        self.assertFalse(res.context["unconfirmed"])
