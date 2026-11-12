"""販売フィー計算書（#206）のテスト。数字は見本.xlsx（シート202610）の10月分。"""
import datetime
from decimal import Decimal

from django.contrib.auth.models import User
from django.db import IntegrityError, transaction
from django.test import TestCase, TransactionTestCase
from django.urls import reverse, reverse_lazy
from django.utils import timezone

from sales.models import (DailyReport, DailyReportDiscountLine, DailyReportEntry, Holiday, IssuerSettings,
                          SalesLocation)
from sales.permissions_testing import grant
from shifts.models import CompanyHoliday

from . import calc, pdf, services
from .models import FeeDestination, FeeRate, FeeStatement, default_pay_date

OCT = datetime.date(2026, 10, 1)
# 見本.xlsx 202610：日 → (750円, 700円, 650円, 大盛, 残数)
SAMPLE = {1: (2, 51, 2, 7, 3), 2: (4, 56, 2, 10, 0), 5: (2, 46, 2, 7, 7), 6: (2, 41, 2, 10, 3),
          7: (5, 39, 3, 8, 5), 8: (5, 42, 3, 9, 0), 9: (6, 40, 2, 10, 2), 13: (6, 44, 2, 8, 0),
          14: (3, 49, 3, 10, 0), 15: (3, 38, 1, 8, 12), 16: (4, 40, 2, 10, 9), 19: (3, 44, 3, 10, 0),
          20: (3, 46, 3, 8, 0), 21: (1, 47, 3, 10, 5), 22: (3, 46, 2, 6, 4), 23: (3, 48, 2, 7, 3),
          26: (0, 50, 3, 8, 17), 27: (3, 42, 3, 10, 4), 28: (5, 44, 3, 10, 0), 29: (5, 43, 2, 9, 1),
          30: (6, 43, 2, 10, 1)}


def make_report(location, day, counts, month=OCT, confirmed=True, unit_prices=(750, 700, 650, 50), **extra):
    d = month.replace(day=day)
    a, b, c, large, remaining = counts
    report = DailyReport.objects.create(date=d, location=location.name, location_no=location.no,
                                        total_revenue=0, confirmed=confirmed, **extra)
    for no, qty, price, rem in ((1, a, unit_prices[0], 0), (2, b, unit_prices[1], remaining),
                                (3, c, unit_prices[2], 0), (11, large, unit_prices[3], 0)):
        DailyReportEntry.objects.create(report=report, product_no=no, product=f"商品{no}",
                                        quantity=qty + rem, sales_quantity=qty, remaining_number=rem,
                                        total_sales=0, unit_price=price)
    return report


def load_sample(location):
    for day, counts in SAMPLE.items():
        make_report(location, day, counts)


class Base(TestCase):
    def setUp(self):
        self.loc = SalesLocation.objects.create(no=12, name="○○ビル", type="室内", price_type="A",
                                                service_name="社員割", service_price=500, service_style="固定")
        self.dest = FeeDestination.objects.create(location=self.loc, enabled=True, addressee="A株式会社")
        FeeRate.objects.create(destination=self.dest, start_month=datetime.date(2026, 4, 1), rate=Decimal("10"))
        Holiday.objects.create(date=datetime.date(2026, 10, 12), description="スポーツの日")
        self.user = User.objects.create_user("keiri", password="pw", last_name="田中", first_name="将平")
        grant(self.user, "can_issue_fees")

    def detail(self, include_service=True, month=OCT):
        ctx = services.MonthContext.load(month, today=datetime.date(2026, 11, 12))
        return services.build_detail(self.loc, ctx, include_service)[0]

    def issue(self, month=OCT, locations=None):
        return services.issue_statements(month, locations or [self.loc.pk], datetime.date(2026, 11, 12),
                                         datetime.date(2026, 11, 10), self.user)


class AmountTests(TestCase):
    def test_sample_october(self):
        a = calc.fee_amounts(754550, Decimal("10"))
        self.assertEqual(a, {"total_sales": 754550, "tax_excluded": 698657, "tax_amount": 55893,
                             "fee_excluded": 69865, "fee_tax": 6986, "pay_amount": 76851})

    def test_rate_zero_like_sample(self):
        a = calc.fee_amounts(754550, Decimal("0"))
        self.assertEqual((a["tax_excluded"], a["tax_amount"], a["pay_amount"]), (698657, 55893, 0))

    def test_rounding_is_floor_everywhere(self):
        # 税抜 1000/1.08=925.9→925、手数料 925×10.5%=97.125→97、手数料消費税 9.7→9
        a = calc.fee_amounts(1000, Decimal("10.5"))
        self.assertEqual((a["tax_excluded"], a["tax_amount"], a["fee_excluded"], a["fee_tax"], a["pay_amount"]),
                         (925, 75, 97, 9, 106))


