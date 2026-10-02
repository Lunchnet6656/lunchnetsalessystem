"""持参数アップロード時に、今日分なら出店状況ページへ自動反映（publish_status_json）するテスト。"""
import io
from datetime import date, timedelta
from unittest.mock import patch

import openpyxl
from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from django.urls import reverse

from sales.models import ItemQuantity, Product, SalesLocation

TODAY = date(2026, 10, 2)  # 金曜（営業日）


def _item_quantity_xlsx(target_date, target_week, product_no, location_name, quantity):
    """upload_view の持参数フォーマット：シート名=日付、1行目=拠点名(C列〜)、2行目以降 A=対象週/B=商品No/C〜=数量。"""
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = target_date.strftime("%Y%m%d")
    ws.append(["対象週", "商品No", location_name])
    ws.append([target_week.strftime("%Y%m%d"), product_no, quantity])
    buf = io.BytesIO()
    wb.save(buf)
    return SimpleUploadedFile(
        "item_quantity.xlsx", buf.getvalue(),
        content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )


class UploadAutoPublishTests(TestCase):
    def setUp(self):
        user = get_user_model().objects.create_user("uploader", password="pw-uploader-123456")
        self.client.force_login(user)
        self.week = TODAY - timedelta(days=TODAY.weekday())
        Product.objects.create(no=1, week=self.week, name="唐揚げ弁当")
        SalesLocation.objects.create(no=1, name="新宿", type="", price_type="", service_name="")

    def _upload(self, target_date):
        return self.client.post(
            reverse("upload"),
            {"item_quantity_file": _item_quantity_xlsx(target_date, self.week, 1, "新宿", 10)},
            follow=True,
        )

    def test_today_upload_publishes(self):
        with patch("lunchnetsale.views.timezone.localdate", return_value=TODAY), \
             patch("lunchnetsale.views.call_command") as mock_cmd:
            res = self._upload(TODAY)
        mock_cmd.assert_called_once_with("publish_status_json")
        self.assertEqual(ItemQuantity.objects.count(), 1)
        self.assertContains(res, "出店状況ページへ反映を送りました")

    def test_future_upload_does_not_publish(self):
        with patch("lunchnetsale.views.timezone.localdate", return_value=TODAY), \
             patch("lunchnetsale.views.call_command") as mock_cmd:
            res = self._upload(TODAY + timedelta(days=1))
        mock_cmd.assert_not_called()
        self.assertEqual(ItemQuantity.objects.count(), 1)
        self.assertNotContains(res, "出店状況ページ")

    def test_publish_failure_keeps_upload(self):
        with patch("lunchnetsale.views.timezone.localdate", return_value=TODAY), \
             patch("lunchnetsale.views.call_command", side_effect=Exception("GitHub down")):
            res = self._upload(TODAY)
        self.assertEqual(ItemQuantity.objects.count(), 1)
        self.assertContains(res, "持参数データがアップロードされました")
        self.assertContains(res, "出店状況ページへの反映に失敗しました")
