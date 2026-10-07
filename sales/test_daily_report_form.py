"""S2-2：日計表入力フォーム（割引欄の自動生成・サーバー側の計算・割引明細の保存）のテスト。"""
import datetime

from django.contrib.auth.models import User
from django.test import TestCase

from sales.models import DailyReport, DailyReportEntry, ItemQuantity, PriceRank, Product, SalesLocation

DAY = datetime.date(2026, 10, 7)


class DailyReportFormTest(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="staff", password="pass")
        self.client.force_login(self.user)
        ranks = {r.name: r for r in PriceRank.objects.all()}
        self.menu = [
            Product.objects.create(no=1, week="2026-10-07", name="★カキフライ", rank=ranks["特選"]),
            Product.objects.create(no=2, week="2026-10-07", name="唐揚げ", rank=ranks["通常"]),
            Product.objects.create(no=10, week="2026-10-07", name="五目チャーハン", rank=ranks["お手頃"]),
            Product.objects.create(no=11, week="2026-10-07", name="大盛りごはん", rank=ranks["大盛り"]),
        ]

    def make_location(self, name, price_type, style="なし", service_price=0):
        loc = SalesLocation.objects.create(
            no=1, name=name, type="移動販売", price_type=price_type,
            service_name="カレーセット" if style != "なし" else "なし",
            service_price=service_price, service_style=style,
        )
        for p in self.menu:
            ItemQuantity.objects.create(target_date=DAY.isoformat(), target_week="2026-10-07",
                                        product=p, sales_location=loc, quantity=10)
        return loc

    def post(self, loc, action, **extra):
        # 新しい画面は割引欄の一覧（discount_signature）を必ず送る
        data = {"action": action, "date": DAY.isoformat(), "location": loc.name,
                "paypay": "0", "digital_payment": "0", "cash": "0", "discount_signature": "new-form"}
        for p in self.menu:
            data[f"quantity_{p.no}"] = "10"
            data[f"remaining_{p.no}"] = "10"
        data.update(extra)
        return self.client.post("/daily_report/", data)

    def test_display_price_a(self):
        loc = self.make_location("広尾", "A")
        res = self.post(loc, "display")
        coupons = [f.label for g, _, fields in res.context["discount_groups"] if g == "coupon" for f in fields]
        self.assertEqual(coupons, ["750円", "700円", "650円"])
        self.assertEqual([r["price"] for r in res.context["price_rows"]], [750, 700, 650, 50])
        self.assertContains(res, "50円（大盛り）")
        self.assertContains(res, 'data-unit="-750"')

    def test_display_price_c(self):
        loc = self.make_location("KONO", "C")
        res = self.post(loc, "display")
        coupons = [f.label for g, _, fields in res.context["discount_groups"] if g == "coupon" for f in fields]
        self.assertEqual(coupons, ["700円", "600円"])

    def test_send_recalculates_and_saves_lines(self):
        loc = self.make_location("広尾", "A", style="サービス", service_price=500)
        self.post(loc, "send", **{
            "remaining_1": "5", "remaining_2": "0", "remaining_11": "4",
            "disc_coupon_700": "1", "disc_service_650": "2",
            # 画面のJSが古くて違う値を送ってきても、サーバーの計算で保存する
            "total_discount": "0", "total_revenue": "1", "sales_difference": "0",
            "cash": "10000",
        })
        report = DailyReport.objects.get(date=DAY, location="広尾")
        # 売上：750×5 + 700×10 + 50×6 = 11,050／割引：-700 + (500-650)×2 = -1,000
        self.assertEqual(int(report.total_discount), -1000)
        self.assertEqual(int(report.total_revenue), 10050)
        self.assertEqual(int(report.sales_difference), -50)
        self.assertEqual(int(report.total_sales_quantity), 15)  # 大盛りを除く
        self.assertEqual(int(report.coupon_type_700), 1)       # 旧欄にも書く
        self.assertEqual(int(report.service_type_600), 2)      # 650円＝旧「600」欄
        self.assertEqual(int(report.service_price), 500)
        self.assertEqual(
            sorted(report.discount_lines.values_list("label", "unit_amount", "quantity")),
            [("クーポン700円", -700, 1), ("サービス650円", -150, 2)],
        )
        entry = DailyReportEntry.objects.get(report=report, product_no=1)
        self.assertEqual((entry.sales_quantity, int(entry.total_sales), int(entry.unit_price)), (5, 3750, 750))

    def test_send_rejects_stale_location(self):
        """A価格の販売所で表示した750円クーポンを、C価格の販売所に切り替えて送ると止める。"""
        loc = self.make_location("KONO", "C")
        res = self.post(loc, "send", disc_coupon_750="1")
        self.assertRedirects(res, "/daily_report/", fetch_redirect_response=False)
        self.assertFalse(DailyReport.objects.filter(location="KONO").exists())


class LegacyFormSubmissionTest(DailyReportFormTest):
    """デプロイ前から開いていた古い画面（割引欄が coupon_type_700 などの旧名）から送られても、割引を0にしない。"""

    def test_old_input_form(self):
        loc = self.make_location("広尾", "A")
        data = {"action": "send", "date": DAY.isoformat(), "location": loc.name,
                "paypay": "0", "digital_payment": "0", "cash": "0",
                "coupon_type_700": "1", "extra_rice_quantity": "2", "coupon_type_600": "1"}
        for p in self.menu:
            data[f"quantity_{p.no}"] = "10"
            data[f"remaining_{p.no}"] = "10"
        self.client.post("/daily_report/", data)  # discount_signature なし＝古い画面
        report = DailyReport.objects.get(location="広尾")
        self.assertEqual(int(report.total_discount), -700 + 200 - 650)
        self.assertEqual(int(report.coupon_type_700), 1)
        self.assertEqual(sorted(report.discount_lines.values_list("label", "quantity")),
                         [("クーポン650円", 1), ("クーポン700円", 1), ("追加", 2)])

    # 親クラスのテストはここでは流さない
    test_display_price_a = test_display_price_c = None
    test_send_recalculates_and_saves_lines = test_send_rejects_stale_location = None
