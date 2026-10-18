"""D-2の下書き（振分表36行目）の記録と、食数予測での比較のテスト。"""
from datetime import date, timedelta
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TestCase, override_settings
from django.urls import reverse

from sales.models import DailyReport, ItemQuantity, ItemQuantityUpload, QuantityDraft, SalesLocation
from sales.permissions_testing import grant

TODAY = date(2026, 10, 2)   # 金曜。次の営業日は 10/5（月）
TOKEN = "draft-token"


@override_settings(ITEM_QUANTITY_API_TOKEN=TOKEN)
class DraftApiTests(TestCase):
    def setUp(self):
        SalesLocation.objects.create(no=1, name="新木場", type="車", price_type="A", service_name="")
        SalesLocation.objects.create(no=2, name="シーサイド", type="行商", price_type="A", service_name="")
        p = patch("sales.api_views.timezone.localdate", return_value=TODAY)
        p.start()
        self.addCleanup(p.stop)

    def post(self, body, token=TOKEN):
        headers = {"HTTP_AUTHORIZATION": f"Bearer {token}"} if token else {}
        return self.client.post(reverse("api_draft_quantity"), data=body.encode("utf-8"),
                                content_type="text/plain; charset=utf-8", **headers)

    def test_saves_and_overwrites_by_name(self):
        res = self.post("20261005\n新木場\t40\nシーサイド\t95\n")
        self.assertEqual(res.status_code, 200, res.json())
        self.assertEqual(res.json()["total"], 135)
        self.assertIn("2店", res.json()["message"])
        self.post("20261005\n新木場\t42\nシーサイド\t95\n")
        self.assertEqual(QuantityDraft.objects.count(), 2)
        self.assertEqual(QuantityDraft.objects.get(location="新木場").quantity, 42)

    def test_unknown_store_is_skipped(self):
        res = self.post("20261005\n新木場\t40\nなだや\t3\n")
        self.assertEqual(res.status_code, 200)
        self.assertIn("なだや", res.json()["message"])
        self.assertEqual(QuantityDraft.objects.count(), 1)

    def test_does_not_touch_item_quantity_or_receipts(self):
        self.post("20261005\n新木場\t40\n")
        self.assertEqual(ItemQuantity.objects.count(), 0)
        self.assertEqual(ItemQuantityUpload.objects.count(), 0)

    def test_auth_and_past_date(self):
        self.assertEqual(self.post("20261005\n新木場\t40\n", token=None).status_code, 401)
        self.assertEqual(self.post("20261005\n新木場\t40\n", token="x").status_code, 401)
        self.assertEqual(self.post("20261001\n新木場\t40\n").status_code, 422)
        self.assertEqual(self.post("あいうえお\n").status_code, 422)
        self.assertEqual(QuantityDraft.objects.count(), 0)


class DraftComparisonPageTests(TestCase):
    def setUp(self):
        cache.clear()
        user = get_user_model().objects.create_user("viewer", password="pw-viewer-123456")
        grant(user, "can_view_performance_by_location")
        self.client.force_login(user)
        SalesLocation.objects.create(no=1, name="A店", type="室内", price_type="A", service_name="")
        SalesLocation.objects.create(no=2, name="B店", type="室内", price_type="A", service_name="")
        # どちらも毎日100食売れて、いつも5食残る（＝余裕分5）
        d = TODAY - timedelta(days=28)
        while d < TODAY:
            if d.weekday() < 5:
                for no, name in ((1, "A店"), (2, "B店")):
                    DailyReport.objects.create(date=d, location=name, location_no=no, total_quantity=105,
                                               total_sales_quantity=100, total_remaining=5, total_revenue=0)
            d += timedelta(days=1)

    def get(self):
        with patch("lunchnetsale.views.timezone.localdate", return_value=TODAY), \
             patch("lunchnetsale.views._fetch_lunch_weather", return_value={}):
            return self.client.get(reverse("meal_forecast"))

    def test_no_draft_no_columns(self):
        res = self.get()
        self.assertFalse(res.context["fc"]["days"][0]["has_draft"])
        self.assertNotContains(res, "半々の目安</th>")

    def test_buffer_is_removed_before_comparing(self):
        monday = date(2026, 10, 5)
        QuantityDraft.objects.create(target_date=monday, location="A店", quantity=105)  # 予測100＋余裕5＝ぴったり
        QuantityDraft.objects.create(target_date=monday, location="B店", quantity=140)  # 余裕分を引いても35多い
        res = self.get()
        day = res.context["fc"]["days"][0]
        locs = {l["name"]: l["draft"] for l in day["locs"]}
        self.assertEqual(locs["A店"]["buffer"], 5)
        self.assertEqual(locs["A店"]["diff"], 0)
        self.assertIsNone(locs["A店"]["flag"])
        self.assertEqual(locs["B店"]["diff"], 35)
        self.assertEqual(locs["B店"]["flag"], "多い")
        self.assertEqual(locs["B店"]["mid"], 123)   # (100 + 135)/2 + 5 = 122.5 → 123（偶数丸めで122も可）
        self.assertTrue(day["has_draft"])
        self.assertEqual(day["draft_total"], 245)
        self.assertEqual(day["draft_flags"], 1)
        self.assertContains(res, "要確認：多い")
        # 下書きは翌営業日以降の欄には出ない（対象日だけ）
        self.assertFalse(res.context["fc"]["days"][1]["has_draft"])
