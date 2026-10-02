"""振分表マクロからの持参数自動送信API・未受信通知のテスト（仕様：lunchnetsale-持参数自動送信-振分表連携.md）。"""
import io
from datetime import date, timedelta
from unittest.mock import patch

import openpyxl
from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command
from django.test import TestCase, override_settings
from django.urls import reverse

from sales.item_quantity_import import next_business_day
from sales.models import Holiday, ItemQuantity, ItemQuantityUpload, Product, SalesLocation

TODAY = date(2026, 10, 2)  # 金曜
MONDAY = date(2026, 10, 5)
WEEK = date(2026, 9, 30)
TOKEN = "test-token-abc"
XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


def build_xlsx(target_date=MONDAY, headers=("新木場", "シーサイド"), rows=None, week=WEEK):
    """[データ変換]マクロの出力と同じ形：A1=対象wk/B1=No./C1〜=店名、2行目〜 対象wk・No・数量。"""
    rows = rows if rows is not None else [[1, 3, 5], [2, 4, 6]]
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = target_date.strftime("%Y%m%d")
    ws.append(["対象wk", "No.", *headers])
    for r in rows:
        ws.append([week.strftime("%Y%m%d"), *r])
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


@override_settings(ITEM_QUANTITY_API_TOKEN=TOKEN, ITEM_QUANTITY_IGNORED_LOCATIONS=["なだや"])
class ItemQuantityApiTests(TestCase):
    def setUp(self):
        Product.objects.create(no=1, week=WEEK, name="豚角煮")
        Product.objects.create(no=2, week=WEEK, name="メンチ")
        SalesLocation.objects.create(no=1, name="新木場", type="", price_type="", service_name="")
        SalesLocation.objects.create(no=2, name="シーサイド", type="", price_type="", service_name="")
        patcher = patch("sales.api_views.timezone.localdate", return_value=TODAY)
        patcher.start()
        self.addCleanup(patcher.stop)

    def post(self, body, token=TOKEN):
        headers = {"HTTP_AUTHORIZATION": f"Bearer {token}"} if token else {}
        return self.client.post(reverse("api_item_quantity"), data=body, content_type=XLSX, **headers)

    def quantities(self):
        return {
            (iq.product.no, iq.sales_location.name): iq.quantity
            for iq in ItemQuantity.objects.select_related("product", "sales_location")
        }

    def test_saves_same_as_screen_upload(self):
        res = self.post(build_xlsx())
        self.assertEqual(res.status_code, 200, res.json())
        self.assertEqual(res.json()["saved_count"], 4)
        self.assertEqual(self.quantities(), {
            (1, "新木場"): 3, (1, "シーサイド"): 5, (2, "新木場"): 4, (2, "シーサイド"): 6,
        })
        iq = ItemQuantity.objects.first()
        self.assertEqual(iq.target_date, "2026-10-05")  # 既存データと同じ "YYYY-MM-DD"
        self.assertEqual(iq.target_week, "2026-09-30")
        log = ItemQuantityUpload.objects.get()
        self.assertTrue(log.ok)
        self.assertEqual(log.source, "api")

    def test_unknown_location_saves_nothing(self):
        res = self.post(build_xlsx(headers=("新木場", "シー")))
        self.assertEqual(res.status_code, 422)
        self.assertIn("シー", res.json()["errors"][0])
        self.assertEqual(ItemQuantity.objects.count(), 0)
        self.assertFalse(ItemQuantityUpload.objects.get().ok)

    def test_ignored_location_is_skipped_with_warning(self):
        res = self.post(build_xlsx(headers=("新木場", "シーサイド", "なだや"), rows=[[1, 3, 5, 1], [2, 4, 6, 1]]))
        self.assertEqual(res.status_code, 200, res.json())
        self.assertEqual(res.json()["saved_count"], 4)
        self.assertIn("なだや", res.json()["warnings"][0])

    def test_blank_cell_saves_nothing(self):
        # マクロは空白を詰めるので、空白があると行が短くなり最後の店が空になる
        res = self.post(build_xlsx(rows=[[1, 3, 5], [2, 4]]))
        self.assertEqual(res.status_code, 422)
        self.assertIn("空白", " ".join(res.json()["errors"]))
        self.assertEqual(ItemQuantity.objects.count(), 0)

    def test_unknown_menu_no_saves_nothing(self):
        res = self.post(build_xlsx(rows=[[1, 3, 5], [9, 4, 6]]))
        self.assertEqual(res.status_code, 422)
        self.assertEqual(ItemQuantity.objects.count(), 0)

    def test_negative_or_fraction_rejected(self):
        res = self.post(build_xlsx(rows=[[1, -1, 5], [2, 4, 6.5]]))
        self.assertEqual(res.status_code, 422)
        self.assertEqual(len(res.json()["errors"]), 2)

    def test_missing_or_wrong_token(self):
        self.assertEqual(self.post(build_xlsx(), token=None).status_code, 401)
        self.assertEqual(self.post(build_xlsx(), token="wrong").status_code, 401)
        self.assertEqual(ItemQuantity.objects.count(), 0)

    @override_settings(ITEM_QUANTITY_API_TOKEN="")
    def test_token_not_configured_rejects(self):
        self.assertEqual(self.post(build_xlsx(), token="").status_code, 401)

    def test_past_date_rejected(self):
        res = self.post(build_xlsx(target_date=TODAY - timedelta(days=1)))
        self.assertEqual(res.status_code, 422)
        self.assertEqual(ItemQuantity.objects.count(), 0)

    def test_resend_overwrites_without_duplicates(self):
        self.post(build_xlsx())
        self.post(build_xlsx(rows=[[1, 10, 5], [2, 4, 0]]))
        self.assertEqual(ItemQuantity.objects.count(), 4)
        self.assertEqual(self.quantities()[(1, "新木場")], 10)
        self.assertEqual(self.quantities()[(2, "シーサイド")], 0)

    def test_today_publishes_status(self):
        with patch("sales.api_views.call_command") as cmd:
            res = self.post(build_xlsx(target_date=TODAY))
        cmd.assert_called_once_with("publish_status_json")
        self.assertTrue(res.json()["published"])

    def test_future_does_not_publish(self):
        with patch("sales.api_views.call_command") as cmd:
            self.post(build_xlsx())
        cmd.assert_not_called()

    def test_not_excel(self):
        res = self.post(b"not an excel file")
        self.assertEqual(res.status_code, 422)


