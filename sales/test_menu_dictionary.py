"""S4-4：メニューデータベース・判定ルール画面、サイドバーとTOPの未確認表示のテスト。"""
import datetime
from unittest import mock

from django.test import TestCase

from sales.menu_registry import register_week
from sales.models import ClassifyRule, MenuProfile, MenuWeekCheck, PriceRank, Product
from sales.test_menu_registry import NAMES
from sales.test_price_admin import make_user

TODAY = datetime.date(2026, 10, 9)   # 金曜：10/7週は販売中、10/14週は開始前


@mock.patch("django.utils.timezone.localdate", return_value=TODAY)
class MenuDictionaryTest(TestCase):
    def setUp(self):
        self.client.force_login(make_user("honbu", price_master=True))
        self.ranks = {r.name: r for r in PriceRank.objects.all()}
        register_week(datetime.date(2026, 10, 7), NAMES)    # 販売中
        register_week(datetime.date(2026, 10, 14), NAMES)   # 開始前

    def test_list_shows_needs_check(self, _):
        res = self.client.get("/menus/dictionary/")
        self.assertContains(res, "要確認 10件")
        self.assertContains(res, 'data-prices="A 650／B 650／C 600"')   # お手頃の今の値段

    def test_confirm_one_reflects_upcoming_only(self, _):
        gapao = MenuProfile.objects.get(name="ガパオライス")
        res = self.client.post("/menus/dictionary/", {
            "confirm_one": gapao.pk, "profile": [p.pk for p in MenuProfile.objects.all()],
            f"rank_{gapao.pk}": self.ranks["お手頃"].id, f"container_{gapao.pk}": "一体型",
        }, follow=True)
        gapao.refresh_from_db()
        self.assertTrue(gapao.confirmed)
        self.assertEqual(MenuProfile.objects.filter(confirmed=False).count(), 9)   # ほかの行は触らない
        self.assertEqual(Product.objects.get(week="2026-10-14", no=9).rank.name, "お手頃")   # 開始前は反映
        self.assertEqual(Product.objects.get(week="2026-10-07", no=9).rank.name, "通常")     # 販売中は変えない
        text = "".join(str(m) for m in res.context["messages"])
        self.assertIn("10/14週（開始前）のメニューにも反映しました。", text)
        self.assertIn("10/7週（販売中）のメニューは変更されません。", text)
        self.assertIn("『ガパオライス』を更新しました（お手頃・一体型）。", text)

    def test_confirm_all(self, _):
        data = {"action": "confirm_all", "profile": []}
        for p in MenuProfile.objects.all():
            data["profile"].append(p.pk)
            data[f"rank_{p.pk}"] = p.rank_id
            data[f"container_{p.pk}"] = p.container
        self.client.post("/menus/dictionary/", data)
        self.assertFalse(MenuProfile.objects.filter(confirmed=False).exists())

    def test_search_and_edit_row(self, _):
        res = self.client.get("/menus/dictionary/?q=チャーハン")
        self.assertEqual([p.name for p in res.context["page"].object_list], ["五目チャーハン"])
        pk = res.context["page"].object_list[0].pk
        self.assertContains(self.client.get(f"/menus/dictionary/?q=チャーハン&edit={pk}"), "保存")
        self.assertContains(self.client.get("/menus/dictionary/?q=存在しない"), "『存在しない』に当てはまるメニューはありません。")


class MenuRulesTest(TestCase):
    def setUp(self):
        self.client.force_login(make_user("honbu", price_master=True))

    def test_add_delete_move_and_try(self):
        self.client.post("/menus/rules/", {"action": "add", "keyword": "ロコモコ", "rank": "", "container": "一体型"})
        rule = ClassifyRule.objects.get(keyword="ロコモコ")
        self.assertIsNone(rule.rank)
        res = self.client.post("/menus/rules/", {"action": "add", "keyword": "丼", "rank": "", "container": ""}, follow=True)
        self.assertIn("どちらかを選択してください", "".join(str(m) for m in res.context["messages"]))
        self.client.post("/menus/rules/", {"action": "up", "rule": rule.pk})
        rules = list(ClassifyRule.objects.values_list("keyword", flat=True))
        self.assertEqual(rules.index("ロコモコ"), len(rules) - 2)
        res = self.client.get("/menus/rules/?try=ロコモコ丼")
        self.assertContains(res, "「ロコモコ」のルールで「一体型」")
        self.assertContains(res, "どのルールでも決まらないので「通常」")
        self.client.post("/menus/rules/", {"action": "delete", "rule": rule.pk})
        self.assertFalse(ClassifyRule.objects.filter(keyword="ロコモコ").exists())


@mock.patch("django.utils.timezone.localdate", return_value=TODAY)
class WeekAlertsTest(TestCase):
    def test_dashboard_and_sidebar_for_price_master_only(self, _):
        register_week(datetime.date(2026, 10, 7), NAMES)
        register_week(datetime.date(2026, 10, 14), NAMES)
        self.client.force_login(make_user("honbu", price_master=True))
        res = self.client.get("/dashboard/")
        self.assertContains(res, "10/7週のメニューが未確認のまま販売中です。")
        self.assertContains(res, "10/14週のメニューが未確認です（要確認 10品）。")
        self.assertContains(res, "メニューデータベース")
        MenuWeekCheck.objects.update(confirmed_at=datetime.datetime(2026, 10, 9, 1, tzinfo=datetime.timezone.utc))
        self.assertNotContains(self.client.get("/dashboard/"), "のメニューが未確認")
        self.client.force_login(make_user("staff_only"))
        res = self.client.get("/dashboard/")
        self.assertNotContains(res, "メニューデータベース")