class ComposeTests(Base):
    def test_sample_month_totals(self):
        load_sample(self.loc)
        d = self.detail()
        self.assertEqual(d["totals"]["amount"], 754550)
        self.assertEqual(d["prices"], [750, 700, 650])
        self.assertEqual(d["large_prices"], [50])
        self.assertEqual(d["totals"]["counts"], [74, 939, 50])
        self.assertEqual(d["totals"]["large"], [185])
        self.assertEqual(d["totals"]["sold"], 1063)      # 大盛りを含めない
        self.assertEqual(d["totals"]["remaining"], 76)
        self.assertEqual(len(d["days"]), 31)
        day1 = d["days"][0]
        self.assertEqual((day1["sold"], day1["amount"], day1["remaining"]), (55, 38850, 3))
        self.assertFalse(d["has_others"])
        self.assertEqual(d["service_names"], [])

    def test_holidays_and_weekends(self):
        load_sample(self.loc)
        days = {r["day"]: r for r in self.detail()["days"]}
        self.assertEqual(days[12]["dow"], "月祝")
        self.assertTrue(days[12]["shade"] and days[12]["bold"])
        self.assertTrue(days[3]["shade"] and not days[3]["bold"])      # 土
        self.assertTrue(days[4]["bold"])                               # 日
        self.assertFalse(days[3]["has_report"])
        self.assertFalse(days[5]["shade"])

    def test_price_columns_only_sold_prices(self):
        # 650円が0個の月は列にしない。値上げの月は旧値段と新値段が並ぶ
        make_report(self.loc, 1, (2, 5, 0, 0, 0))
        make_report(self.loc, 2, (1, 3, 0, 0, 0), unit_prices=(800, 700, 650, 50))
        d = self.detail()
        self.assertEqual(d["prices"], [800, 750, 700])
        self.assertEqual(d["large_prices"], [])

    def test_others_sales_are_added(self):
        make_report(self.loc, 1, (0, 10, 0, 0, 0), others_sales_1="お茶", others_price1=100,
                    others_sales_quantity1=12, others_sales_2="スープ", others_price2=150, others_sales_quantity2=2)
        d = self.detail()
        self.assertTrue(d["has_others"])
        self.assertEqual(d["days"][0]["others"], 1500)
        self.assertEqual(d["totals"]["amount"], 7000 + 1500)
        self.assertEqual(d["days"][0]["sold"], 10)
        names = [(o["name"], o["qty"], o["amount"]) for o in d["others_breakdown"]]
        self.assertEqual(names, [("スープ", 2, 300), ("お茶", 12, 1200)])

    def _service(self, report, qty=3):
        DailyReportDiscountLine.objects.create(report=report, group="service", label="サービス700円",
                                               base_price=700, unit_amount=-200, quantity=qty, amount=-200 * qty)
        # クーポン・返金は総売上から引かない
        DailyReportDiscountLine.objects.create(report=report, group="coupon", label="クーポン700円",
                                               base_price=700, unit_amount=-700, quantity=2, amount=-1400)

    def test_service_on_subtracts_with_location_service_name(self):
        r = make_report(self.loc, 1, (0, 10, 0, 0, 0))
        self._service(r)
        d = self.detail(include_service=True)
        self.assertEqual(d["service_names"], ["社員割"])
        self.assertEqual(d["days"][0]["services"], [-600])
        self.assertEqual(d["totals"]["amount"], 7000 - 600)
        line = pdf.table_context(d)["service_lines"][0]
        self.assertEqual(line, "社員割の内訳：¥700→¥500 ×3 −¥600")

    def test_service_off_keeps_total(self):
        r = make_report(self.loc, 1, (0, 10, 0, 0, 0))
        self._service(r)
        d = self.detail(include_service=False)
        self.assertEqual(d["service_names"], [])
        self.assertEqual(d["totals"]["amount"], 7000)

    def test_reports_matched_by_current_name_not_no(self):
        other = SalesLocation.objects.create(no=12, name="別の店", type="車", price_type="A", service_name="なし")
        make_report(other, 1, (0, 10, 0, 0, 0))   # 同じNo.の別の店（振り直し）
        make_report(self.loc, 2, (0, 1, 0, 0, 0))
        self.assertEqual(self.detail()["totals"]["amount"], 700)