@override_settings(ITEM_QUANTITY_IGNORED_LOCATIONS=[])
class ScreenUploadValidationTests(TestCase):
    def setUp(self):
        user = get_user_model().objects.create_user("uploader", password="pw-uploader-123456")
        self.client.force_login(user)
        Product.objects.create(no=1, week=WEEK, name="豚角煮")
        Product.objects.create(no=2, week=WEEK, name="メンチ")
        SalesLocation.objects.create(no=1, name="新木場", type="", price_type="", service_name="")
        SalesLocation.objects.create(no=2, name="シーサイド", type="", price_type="", service_name="")

    def upload(self, body):
        return self.client.post(
            reverse("upload"),
            {"item_quantity_file": SimpleUploadedFile("f.xlsx", body, content_type=XLSX)},
            follow=True,
        )

    def test_blank_cell_blocked_on_screen_too(self):
        res = self.upload(build_xlsx(rows=[[1, 3, 5], [2, 4]]))
        self.assertContains(res, "空白です")
        self.assertEqual(ItemQuantity.objects.count(), 0)

    def test_screen_allows_past_date(self):
        res = self.upload(build_xlsx(target_date=date(2026, 9, 1)))
        self.assertContains(res, "持参数データがアップロードされました")
        self.assertEqual(ItemQuantity.objects.count(), 4)
        self.assertEqual(ItemQuantityUpload.objects.get().source, "screen")

    def test_upload_page_shows_next_day_status(self):
        with patch("lunchnetsale.views.timezone.localdate", return_value=TODAY):
            res = self.client.get(reverse("upload"))
            self.assertContains(res, "10/5(月)分")
            self.assertContains(res, "まだ届いていません")
            ItemQuantityUpload.objects.create(target_date=MONDAY, source="api", ok=True, saved_count=40)
            res = self.client.get(reverse("upload"))
        self.assertContains(res, "最終受信")
        self.assertContains(res, "振分表の確定ボタン")


@override_settings(OWNER_LINE_USER_ID="Uowner")
class CheckReceivedCommandTests(TestCase):
    def run_on(self, today):
        with patch("sales.management.commands.check_item_quantity_received.timezone.localdate", return_value=today), \
             patch("sales.management.commands.check_item_quantity_received.push_text", return_value=True) as push:
            call_command("check_item_quantity_received", stdout=io.StringIO(), stderr=io.StringIO())
        return push

    def test_friday_checks_monday_and_notifies(self):
        push = self.run_on(TODAY)
        push.assert_called_once()
        self.assertEqual(push.call_args.args[0], "Uowner")
        self.assertIn("10/5(月)", push.call_args.args[1])

    def test_received_means_no_notification(self):
        ItemQuantityUpload.objects.create(target_date=MONDAY, source="api", ok=True, saved_count=40)
        self.run_on(TODAY).assert_not_called()

    def test_failed_attempt_is_included_in_message(self):
        ItemQuantityUpload.objects.create(target_date=MONDAY, source="api", ok=False, errors=["新川が空白です"])
        push = self.run_on(TODAY)
        self.assertIn("新川が空白です", push.call_args.args[1])

    def test_failed_after_success_still_notifies(self):
        # 一度確定して届いたが、確定し直しが空白で止められた
        ItemQuantityUpload.objects.create(target_date=MONDAY, source="api", ok=True, saved_count=40)
        ItemQuantityUpload.objects.create(target_date=MONDAY, source="api", ok=False, errors=["新川が空白です"])
        push = self.run_on(TODAY)
        self.assertIn("止められています", push.call_args.args[1])

    def test_skips_on_weekend(self):
        self.run_on(date(2026, 10, 3)).assert_not_called()

    def test_skips_holiday_and_company_holiday(self):
        # 2026-10-12(月)はスポーツの日 → 10/9(金)は10/13(火)分を見る
        self.assertEqual(next_business_day(date(2026, 10, 9)), date(2026, 10, 13))
        Holiday.objects.create(date=date(2026, 10, 13))
        self.assertEqual(next_business_day(date(2026, 10, 9)), date(2026, 10, 14))
        self.run_on(date(2026, 10, 13)).assert_not_called()
