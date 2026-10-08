"""売上ダッシュボードの販売場所別売上：販売場所No.が振り直されても同じ店は1本にまとまること。"""
import json
from datetime import date

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from sales.models import DailyReport


class DashboardLocationChartTests(TestCase):
    def setUp(self):
        user = get_user_model().objects.create_user("viewer", password="pw-viewer-123456")
        self.client.force_login(user)
        # 10/2 まではNo.21、10/5 の振り直し後はNo.22（同じ「虎ノ門」）
        DailyReport.objects.create(date=date(2026, 10, 2), location="虎ノ門", location_no=21, total_revenue=30000)
        DailyReport.objects.create(date=date(2026, 10, 5), location="虎ノ門", location_no=22, total_revenue=35000)
        # 振り直し前に No.22 を使っていた別の店
        DailyReport.objects.create(date=date(2026, 10, 1), location="高輪", location_no=22, total_revenue=40000)

    def test_same_store_is_one_bar(self):
        res = self.client.get(reverse("sales_dashboard"), {"year": 2026, "month": 10})
        labels = json.loads(res.context["location_labels"])
        revenues = json.loads(res.context["location_data"])
        bars = dict(zip(labels, revenues))
        self.assertEqual(labels.count("虎ノ門"), 1)
        self.assertEqual(bars["虎ノ門"], 65000)
        self.assertEqual(bars["高輪"], 40000)