class CheckTests(Base):
    def rows(self):
        return services.issue_rows(OCT, today=datetime.date(2026, 11, 12))[0]

    def test_missing_unit_price_blocks(self):
        r = make_report(self.loc, 1, (0, 10, 0, 0, 0))
        DailyReportEntry.objects.filter(report=r, product_no=2).update(unit_price=None)
        row = self.rows()[0]
        self.assertEqual(row.blocks[0][1], "単価なし 1日")
        self.assertFalse(row.selectable)

    def test_unconfirmed_is_unchecked_by_default(self):
        load_sample(self.loc)
        DailyReport.objects.filter(date__day__in=[5, 6]).update(confirmed=False)
        row = self.rows()[0]
        self.assertIn(("unconfirmed", "未確認 2日", [datetime.date(2026, 10, 5), datetime.date(2026, 10, 6)]),
                      row.warns)
        self.assertTrue(row.selectable)
        self.assertFalse(row.default_checked)

    def test_unsubmitted_skips_weekend_holiday_and_company_holiday(self):
        load_sample(self.loc)
        DailyReport.objects.filter(date=datetime.date(2026, 10, 20)).delete()
        CompanyHoliday.objects.create(date=datetime.date(2026, 10, 21), name="創立記念日")
        DailyReport.objects.filter(date=datetime.date(2026, 10, 21)).delete()
        row = self.rows()[0]
        # 10/12（祝）・土日・10/21（会社休日）は数えない
        self.assertEqual(row.check["unsubmitted"], [datetime.date(2026, 10, 20)])
        self.assertTrue(row.default_checked)   # 警告だけ・既定ON

    def test_holiday_from_jpholiday_without_holiday_table(self):
        # 本番は Holiday 表が2025年6月で止まっている。表になくても 10/12（スポーツの日）は祝日
        Holiday.objects.all().delete()
        load_sample(self.loc)
        row = self.rows()[0]
        self.assertEqual(row.check["unsubmitted"], [])
        day12 = next(d for d in row.detail["days"] if d["day"] == 12)
        self.assertIn("祝", day12["dow"])

    def test_unsubmitted_skips_shift_rest_and_cancel(self):
        from shifts.models import ShiftAssignment
        load_sample(self.loc)
        DailyReport.objects.filter(date__in=[datetime.date(2026, 10, 20), datetime.date(2026, 10, 21),
                                             datetime.date(2026, 10, 22)]).delete()
        ShiftAssignment.objects.create(date=datetime.date(2026, 10, 20), sales_location=self.loc, special_type="REST")
        ShiftAssignment.objects.create(date=datetime.date(2026, 10, 21), sales_location=self.loc, special_type="CANCEL")
        row = self.rows()[0]
        self.assertEqual(row.check["unsubmitted"], [datetime.date(2026, 10, 22)])

    def test_no_reports_is_warning_and_unchecked(self):
        row = self.rows()[0]
        self.assertEqual([w[1] for w in row.warns], ["日計表なし"])
        self.assertTrue(row.selectable)
        self.assertFalse(row.default_checked)

    def test_missing_addressee_and_rate(self):
        self.dest.addressee = ""
        self.dest.save()
        self.dest.rates.all().delete()
        load_sample(self.loc)
        labels = [b[1] for b in self.rows()[0].blocks]
        self.assertEqual(labels, ["宛名なし", "率なし"])

    def test_rate_by_start_month(self):
        FeeRate.objects.create(destination=self.dest, start_month=datetime.date(2026, 11, 1), rate=Decimal("8.5"))
        self.assertEqual(self.dest.rate_on(OCT), Decimal("10"))
        self.assertEqual(self.dest.rate_on(datetime.date(2026, 11, 30)), Decimal("8.5"))
        self.assertIsNone(self.dest.rate_on(datetime.date(2026, 3, 1)))

    def test_diff_after_daily_report_changed(self):
        load_sample(self.loc)
        self.issue()
        DailyReportEntry.objects.filter(report__date=datetime.date(2026, 10, 1), product_no=2).update(sales_quantity=52)
        row = self.rows()[0]
        self.assertEqual(row.diff_total, 754550 + 700)
        self.assertFalse(row.selectable)


class IssueTests(Base):
    def test_issue_snapshot(self):
        load_sample(self.loc)
        st, = self.issue()
        self.assertEqual(st.number, "FEE-202610-0001")
        self.assertEqual((st.total_sales, st.tax_excluded, st.tax_amount, st.fee_excluded, st.fee_tax, st.pay_amount),
                         (754550, 698657, 55893, 69865, 6986, 76851))
        self.assertEqual(st.staff_name, "田中　将平")
        self.assertEqual(st.addressee, "A株式会社")
        self.assertEqual(st.issuer["company_name"], IssuerSettings.load().company_name)
        self.assertEqual(st.issuer["registration_number"], "")   # 既定OFF
        # 後で日計表・宛名を直しても発行済みは変わらない
        DailyReport.objects.all().delete()
        self.dest.addressee = "B社"
        self.dest.save()
        st.refresh_from_db()
        self.assertEqual(st.total_sales, 754550)
        self.assertEqual(len(st.detail["days"]), 31)

    def test_registration_number_when_enabled(self):
        s = IssuerSettings.load()
        s.show_registration_on_fee = True
        s.save()
        load_sample(self.loc)
        st, = self.issue()
        self.assertEqual(st.issuer["registration_number"], s.registration_number)

    def test_number_is_target_month_and_not_reused(self):
        load_sample(self.loc)
        loc2 = SalesLocation.objects.create(no=15, name="△△工場", type="車", price_type="A", service_name="なし")
        d2 = FeeDestination.objects.create(location=loc2, enabled=True, addressee="B株式会社")
        FeeRate.objects.create(destination=d2, start_month=OCT, rate=Decimal("8"))
        make_report(loc2, 1, (0, 5, 0, 0, 0))
        a, b = self.issue(locations=[self.loc.pk, loc2.pk])
        self.assertEqual((a.number, b.number), ("FEE-202610-0001", "FEE-202610-0002"))
        services.void_statement(a, self.user, "日計表の修正")
        again, = self.issue()
        self.assertEqual(again.number, "FEE-202610-0003")
        self.assertEqual(again.replaces, a)
        nov, = self.issue(month=datetime.date(2026, 11, 1))
        self.assertEqual(nov.number, "FEE-202611-0001")

    def test_double_issue_is_blocked(self):
        load_sample(self.loc)
        self.issue()
        with self.assertRaises(services.FeeIssueError) as e:
            self.issue()
        self.assertIn("○○ビル（発行済み）", str(e.exception))
        self.assertEqual(FeeStatement.objects.count(), 1)

    def test_db_constraint_one_issued_per_month_location(self):
        load_sample(self.loc)
        st, = self.issue()
        st.pk = None
        st.number = "FEE-202610-0099"
        with self.assertRaises(IntegrityError), transaction.atomic():
            st.save()

    def test_one_failure_rolls_back_all(self):
        load_sample(self.loc)
        loc2 = SalesLocation.objects.create(no=15, name="△△工場", type="車", price_type="A", service_name="なし")
        FeeDestination.objects.create(location=loc2, enabled=True, addressee="")   # 宛名なし
        with self.assertRaises(services.FeeIssueError) as e:
            self.issue(locations=[self.loc.pk, loc2.pk])
        self.assertIn("△△工場", str(e.exception))
        self.assertEqual(FeeStatement.objects.count(), 0)

    def test_default_pay_date(self):
        # 発行日より後の、いちばん近い10日
        self.assertEqual(default_pay_date(datetime.date(2026, 11, 12)), datetime.date(2026, 12, 10))
        self.assertEqual(default_pay_date(datetime.date(2026, 11, 5)), datetime.date(2026, 11, 10))
        self.assertEqual(default_pay_date(datetime.date(2026, 11, 10)), datetime.date(2026, 12, 10))
        self.assertEqual(default_pay_date(datetime.date(2026, 12, 20)), datetime.date(2027, 1, 10))

    def test_future_month_is_not_issued(self):
        make_report(self.loc, 1, (0, 5, 0, 0, 0), month=datetime.date(2099, 1, 1))
        with self.assertRaises(services.FeeIssueError):
            self.issue(month=datetime.date(2099, 1, 1))
        self.assertEqual(FeeStatement.objects.count(), 0)

    def test_service_name_is_snapshotted(self):
        r = make_report(self.loc, 1, (0, 10, 0, 0, 0))
        DailyReportDiscountLine.objects.create(report=r, group="service", label="サービス700円",
                                               base_price=700, unit_amount=-200, quantity=3, amount=-600)
        st, = self.issue()
        self.loc.service_name = "別の名前"
        self.loc.save()
        st.refresh_from_db()
        self.assertIn("社員割<br>（金額）", pdf.render_html(st))
        self.assertIn("社員割の内訳", pdf.table_context(st.detail)["service_lines"][0])


