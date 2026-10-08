"""S4-3：週のメニュー確認画面のテスト。"""
import datetime
from unittest import mock

from django.test import TestCase

from sales.menu_registry import register_week
from sales.models import DailyReport, MenuProfile, MenuWeekCheck, PriceRank, PriceTable, PriceTableCell, Product
from sales.test_menu_registry import NAMES
from sales.test_price_admin import make_user

WEEK = datetime.date(2026, 10, 14)


class MenuWeekScreenTest(TestCase):
    def setUp(self):
        self.client.force_login(make_user("honbu", price_master=True))
        register_week(WEEK, NAMES)
        self.url = "/menus/week/2026-10-14/"
        self.ranks = {r.name: r for r in PriceRank.objects.all()}

    def post_confirm(self, extra=None):
        data = {}
        for p in Product.objects.filter(week="2026-10-14").exclude(no=11):
            data[f"rank_{p.no}"] = str(p.rank_id)
            data[f"container_{p.no}"] = p.container_type
        data.update(extra or {})
        return self.client.post(self.url, data)

    @mock.patch("django.utils.timezone.localdate", return_value=datetime.date(2026, 10, 9))
    def test_detail_upcoming(self, _):
        res = self.client.get(self.url)
        self.assertContains(res, "10/14週のメニュー確認")
        self.assertContains(res, "未確認")
        self.assertContains(res, "種類・容器・価格を確認しました")
        self.assertEqual(res.context["needs_check_count"], 10)   # 大盛りごはんは数えない
        self.assertEqual(len(res.context["rows"]), 11)
        tokusen = next(r for r in res.context["rows"] if r.product.no == 1)
        self.assertEqual(tokusen.prices[0]["A"], 750)

    @mock.patch("django.utils.timezone.localdate", return_value=datetime.date(2026, 10, 9))
    def test_confirm_upcoming_remembers_dictionary(self, _):
        res = self.post_confirm({"rank_9": str(self.ranks["お手頃"].id)})
        self.assertRedirects(res, self.url, fetch_redirect_response=False)
        self.assertTrue(MenuWeekCheck.objects.get(week=WEEK).is_confirmed)
        gapao = Product.objects.get(week="2026-10-14", no=9)
        self.assertEqual((gapao.rank.name, int(gapao.price_A)), ("お手頃", 650))
        self.assertEqual(MenuProfile.objects.get(name="ガパオライス").rank.name, "お手頃")
        self.assertFalse(MenuProfile.objects.filter(confirmed=False).exists())

    @mock.patch("django.utils.timezone.localdate", return_value=datetime.date(2026, 10, 16))
    def test_selling_week_change_needs_confirmation(self, _):
        DailyReport.objects.create(date=datetime.date(2026, 10, 14), location="広尾", total_revenue=0)
        res = self.post_confirm({"rank_9": str(self.ranks["お手頃"].id)})
        self.assertContains(res, "確認：販売中の週です")
        self.assertContains(res, "No.9 ガパオライス　通常 → お手頃")
        self.assertContains(res, "すでに送られた日計表（10/14（水）〜10/16（金）、1件）の値段は変わりません。")
        self.assertFalse(MenuWeekCheck.objects.get(week=WEEK).is_confirmed)
        self.post_confirm({"rank_9": str(self.ranks["お手頃"].id), "selling_ok": "1"})
        self.assertTrue(MenuWeekCheck.objects.get(week=WEEK).is_confirmed)

    @mock.patch("django.utils.timezone.localdate", return_value=datetime.date(2026, 10, 16))
    def test_selling_week_without_change_confirms_directly(self, _):
        res = self.post_confirm()
        self.assertRedirects(res, self.url, fetch_redirect_response=False)
        self.assertTrue(MenuWeekCheck.objects.get(week=WEEK).is_confirmed)

    @mock.patch("django.utils.timezone.localdate", return_value=datetime.date(2026, 10, 21))
    def test_ended_week_is_read_only(self, _):
        res = self.client.get(self.url)
        self.assertContains(res, "この週は終わっています。見るだけです。")
        self.assertNotContains(res, "種類・容器・価格を確認しました")
        self.post_confirm()
        self.assertFalse(MenuWeekCheck.objects.get(week=WEEK).is_confirmed)

    @mock.patch("django.utils.timezone.localdate", return_value=datetime.date(2026, 10, 9))
    def test_mid_week_price_change_shows_two_lines(self, _):
        table = PriceTable.objects.create(valid_from=datetime.date(2026, 10, 17), note="週の途中")
        for cell in PriceTable.objects.get(valid_from="2026-10-01").cells.all():
            PriceTableCell.objects.create(table=table, rank=cell.rank, pattern=cell.pattern, price=cell.price + 50)
        res = self.client.get(self.url)
        self.assertTrue(res.context["multi_segment"])
        row = next(r for r in res.context["rows"] if r.product.no == 2)
        self.assertEqual([(p["label"], p["A"]) for p in row.prices], [("〜10/16", 700), ("10/17〜", 750)])

    @mock.patch("django.utils.timezone.localdate", return_value=datetime.date(2026, 10, 9))
    def test_week_list_and_missing_week(self, _):
        res = self.client.get("/menus/week/")
        labels = [w["label"] for w in res.context["weeks"]]
        self.assertEqual(labels[:2], ["10/14週", "10/7週"])
        self.assertContains(self.client.get("/menus/week/2026-10-21/"), "10/21週のメニューはまだ届いていません。")

    def test_permission(self):
        self.client.force_login(make_user("staff_only"))
        self.assertRedirects(self.client.get(self.url), "/dashboard/", fetch_redirect_response=False)


class LoginNextTest(TestCase):
    """Excelから確認画面を開いてログイン画面になったとき、ログイン後に確認画面へ戻る（同じサイト内だけ）。"""

    def test_login_returns_to_next(self):
        make_user("honbu", price_master=True)
        res = self.client.post("/login/?next=/menus/week/2026-10-14/",
                               {"username": "honbu", "password": "pass", "next": "/menus/week/2026-10-14/"})
        self.assertRedirects(res, "/menus/week/2026-10-14/", fetch_redirect_response=False)

    def test_external_next_is_ignored(self):
        make_user("honbu2", price_master=True)
        res = self.client.post("/login/", {"username": "honbu2", "password": "pass", "next": "https://evil.example/"})
        self.assertRedirects(res, "/dashboard/", fetch_redirect_response=False)
