"""S2-3：日計表の編集画面（保存済みの単価・割引明細で表示し、保存時にサーバーで計算し直す）のテスト。"""
import datetime

from django.contrib.auth.models import User
from django.test import TestCase

from lunchnetsale.forms import DailyReportForm
from sales.discounts import legacy_lines, save_lines
from sales.models import DailyReport, DailyReportEntry, DiscountItem, SalesLocation


class DailyReportEditTest(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="staff", password="pass")
        self.client.force_login(self.user)
        SalesLocation.objects.create(no=1, name="竹芝", type="移動販売", price_type="A",
                                     service_name="なし", service_price=0, service_style="なし")

    def make_report(self, day, **fields):
        report = DailyReport.objects.create(date=day, location="竹芝", submitted_by=self.user,
                                            total_revenue=0, **fields)
        DailyReportEntry.objects.create(report=report, product_no=1, product="唐揚げ", quantity=10,
                                        sales_quantity=8, remaining_number=2, total_sales=4800, unit_price=600)
        DailyReportEntry.objects.create(report=report, product_no=11, product="大盛りごはん", quantity=10,
                                        sales_quantity=4, remaining_number=6, total_sales=200, unit_price=50)
        save_lines(report, legacy_lines(report))
        return report

    def post_edit(self, report, **extra):
        form = DailyReportForm(instance=report)
        data = {n: "" if form[n].value() is None else str(form[n].value()) for n in form.fields}
        data.update({"product_1": "唐揚げ", "quantity_1": "10", "remaining_1": "2",
                     "product_11": "大盛りごはん", "quantity_11": "10", "remaining_11": "6",
                     "discount_signature": "new-form"})
        data.update(extra)
        return self.client.post(f"/daily_report_detail_rol/{report.pk}/edit/", data)

    def test_old_report_shows_old_coupon_price(self):
        report = self.make_report(datetime.date(2025, 4, 10), coupon_type_600=1)
        res = self.client.get(f"/daily_report_detail_rol/{report.pk}/edit/")
        self.assertContains(res, "この日（2025/04/10）の値段で表示しています。")
        self.assertContains(res, 'name="disc_coupon_600"')
        self.assertContains(res, 'data-unit="-600"')
        self.assertEqual([r["price"] for r in res.context["price_rows"]], [600, 50])

    def test_save_keeps_old_amounts(self):
        """2025年4月（600円クーポン）の日計表を何も変えずに保存しても、金額が変わらない。"""
        report = self.make_report(datetime.date(2025, 4, 10), coupon_type_600=1,
                                  total_discount=-600)
        report.total_revenue = 4400
        report.save()
        res = self.post_edit(report, disc_coupon_600="1")
        self.assertEqual(res.status_code, 302)
        report.refresh_from_db()
        self.assertEqual((int(report.total_discount), int(report.total_revenue)), (-600, 4400))
        self.assertEqual(int(report.coupon_type_600), 1)
        entry = report.entries.get(product_no=1)
        self.assertEqual((int(entry.unit_price), int(entry.total_sales)), (600, 4800))

    def test_save_fixes_plus_saved_as_zero(self):
        """全角「＋」で割引合計0円になっていた日計表は、保存し直すと正しい＋100円になる（総売上はもともと正しい）。"""
        report = self.make_report(datetime.date(2025, 8, 1), extra_rice_quantity=1, total_discount=0)
        extra_rice = DiscountItem.objects.get(group="rice", label="追加")
        res = self.post_edit(report, **{f"disc_item_{extra_rice.id}": "1"})
        self.assertEqual(res.status_code, 302)
        report.refresh_from_db()
        self.assertEqual((int(report.total_discount), int(report.total_revenue)), (100, 5100))

    def test_unknown_unit_price_uses_screen_total(self):
        """単価が分からない古い明細は、売上を0円にせず、画面の売上から単価を出す。"""
        report = self.make_report(datetime.date(2026, 10, 7))
        DailyReportEntry.objects.filter(report=report, product_no=1).update(
            unit_price=None, sales_quantity=0, remaining_number=10, total_sales=0)
        res = self.post_edit(report, total_sales_1="3500", remaining_1="5")
        self.assertEqual(res.status_code, 302)
        entry = report.entries.get(product_no=1)
        self.assertEqual((int(entry.unit_price), entry.sales_quantity, int(entry.total_sales)), (700, 5, 3500))


class LegacyEditFormTest(DailyReportEditTest):
    """古い編集画面（旧名の割引欄）から保存されても、割引を0にしない。"""

    def test_old_edit_form(self):
        report = self.make_report(datetime.date(2026, 10, 6), coupon_type_750=1, total_discount=-750)
        form = DailyReportForm(instance=report)
        data = {n: "" if form[n].value() is None else str(form[n].value()) for n in form.fields}
        data.update({"product_1": "唐揚げ", "quantity_1": "10", "remaining_1": "2",
                     "product_11": "大盛りごはん", "quantity_11": "10", "remaining_11": "6",
                     "coupon_type_750": "2"})  # discount_signature なし＝古い画面
        res = self.client.post(f"/daily_report_detail_rol/{report.pk}/edit/", data)
        self.assertEqual(res.status_code, 302)
        report.refresh_from_db()
        self.assertEqual((int(report.total_discount), int(report.coupon_type_750)), (-1500, 2))
        self.assertEqual(list(report.discount_lines.values_list("label", "quantity")), [("クーポン750円", 2)])

    test_old_report_shows_old_coupon_price = test_save_keeps_old_amounts = None
    test_save_fixes_plus_saved_as_zero = test_unknown_unit_price_uses_screen_total = None