class PdfTests(Base):
    def test_sample_fits_one_page_and_shows_target_month(self):
        load_sample(self.loc)
        st, = self.issue()
        html = pdf.render_html(st)
        self.assertIn("計　算　書　（2026年10月分）", html)
        self.assertIn("2026年11月12日", html)
        self.assertIn("No. FEE-202610-0001", html)
        self.assertIn("A株式会社　御中", html)
        self.assertIn("担当　田中　将平", html)
        self.assertIn("お支払日：2026年11月10日", html)
        self.assertIn("＊軽減税率対象", html)
        self.assertNotIn("登録番号", html)
        self.assertIn("+¥50", html)
        self.assertEqual(pdf.page_count(st), 1)

    def test_widest_month_fits_one_page(self):
        # 31日・値段5本＋大盛2本＋その他＋サービス・長い宛名・社印
        s = IssuerSettings.load()
        s.seal_image_data = "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
        s.show_registration_on_fee = True
        s.save()
        self.dest.addressee = "株式会社とても長い名前のテナント管理会社"
        self.dest.save()
        prices = [(800, 750, 700, 60), (650, 600, 700, 50)]
        for day in range(1, 32):
            r = make_report(self.loc, day, (9, 99, 9, 9, 9), unit_prices=prices[day % 2],
                            others_sales_1="お茶", others_price1=100, others_sales_quantity1=3)
            DailyReportDiscountLine.objects.create(report=r, group="service", label="サービス700円",
                                                   base_price=700, unit_amount=-200, quantity=2, amount=-400)
        st, = self.issue()
        self.assertEqual(len(st.detail["prices"]), 5)
        self.assertEqual(pdf.page_count(st), 1)
        html = pdf.render_html(st)
        self.assertIn('class="seal"', html)
        self.assertIn("登録番号", html)
        self.assertIn("社員割<br>（金額）", html)

    def test_void_and_preview_marks(self):
        load_sample(self.loc)
        st, = self.issue()
        services.void_statement(st, self.user)
        self.assertIn('<span class="mark">取消</span>', pdf.render_html(st))
        pv = services.preview_statement(self.dest, OCT)
        html = pdf.render_html(pv, preview=True)
        self.assertIn('<span class="mark">プレビュー</span>', html)
        self.assertIn("（発行時に採番）", html)

    def test_closed_days_are_blank(self):
        load_sample(self.loc)
        st, = self.issue()
        rows = pdf.table_context(st.detail)["rows"]
        self.assertTrue(all(c == "" for c in rows[2]["cells"]))   # 10/3（土）休業
        self.assertEqual(rows[0]["cells"][-3:], ["55", "¥38,850", "3"])


