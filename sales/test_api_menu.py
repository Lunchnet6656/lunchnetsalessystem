"""S4-2：メニュー送信の受け口 /api/menu/ のテスト。"""
import datetime
import json
from unittest import mock

from django.test import TestCase, override_settings

from sales.models import MenuWeekCheck, Product
from sales.test_menu_registry import NAMES

TOKEN = "test-token"
TODAY = datetime.date(2026, 10, 9)  # 金曜


@override_settings(ITEM_QUANTITY_API_TOKEN=TOKEN)
@mock.patch("django.utils.timezone.localdate", return_value=TODAY)
class ApiMenuTest(TestCase):
    def post(self, body, token=TOKEN):
        return self.client.post("/api/menu/", data=json.dumps(body, ensure_ascii=False),
                                content_type="application/json", HTTP_AUTHORIZATION=f"Bearer {token}")

    def test_register_next_week(self, _):
        res = self.post({"week": "2026-10-14", "menus": NAMES, "extra": ["", ""]})
        data = res.json()
        self.assertEqual(res.status_code, 200)
        self.assertTrue(data["ok"])
        self.assertEqual(data["saved_count"], 11)
        self.assertIn("10/14週 11品を登録しました（要確認 10品）。", data["message"])
        self.assertIn("確認画面を開きます。", data["message"])
        self.assertTrue(data["url"].endswith("/menus/week/2026-10-14/"))
        self.assertEqual(Product.objects.filter(week="2026-10-14").count(), 11)
        self.assertFalse(MenuWeekCheck.objects.get(week="2026-10-14").is_confirmed)

    def test_selling_week_warns(self, _):
        data = self.post({"week": "2026-10-07", "menus": NAMES}).json()
        self.assertTrue(data["ok"])
        self.assertIn("販売中の週を上書きしました。", data["message"])

    def test_refusals(self, _):
        cases = [
            ({"week": "2026-09-30", "menus": NAMES}, "9/30週はもう終わっています。"),
            ({"week": "2026-10-15", "menus": NAMES}, "『20261015』は水曜日ではありません。"),
            ({"week": "2026-10-14", "menus": NAMES[:3] + [""] + NAMES[4:]}, "メニューの欄が空いています：④"),
            ({"week": "2026-10-14", "menus": NAMES, "extra": ["お子様ランチ", ""]}, "⑪：お子様ランチ"),
        ]
        for body, expected in cases:
            res = self.post(body)
            self.assertEqual(res.status_code, 422, expected)
            self.assertIn("送りませんでした。", res.json()["message"])
            self.assertIn(expected, res.json()["message"])
        self.assertFalse(Product.objects.exists())

    def test_auth_and_bad_body(self, _):
        self.assertEqual(self.post({"week": "2026-10-14", "menus": NAMES}, token="wrong").status_code, 401)
        res = self.client.post("/api/menu/", data="not json", content_type="application/json",
                               HTTP_AUTHORIZATION=f"Bearer {TOKEN}")
        self.assertEqual(res.status_code, 400)
