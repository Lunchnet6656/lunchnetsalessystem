"""S1-3：日計表・予約の値段の決め方を sales/pricing.py に切り替えた回帰テスト。

- メニューに種類（rank）が入っていない間は、値段が今までと1円も変わらない
- 日計表を保存すると、保存時点の単価（DailyReportEntry.unit_price）が残る
- 単価を持たない古い明細は、編集画面で保存したときに画面に出していた単価で埋まる
"""
import datetime

from django.contrib.auth.models import User
from django.test import TestCase

from reservations import services as reservation_services
from lunchnetsale.forms import DailyReportForm
from sales.models import (
    DailyReport, DailyReportEntry, ItemQuantity, PriceRank, Product, SalesLocation,
)

DAY = datetime.date(2026, 10, 7)


def make_location(name="テスト売り場", price_type="B"):
    return SalesLocation.objects.create(
        no=1, name=name, type="移動販売", price_type=price_type,
        service_name="なし", service_price=0, service_style="なし",
    )


class DailyReportSendSavesUnitPriceTest(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="staff", password="pass")
        self.client.force_login(self.user)
        self.location = make_location(price_type="B")
        self.product = Product.objects.create(
            no=1, week="2026-10-07", name="唐揚げ弁当", price_A=700, price_B=650, price_C=600,
        )
        ItemQuantity.objects.create(
            target_date=DAY.isoformat(), target_week="2026-10-07",
            product=self.product, sales_location=self.location, quantity=10,
        )

    def _post(self, action):
        return self.client.post("/daily_report/", {
            "action": action, "date": DAY.isoformat(), "location": self.location.name,
            "quantity_1": "10", "sales_quantity_1": "8", "remaining_1": "2", "total_sales_1": "5,200",
            "total_revenue": "5,200", "total_discount": "0", "sales_difference": "0",
        })

    def test_display_uses_same_price_as_before(self):
        """種類が空のメニューは、今までどおり販売所の価格パターン（B）の値段で表示される。"""
        response = self._post("display")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["item_data"][0]["price"], 650)
        self.assertEqual(response.context["unique_prices"], [650])

    def test_send_saves_unit_price(self):
        self._post("send")
        entry = DailyReportEntry.objects.get(report__date=DAY, product_no=1)
        self.assertEqual(entry.unit_price, 650)

    def test_send_uses_price_table_when_rank_is_set(self):
        self.product.rank = PriceRank.objects.get(name="特選")
        self.product.save()
        self._post("send")
        entry = DailyReportEntry.objects.get(report__date=DAY, product_no=1)
        self.assertEqual(entry.unit_price, 700)  # 特選のB価格（2026-10-01版）。メニューの price_B=650 ではない


class EditFillsUnitPriceTest(TestCase):
    """移行前の古い明細（unit_price 空）を編集画面で保存すると、画面に出していた単価で埋まる。"""

    def setUp(self):
        self.user = User.objects.create_user(username="saver", password="pass")
        self.client.force_login(self.user)
        make_location(price_type="A")
        self.report = DailyReport.objects.create(
            date=DAY, location="テスト売り場", total_revenue=0, submitted_by=self.user,
        )
        self.sold = DailyReportEntry.objects.create(
            report=self.report, product_no=1, product="唐揚げ弁当",
            quantity=10, sales_quantity=4, remaining_number=6, total_sales=2600,
        )

    def test_old_entry_gets_derived_unit_price(self):
        form = DailyReportForm(instance=self.report)
        data = {name: "" if form[name].value() is None else str(form[name].value()) for name in form.fields}
        data.update({"product_1": "唐揚げ弁当", "quantity_1": "10", "sales_quantity_1": "5",
                     "remaining_1": "5", "total_sales_1": "3250"})
        response = self.client.post(f"/daily_report_detail_rol/{self.report.pk}/edit/", data)
        self.assertEqual(response.status_code, 302)
        self.sold.refresh_from_db()
        # 編集前の「売上2600÷販売数4＝650円」で残る（編集後の値で計算し直さない）
        self.assertEqual(self.sold.unit_price, 650)
        self.assertEqual(self.sold.sales_quantity, 5)


class ReservationPriceTest(TestCase):
    def setUp(self):
        self.location = make_location(price_type="C")
        self.product = Product.objects.create(
            no=2, week="2026-10-07", name="唐揚げ弁当", price_A=700, price_B=650, price_C=600,
        )

    def test_without_rank_same_as_before(self):
        self.assertEqual(reservation_services.unit_price_for(self.location, self.product, DAY), 600)
        self.assertEqual(reservation_services.unit_price_for(self.location, self.product), 600)

    def test_with_rank_uses_price_table(self):
        self.product.rank = PriceRank.objects.get(name="特選")
        self.product.save()
        self.assertEqual(reservation_services.unit_price_for(self.location, self.product, DAY), 700)