class ViewTests(Base):
    def setUp(self):
        super().setUp()
        self.viewer = User.objects.create_user("viewer", password="pw")
        grant(self.viewer, "can_view_fees")
        self.nobody = User.objects.create_user("nobody", password="pw")

    URL = reverse_lazy("fees:list")

    def page(self, month="2026-10"):
        return self.client.get(f"{self.URL}?month={month}")

    def post_issue(self, month="2026-10", issue_date="2026-11-12", pay_date="2026-12-10", locations=None):
        return self.client.post(f"{self.URL}?month={month}", {
            "month": month, "issue_date": issue_date, "pay_date": pay_date,
            "location": locations if locations is not None else [self.loc.pk]})

    def test_sidebar_by_permission(self):
        self.client.force_login(self.viewer)
        html = self.page().content.decode()
        self.assertIn('<i class="fas fa-file-alt"></i> 計算書\n', html)
        self.assertNotIn(reverse("fees:destinations"), html)
        self.client.force_login(self.user)
        html = self.page().content.decode()
        self.assertIn(reverse("fees:destinations"), html)

    def test_view_only_is_blocked_from_issue_screens(self):
        load_sample(self.loc)
        st, = self.issue()
        self.client.force_login(self.viewer)
        for url in (reverse("fees:destinations"), reverse("fees:void", args=[st.pk]),
                    reverse("fees:issuer"), reverse("fees:destination_edit", args=[self.loc.pk]),
                    reverse("fees:issue_preview") + f"?month=2026-10&location={self.loc.pk}"):
            self.assertRedirects(self.client.get(url), reverse("dashboard"), fetch_redirect_response=False)
        self.assertEqual(self.client.post(reverse("fees:void", args=[st.pk])).status_code, 302)
        st.refresh_from_db()
        self.assertFalse(st.is_void)
        html = self.client.get(reverse("fees:detail", args=[st.pk])).content.decode()
        self.assertNotIn(reverse("fees:void", args=[st.pk]), html)
        self.assertEqual(self.client.get(reverse("fees:pdf", args=[st.pk])).status_code, 200)

    def test_view_only_sees_no_operations(self):
        load_sample(self.loc)
        loc2 = SalesLocation.objects.create(no=15, name="△△工場", type="車", price_type="A", service_name="なし")
        FeeDestination.objects.create(location=loc2, enabled=True, addressee="")
        loc3 = SalesLocation.objects.create(no=16, name="□□センター", type="車", price_type="A", service_name="なし")
        d3 = FeeDestination.objects.create(location=loc3, enabled=True, addressee="C株式会社")
        FeeRate.objects.create(destination=d3, start_month=OCT, rate=Decimal("8"))
        make_report(loc3, 1, (0, 5, 0, 0, 0))
        st, = self.issue()
        self.client.force_login(self.viewer)
        html = self.page().content.decode()
        self.assertIn(st.number, html)
        self.assertIn(reverse("fees:pdf", args=[st.pk]), html)
        for absent in ('name="location"', 'name="issue_date"', "fee-issue-btn", reverse("fees:void", args=[st.pk]),
                       "宛先を編集", reverse("fees:issue_preview"), 'id="fee-all"'):
            self.assertNotIn(absent, html)
        # 閲覧のみがPOSTしても発行しない
        self.post_issue(locations=[loc3.pk])
        self.assertEqual(FeeStatement.objects.count(), 1)

    def test_no_permission_is_blocked(self):
        self.client.force_login(self.nobody)
        self.assertRedirects(self.client.get(reverse("fees:list")), reverse("dashboard"),
                             fetch_redirect_response=False)
        html = self.client.get(reverse("dashboard")).content.decode()
        self.assertNotIn(reverse("fees:list"), html)

    def test_old_issue_url_redirects(self):
        self.client.force_login(self.viewer)
        self.assertRedirects(self.client.get(reverse("fees:issue") + "?month=2026-09"),
                             f"{self.URL}?month=2026-09", fetch_redirect_response=False)

    def test_default_month_is_previous_month(self):
        self.client.force_login(self.viewer)
        today = timezone.localdate()
        prev = (today.replace(day=1) - datetime.timedelta(days=1))
        self.assertContains(self.client.get(self.URL), f'value="{prev:%Y-%m}"')

    def test_issue_on_same_screen_then_shown_as_issued(self):
        load_sample(self.loc)
        self.client.force_login(self.user)
        page = self.page()
        self.assertContains(page, "2026年10月分")
        self.assertContains(page, "¥76,851")
        self.assertContains(page, "未発行")
        today = timezone.localdate()
        self.assertContains(page, f'value="{default_pay_date(today):%Y-%m-%d}"')
        resp = self.post_issue()
        self.assertRedirects(resp, f"{self.URL}?month=2026-10", fetch_redirect_response=False)
        page = self.client.get(resp.url)
        self.assertContains(page, "2026年10月分 1件を発行")
        self.assertContains(page, "FEE-202610-0001")
        self.assertContains(page, '<span class="fee-badge fee-badge-issued">発行済み</span>')
        self.assertNotContains(page, "fee-issue-btn")   # 発行できる行がない
        # 二度押し：同じ月×売り場は2件目を作らない
        resp = self.post_issue()
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "発行できなかった：○○ビル（発行済み）")
        self.assertEqual(FeeStatement.objects.count(), 1)

    def test_issued_row_shows_snapshot_amounts(self):
        load_sample(self.loc)
        self.issue()
        DailyReportEntry.objects.filter(report__date=datetime.date(2026, 10, 1), product_no=2).update(sales_quantity=52)
        self.client.force_login(self.user)
        page = self.page()
        self.assertContains(page, "¥754,550")
        self.assertContains(page, "差あり")

    def test_pay_date_before_issue_date(self):
        load_sample(self.loc)
        self.client.force_login(self.user)
        resp = self.post_issue(pay_date="2026-11-10")
        self.assertContains(resp, "支払日が発行日より前")
        self.assertContains(resp, 'value="2026-11-10"')
        self.assertContains(resp, f'value="{self.loc.pk}"\n                           aria-label="○○ビル を選択" checked')
        self.assertEqual(FeeStatement.objects.count(), 0)

    def test_issue_with_default_dates_passes(self):
        load_sample(self.loc)
        self.client.force_login(self.user)
        for issue_date in (datetime.date(2026, 11, 12), datetime.date(2026, 11, 5)):
            FeeStatement.objects.all().delete()
            resp = self.post_issue(issue_date=f"{issue_date:%Y-%m-%d}",
                                   pay_date=f"{default_pay_date(issue_date):%Y-%m-%d}")
            self.assertEqual(resp.status_code, 302)
            self.assertEqual(FeeStatement.objects.get().pay_date, default_pay_date(issue_date))

    def test_future_month_post_is_rejected(self):
        self.client.force_login(self.user)
        resp = self.post_issue(month="2099-01")
        self.assertContains(resp, "対象月が未来")
        self.assertEqual(FeeStatement.objects.count(), 0)

    def test_future_month(self):
        self.client.force_login(self.user)
        self.assertContains(self.page("2099-01"), "対象月が未来")

    def test_void_returns_to_same_month_and_can_reissue(self):
        load_sample(self.loc)
        st, = self.issue()
        self.client.force_login(self.user)
        page = self.page().content.decode()
        void_url = reverse("fees:void", args=[st.pk]) + "?from=month"
        self.assertIn(void_url, page)
        self.assertContains(self.client.get(void_url), f'href="{self.URL}?month=2026-10" class="btn btn-outline-secondary">戻る')
        resp = self.client.post(reverse("fees:void", args=[st.pk]), {"void_reason": "日計表の修正"})
        self.assertRedirects(resp, f"{self.URL}?month=2026-10", fetch_redirect_response=False)
        page = self.client.get(resp.url)
        self.assertContains(page, "FEE-202610-0001 を取消")
        self.assertContains(page, "取消した計算書（1件）")
        self.assertContains(page, 'aria-label="○○ビル を選択"')   # 未発行に戻ってそのまま選べる
        self.assertRedirects(self.post_issue(), f"{self.URL}?month=2026-10", fetch_redirect_response=False)
        again = FeeStatement.objects.get(status=FeeStatement.STATUS_ISSUED)
        self.assertEqual((again.number, again.replaces_id), ("FEE-202610-0002", st.pk))
        # 詳細の［再発行］も同じ月の計算書画面へ
        services.void_statement(again, self.user)
        html = self.client.get(reverse("fees:detail", args=[again.pk])).content.decode()
        self.assertIn(f'href="{self.URL}?month=2026-10" class="btn btn-primary">再発行', html)

    def test_month_pdf_download_excludes_void(self):
        load_sample(self.loc)
        loc2 = SalesLocation.objects.create(no=15, name="△△工場", type="車", price_type="A", service_name="なし")
        d2 = FeeDestination.objects.create(location=loc2, enabled=True, addressee="B株式会社")
        FeeRate.objects.create(destination=d2, start_month=OCT, rate=Decimal("8"))
        make_report(loc2, 1, (0, 5, 0, 0, 0))
        a, b = self.issue(locations=[self.loc.pk, loc2.pk])
        services.void_statement(b, self.user)
        self.client.force_login(self.viewer)
        self.assertContains(self.page(), reverse("fees:pdf_month") + "?month=2026-10")
        resp = self.client.get(reverse("fees:pdf_month") + "?month=2026-10")
        self.assertEqual(resp["Content-Type"], "application/pdf")
        self.assertIn("filename*=UTF-8''%E8%A8%88%E7%AE%97%E6%9B%B8_202610.pdf", resp["Content-Disposition"])
        self.assertEqual(resp.content[:4], b"%PDF")
        # 発行済みがない月は戻す
        resp = self.client.get(reverse("fees:pdf_month") + "?month=2026-09")
        self.assertRedirects(resp, f"{self.URL}?month=2026-09", fetch_redirect_response=False)

    def test_disabled_destination_with_issued_statement_stays_in_month(self):
        load_sample(self.loc)
        st, = self.issue()
        self.dest.enabled = False
        self.dest.save()
        self.client.force_login(self.user)
        self.assertContains(self.page(), st.number)


class DestinationViewTests(Base):
    def setUp(self):
        super().setUp()
        self.client.force_login(self.user)
        self.loc2 = SalesLocation.objects.create(no=3, name="新川", type="車", price_type="A", service_name="なし")

    def test_add_modal_lists_only_candidates_in_no_order(self):
        html = self.client.get(reverse("fees:destinations")).content.decode()
        self.assertIn("3　新川", html)
        self.assertNotIn("12　○○ビル</option>", html)

    def test_add_flow_opens_with_enabled_on(self):
        resp = self.client.get(reverse("fees:destination_add") + f"?location={self.loc2.pk}")
        url = reverse("fees:destination_edit", args=[self.loc2.pk]) + "?add=1"
        self.assertRedirects(resp, url, fetch_redirect_response=False)
        self.assertContains(self.client.get(url), 'name="enabled" checked')

    def test_save_requires_addressee_when_enabled(self):
        url = reverse("fees:destination_edit", args=[self.loc2.pk])
        resp = self.client.post(url, {"action": "save", "enabled": "on", "addressee": ""})
        self.assertContains(resp, "宛名を入力")
        resp = self.client.post(url, {"action": "save", "enabled": "on", "addressee": "C株式会社 御中",
                                      "include_service": "on"})
        self.assertRedirects(resp, reverse("fees:destinations"), fetch_redirect_response=False)
        dest = FeeDestination.objects.get(location=self.loc2)
        self.assertEqual(dest.addressee, "C株式会社")   # 御中は外す
        self.assertTrue(dest.enabled and dest.include_service)

    def test_disable_keeps_values_and_returns_to_candidates(self):
        url = reverse("fees:destination_edit", args=[self.loc.pk])
        self.client.post(url, {"action": "save", "addressee": "A株式会社"})
        self.dest.refresh_from_db()
        self.assertFalse(self.dest.enabled)
        self.assertEqual(self.dest.addressee, "A株式会社")
        self.assertEqual(self.dest.rates.count(), 1)
        self.assertIn("12　○○ビル", self.client.get(reverse("fees:destinations")).content.decode())

    def test_add_rate_also_saves_destination(self):
        # 率の［追加］だけ押しても、宛名と「出す」は保存されて一覧に出る（本番で起きた取りこぼし）
        url = reverse("fees:destination_edit", args=[self.loc2.pk]) + "?add=1"
        self.client.post(url, {"action": "add_rate", "add": "1", "enabled": "on", "addressee": "C株式会社",
                               "include_service": "on", "start_month": "2026-06", "rate": "7"})
        dest = FeeDestination.objects.get(location=self.loc2)
        self.assertTrue(dest.enabled)
        self.assertEqual(dest.addressee, "C株式会社")
        self.assertEqual(dest.rates.get().rate, Decimal("7"))
        self.assertContains(self.client.get(reverse("fees:destinations")), "C株式会社 御中")

    def test_save_also_adds_filled_rate(self):
        url = reverse("fees:destination_edit", args=[self.loc2.pk])
        resp = self.client.post(url, {"action": "save", "enabled": "on", "addressee": "C株式会社",
                                      "start_month": "2026-06", "rate": "7"})
        self.assertRedirects(resp, reverse("fees:destinations"), fetch_redirect_response=False)
        self.assertEqual(FeeDestination.objects.get(location=self.loc2).rates.get().rate, Decimal("7"))
        # 率が空なら率は足さずに宛先だけ保存（開始月は既定値が入っている）
        self.client.post(url, {"action": "save", "enabled": "on", "addressee": "C株式会社",
                               "start_month": "2026-12", "rate": ""})
        self.assertEqual(FeeDestination.objects.get(location=self.loc2).rates.count(), 1)

    def test_note_saved_shown_in_pdf_and_snapshotted(self):
        url = reverse("fees:destination_edit", args=[self.loc.pk])
        self.client.post(url, {"action": "save", "enabled": "on", "addressee": "A株式会社",
                               "note": "経理部 御中\r\n振込手数料は当社負担"})
        self.dest.refresh_from_db()
        self.assertEqual(self.dest.note, "経理部 御中\n振込手数料は当社負担")
        load_sample(self.loc)
        st = self.issue()[0]
        self.assertEqual(st.note, "経理部 御中\n振込手数料は当社負担")
        html = pdf.render_html(st)
        self.assertIn("経理部 御中<br>振込手数料は当社負担", html)
        self.assertEqual(pdf.page_count(st), 1)
        # 発行後に備考を変えても、発行済みの計算書は発行時の備考のまま
        self.dest.note = "変更後"
        self.dest.save()
        st.refresh_from_db()
        self.assertNotIn("変更後", pdf.render_html(st))

    def test_note_limits_and_escape(self):
        url = reverse("fees:destination_edit", args=[self.loc.pk])
        resp = self.client.post(url, {"action": "save", "enabled": "on", "addressee": "A株式会社",
                                      "note": "1\n2\n3\n4"})
        self.assertContains(resp, "備考は3行まで")
        self.client.post(url, {"action": "save", "enabled": "on", "addressee": "A株式会社",
                               "note": "<script>x</script>"})
        load_sample(self.loc)
        html = pdf.render_html(self.issue()[0])
        self.assertNotIn("<script>x</script>", html)
        self.assertIn("&lt;script&gt;", html)

    def test_save_with_bad_rate_saves_nothing(self):
        url = reverse("fees:destination_edit", args=[self.loc2.pk])
        resp = self.client.post(url, {"action": "save", "enabled": "on", "addressee": "C株式会社",
                                      "start_month": "2026-06", "rate": "101"})
        self.assertContains(resp, "率は0〜100")
        self.assertContains(resp, 'value="C株式会社"')
        self.assertFalse(FeeDestination.objects.filter(location=self.loc2).exists())

    def test_rate_rules(self):
        url = reverse("fees:destination_edit", args=[self.loc.pk])
        self.assertContains(self.client.post(url, {"action": "add_rate", "start_month": "2026-04", "rate": "8"}),
                            "同じ開始月あり")
        self.assertContains(self.client.post(url, {"action": "add_rate", "start_month": "2026-12", "rate": "101"}),
                            "率は0〜100")
        self.assertContains(self.client.post(url, {"action": "add_rate", "start_month": "2026-12", "rate": "8.25"}),
                            "小数は1桁まで")
        for bad in ("NaN", "Infinity", "-Infinity", "abc"):
            self.assertContains(self.client.post(url, {"action": "add_rate", "start_month": "2027-01", "rate": bad}),
                                "率は0〜100")
        self.client.post(url, {"action": "add_rate", "start_month": "2099-12", "rate": "8.5"})
        future = FeeRate.objects.get(start_month=datetime.date(2099, 12, 1))
        self.assertEqual(future.rate, Decimal("8.5"))
        current = FeeRate.objects.get(start_month=datetime.date(2026, 4, 1))
        self.client.post(url, {"action": "cancel_rate", "rate_id": current.pk})
        self.assertTrue(FeeRate.objects.filter(pk=current.pk).exists())   # 適用中は取消できない
        self.client.post(url, {"action": "cancel_rate", "rate_id": future.pk})
        self.assertFalse(FeeRate.objects.filter(pk=future.pk).exists())

    def test_issuer_update(self):
        resp = self.client.post(reverse("fees:issuer"), {"company_name": "", "postal_code": "", "address": "",
                                                         "tel": "", "fax": "", "registration_number": ""})
        self.assertContains(resp, "会社名を入力")
        self.client.post(reverse("fees:issuer"), {"company_name": "新社名", "postal_code": "〒1", "address": "住所",
                                                  "tel": "1", "fax": "2", "registration_number": "T9",
                                                  "show_registration_on_fee": "on"})
        s = IssuerSettings.load()
        self.assertEqual((s.company_name, s.registration_number, s.show_registration_on_fee), ("新社名", "T9", True))


class InvoiceUnchangedTests(TestCase):
    """発行元を受注設定から切り出しても、請求書・納品書・領収書のPDFに出る値は同じ。"""

    def test_order_settings_reads_issuer(self):
        from orders.models import OrderSettings
        s = IssuerSettings.load()
        s.company_name, s.tel, s.registration_number = "発行元社", "03-0000-0000", "T1111111111111"
        s.seal_image_data = "data:image/png;base64,QUJD"
        s.save()
        o = OrderSettings.load()
        self.assertEqual((o.company_name, o.tel, o.invoice_number, o.seal_image_data),
                         ("発行元社", "03-0000-0000", "T1111111111111", "data:image/png;base64,QUJD"))

    def test_invoice_html_uses_issuer(self):
        from orders import services as order_services
        from orders.models import Customer, Invoice, Order, OrderItem, PaymentMethod
        from orders.pdf import build_invoice_context
        from django.template.loader import render_to_string
        s = IssuerSettings.load()
        s.company_name, s.postal_code, s.address = "発行元社", "〒100-0001", "東京都千代田区1-2-3"
        s.tel, s.fax, s.registration_number = "03-1111-2222", "03-3333-4444", "T1234567890123"
        s.seal_image_data = "data:image/png;base64,QUJD"
        s.show_registration_on_fee = False   # 計算書の設定は請求書に影響しない
        s.save()
        pm = PaymentMethod.objects.create(name="請求書")
        c = Customer.objects.create(customer_type="B2B", company_name="客社", price_type="A", bento_type="REGULAR",
                                    payment_method=pm, invoice_timing="MONTH_END", invoice_delivery="EMAIL")
        d = datetime.date(2026, 10, 5)
        o = Order.objects.create(customer=c, order_date=d, delivery_date=d, billing_pattern="MONTH_END")
        OrderItem.objects.create(order=o, product_name="弁当", quantity_regular=3, unit_price=Decimal(700),
                                 subtotal=Decimal(2100))
        inv = order_services.issue_invoice(c, [o.pk], pattern=Invoice.PATTERN_MONTH_END)
        self.assertEqual(inv.registration_number, "T1234567890123")
        html = render_to_string("orders/invoice.html", build_invoice_context(inv))
        for text in ('<div class="company-name">発行元社</div>', "〒100-0001　東京都千代田区1-2-3",
                     "TEL 03-1111-2222　FAX 03-3333-4444", "登録番号：T1234567890123",
                     '<img class="seal" src="data:image/png;base64,QUJD" alt="社印">'):
            self.assertIn(text, html)


class IssuerMigrationTests(TransactionTestCase):
    """既存の受注設定の会社情報・社印が発行元へ写る（sales.0103）。"""

    def test_copy_from_order_settings(self):
        from django.db import connection
        from django.db.migrations.executor import MigrationExecutor
        before = [("sales", "0102_item_quantity_edit"), ("orders", "0029_customer_price_type_free")]
        after = [("sales", "0103_issuer_settings_fee_permissions"), ("orders", "0030_order_settings_issuer_legacy")]
        executor = MigrationExecutor(connection)
        executor.migrate(before)
        old = executor.loader.project_state(before).apps
        old.get_model("orders", "OrderSettings").objects.create(
            pk=1, company_name="旧社名", postal_code="〒1", address="旧住所", tel="1", fax="2",
            invoice_number="T7777777777777", seal_image_data="data:image/png;base64,WFla")
        executor = MigrationExecutor(connection)
        executor.loader.build_graph()
        executor.migrate(after)
        executor = MigrationExecutor(connection)
        executor.loader.build_graph()
        executor.migrate(executor.loader.graph.leaf_nodes())
        s = IssuerSettings.objects.get(pk=1)
        self.assertEqual((s.company_name, s.address, s.registration_number, s.seal_image_data),
                         ("旧社名", "旧住所", "T7777777777777", "data:image/png;base64,WFla"))
        self.assertFalse(s.show_registration_on_fee)
