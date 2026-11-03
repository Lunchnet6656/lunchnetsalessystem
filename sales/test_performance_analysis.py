"""実績分析の集計・前月比/前週比・4象限・画面の権限のテスト。"""
from datetime import date
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from orders.models import Customer, Order, OrderItem
from sales.models import AnalysisBasisLog, AnalysisBasisSetting, DailyReport, DailyReportEntry, UserMenuPermission
from sales.performance_analysis import analyze, default_period


def report(day, location, rows, revenue=0, location_no=0):
    """rows: [(no, name, 持参, 販売, 残)]"""
    r = DailyReport.objects.create(date=day, location=location, location_no=location_no, total_revenue=revenue)
    for no, name, q, s, rem in rows:
        DailyReportEntry.objects.create(
            report=r, product_no=no, product=name, quantity=q, sales_quantity=s,
            remaining_number=rem, total_sales=0,
        )


def order(day, qty):
    customer = Customer.objects.create(
        customer_type="B2B", company_name=f"会社{day}{qty}", price_type="A", bento_type="REGULAR",
    )
    o = Order.objects.create(customer=customer, order_date=day, delivery_date=day)
    OrderItem.objects.create(order=o, product_name="唐揚げ", quantity_regular=qty, unit_price=Decimal(700), subtotal=Decimal(700 * qty))


class DefaultPeriodTests(TestCase):
    def test_this_month_until_yesterday(self):
        self.assertEqual(default_period(date(2026, 11, 15)), (date(2026, 11, 1), date(2026, 11, 14)))

    def test_first_day_shows_last_month(self):
        self.assertEqual(default_period(date(2026, 11, 1)), (date(2026, 10, 1), date(2026, 10, 31)))


class TotalsTests(TestCase):
    def setUp(self):
        report(date(2026, 10, 1), "虎ノ門", [(1, "唐揚げ", 20, 18, 2), (11, "大盛りごはん", 5, 5, 0), (0, "", 0, 0, 0)], revenue=13000, location_no=21)
        report(date(2026, 10, 2), "虎ノ門", [(1, "唐揚げ", 20, 20, 0)], revenue=14000, location_no=22)
        report(date(2026, 10, 2), "高輪", [(2, "鮭", 10, 6, 4)], revenue=4000)
        order(date(2026, 10, 2), 30)

    def test_summary(self):
        s = analyze(date(2026, 10, 1), date(2026, 10, 31))["summary"]
        # 大盛りごはん(11)と空行(0)は数えない。受注30食は持参・販売に足す（残0）
        self.assertEqual((s["quantity"], s["sales"], s["remaining"]), (80, 74, 6))
        self.assertAlmostEqual(s["waste_rate"], 7.5)
        # 評価の基準は売り場だけの廃棄率
        self.assertAlmostEqual(s["store_waste_rate"], 12.0)
        # (売り場44＋受注30) ÷ (虎ノ門2日＋高輪1日)
        self.assertAlmostEqual(s["avg_sales"], 74 / 3)
        # 日計表31,000＋受注30食×700
        self.assertEqual(s["revenue"], 31000 + 21000)
        self.assertEqual(s["orders"], 30)
        # 稼働日数は売り場の中でいちばん多い日数（虎ノ門2日・高輪1日）
        self.assertEqual(s["op_days"], 2)
        self.assertEqual(s["deltas"]["op_days"][0]["text"], "—")

    def test_store_renumbered_is_one_row(self):
        locs = {l["name"]: l for l in analyze(date(2026, 10, 1), date(2026, 10, 31))["locations"] if not l.get("is_orders")}
        self.assertEqual(set(locs), {"虎ノ門", "高輪"})
        self.assertEqual(locs["虎ノ門"]["days"], 2)
        self.assertEqual(locs["虎ノ門"]["revenue"], 27000)

    def test_orders_lower_waste_rate_but_not_store_basis(self):
        order(date(2026, 10, 3), 100)
        r = analyze(date(2026, 10, 1), date(2026, 10, 31))
        self.assertAlmostEqual(r["summary"]["waste_rate"], 6 / 180 * 100)
        self.assertAlmostEqual(r["summary"]["store_waste_rate"], 12.0)
        # 平均販売数も受注を含む（分母は売り場の稼働日数）
        self.assertAlmostEqual(r["summary"]["avg_sales"], (44 + 30 + 100) / 3)
        # 売り場別の売上・1日平均は日計表だけ
        locs = {l["name"]: l for l in r["locations"]}
        self.assertEqual(locs["虎ノ門"]["revenue"], 27000)
        self.assertAlmostEqual(locs["虎ノ門"]["avg_sales"], 19.0)


class OrdersRowTests(TestCase):
    def test_orders_row_in_locations(self):
        report(date(2026, 10, 1), "虎ノ門", [(1, "唐揚げ", 20, 18, 2)], revenue=13000)
        order(date(2026, 10, 1), 30)
        order(date(2026, 10, 2), 10)
        order(date(2026, 9, 1), 20)
        r = analyze(date(2026, 10, 1), date(2026, 10, 31))
        row = r["locations"][-1]
        self.assertEqual(row["name"], "ルート&別注仕出し(受注販売)")
        self.assertEqual((row["quantity"], row["sales"], row["remaining"], row["days"]), (40, 40, 0, 2))
        self.assertEqual(row["waste_rate"], 0)
        self.assertEqual(row["avg_sales"], 20)
        self.assertEqual(row["revenue"], 40 * 700)
        self.assertIsNone(row["label"])
        self.assertEqual(row["eval_rank"], -2)
        self.assertEqual(row["deltas"]["sales"][0]["text"], "▲ 100%")
        # 対象外の件数には入らない（稼働1日の虎ノ門だけ）
        self.assertEqual([c["count"] for c in r["location_counts"] if c["label"] is None], [1])

    def test_no_orders_no_row(self):
        report(date(2026, 10, 1), "虎ノ門", [(1, "唐揚げ", 20, 18, 2)])
        r = analyze(date(2026, 10, 1), date(2026, 10, 31))
        self.assertFalse(any(l.get("is_orders") for l in r["locations"]))


class ComparisonTests(TestCase):
    def test_previous_month_and_week(self):
        # 今回：10/10 持参20 販売16 残4（廃棄20%）
        report(date(2026, 10, 10), "虎ノ門", [(1, "唐揚げ", 20, 16, 4)])
        # 前月：9/10 持参20 販売20 残0
        report(date(2026, 9, 10), "虎ノ門", [(1, "唐揚げ", 20, 20, 0)])
        # 前週：10/3 持参20 販売8 残12（廃棄60%）
        report(date(2026, 10, 3), "虎ノ門", [(1, "唐揚げ", 20, 8, 12)])

        s = analyze(date(2026, 10, 10), date(2026, 10, 10))["summary"]
        ch = s["changes"]
        self.assertEqual(ch["avg_sales"]["month"], {"value": 20.0, "unit": "%", "arrow": "▼", "tone": "bad"})
        self.assertEqual(ch["avg_sales"]["week"], {"value": 100.0, "unit": "%", "arrow": "▲", "tone": "good"})
        # 廃棄率はpt。上がると悪い
        self.assertEqual(ch["waste_rate"]["month"], {"value": 20.0, "unit": "pt", "arrow": "▲", "tone": "bad"})
        self.assertEqual(ch["waste_rate"]["week"], {"value": 40.0, "unit": "pt", "arrow": "▼", "tone": "good"})
        # 持参数は良し悪しを付けない／前週比は廃棄率・平均販売数だけ
        self.assertEqual(ch["quantity"]["month"]["tone"], "neutral")
        self.assertNotIn("week", ch["quantity"])

    def test_month_end_is_rounded(self):
        r = analyze(date(2026, 10, 1), date(2026, 10, 31))
        self.assertEqual(r["prev_month"], (date(2026, 9, 1), date(2026, 9, 30)))
        self.assertEqual(r["prev_week"], (date(2026, 9, 24), date(2026, 10, 24)))

    def test_no_previous_data(self):
        report(date(2026, 10, 10), "虎ノ門", [(1, "唐揚げ", 20, 16, 4)])
        ch = analyze(date(2026, 10, 10), date(2026, 10, 10))["summary"]["changes"]
        self.assertIsNone(ch["waste_rate"]["month"])
        self.assertIsNone(ch["avg_sales"]["week"])

    def test_no_change_is_neutral(self):
        report(date(2026, 10, 10), "虎ノ門", [(1, "唐揚げ", 20, 16, 4)])
        report(date(2026, 9, 10), "虎ノ門", [(1, "唐揚げ", 20, 16, 4)])
        ch = analyze(date(2026, 10, 10), date(2026, 10, 10))["summary"]["changes"]
        self.assertEqual(ch["waste_rate"]["month"], {"value": 0.0, "unit": "pt", "arrow": "±", "tone": "neutral"})


class QuadrantTests(TestCase):
    def setUp(self):
        # 3日ずつ。1日あたり 優良=30売/残0、持参を減らす=30売/残10、テコ入れ=5売/残0、撤退検討=5売/残10
        plans = {
            "優良店": (30, 30, 0), "減らす店": (40, 30, 10),
            "テコ入れ店": (5, 5, 0), "撤退店": (15, 5, 10),
        }
        for day in (1, 2, 3):
            for name, (q, s, r) in plans.items():
                report(date(2026, 10, day), name, [(1, "唐揚げ", q, s, r)])
        # 稼働2日（足切り未満）は評価しない
        for day in (1, 2):
            report(date(2026, 10, day), "たまに店", [(1, "唐揚げ", 10, 10, 0)])

    def test_location_labels(self):
        r = analyze(date(2026, 10, 1), date(2026, 10, 31))
        labels = {l["name"]: l["label"] for l in r["locations"]}
        self.assertEqual(labels, {
            "優良店": "優良", "減らす店": "持参を減らす", "テコ入れ店": "テコ入れ",
            "撤退店": "撤退検討", "たまに店": None,
        })
        # 中央値は評価対象4店の1日平均（30,30,5,5）
        self.assertEqual(r["location_basis"]["avg_sales"], 17.5)
        self.assertEqual(r["locations"][-1]["name"], "たまに店")
        # 区切り行：中央値未満の先頭（テコ入れ/撤退の高い方）と対象外の先頭
        breaks = [(l["name"], l["median_break"], l["na_break"]) for l in r["locations"] if l["median_break"] or l["na_break"]]
        self.assertEqual([b[1:] for b in breaks], [(True, False), (False, True)])

    def test_menu_labels_and_order(self):
        for day in (1, 2, 3):
            report(date(2026, 10, day), "別店", [
                (2, "鮭", 10, 10, 0), (3, "ハンバーグ", 60, 40, 20), (4, "麻婆丼", 10, 2, 8),
            ])
        r = analyze(date(2026, 10, 1), date(2026, 10, 31))
        menus = {m["name"]: m for m in r["menus"]}
        self.assertEqual(menus["鮭"]["label"], "増やせる")
        self.assertEqual(menus["ハンバーグ"]["label"], "作りすぎ")
        self.assertEqual(menus["麻婆丼"]["label"], "不人気")
        # 既定は販売数の多い順。評価の並べ替え用の順位は人気=0…不人気=3
        self.assertEqual(r["menus"][0]["name"], "唐揚げ")
        self.assertEqual(menus["麻婆丼"]["eval_rank"], 3)
        self.assertEqual(r["menu_counts"]["不人気"], 1)


class TrendTests(TestCase):
    def test_twelve_months_with_gaps(self):
        report(date(2026, 10, 5), "虎ノ門", [(1, "唐揚げ", 20, 15, 5)], revenue=10000)
        report(date(2025, 11, 5), "虎ノ門", [(1, "唐揚げ", 10, 10, 0)], revenue=7000)
        report(date(2025, 10, 5), "虎ノ門", [(1, "唐揚げ", 99, 99, 0)])  # 13か月前は入らない
        trend = analyze(date(2026, 10, 1), date(2026, 10, 31))["trend"]
        self.assertEqual(len(trend), 12)
        self.assertEqual(trend[0]["month"], date(2025, 11, 1))
        self.assertEqual(trend[0]["sales"], 10)
        self.assertTrue(trend[5]["empty"])
        self.assertAlmostEqual(trend[-1]["waste_rate"], 25.0)
        self.assertEqual(trend[-1]["revenue"], 10000)
        order(date(2026, 10, 6), 20)
        last = analyze(date(2026, 10, 1), date(2026, 10, 31))["trend"][-1]
        self.assertEqual((last["quantity"], last["sales"], last["remaining"]), (40, 35, 5))
        self.assertEqual(last["revenue"], 10000 + 20 * 700)

    def test_month_over_month(self):
        report(date(2026, 9, 5), "虎ノ門", [(1, "唐揚げ", 20, 20, 0)], revenue=10000)
        report(date(2026, 10, 5), "虎ノ門", [(1, "唐揚げ", 20, 15, 5)], revenue=12000)
        report(date(2025, 10, 5), "虎ノ門", [(1, "唐揚げ", 10, 5, 5)])  # 表示の先頭（2025/11）の前月
        report(date(2025, 11, 5), "虎ノ門", [(1, "唐揚げ", 10, 10, 0)])
        trend = analyze(date(2026, 10, 1), date(2026, 10, 31))["trend"]
        last = trend[-1]["deltas"]
        self.assertEqual(last["sales"][0]["text"], "▼ 25.0%")
        self.assertEqual(last["sales"][0]["tone"], "bad")
        self.assertEqual(last["waste_rate"][0]["text"], "▲ 25.0pt")
        self.assertEqual(last["revenue"][0]["tone"], "good")
        # 先頭の月も、表に出ない前月と比べる
        self.assertEqual(trend[0]["deltas"]["sales"][0]["text"], "▲ 100%")
        # 前月（8月）にデータがない9月は「—」
        self.assertEqual(trend[-2]["deltas"]["sales"][0]["text"], "—")


class ViewTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user("boss", password="pw-boss-123456")
        self.client.force_login(self.user)

    def _perm(self, allowed):
        perm, _ = UserMenuPermission.objects.get_or_create(user=self.user)
        perm.can_view_dashboard = allowed
        perm.save()

    def test_without_permission_redirects(self):
        self._perm(False)
        res = self.client.get(reverse("performance_analysis"))
        self.assertEqual(res.status_code, 302)

    def test_with_permission_shows(self):
        self._perm(True)
        report(date(2026, 10, 1), "虎ノ門", [(1, "唐揚げ", 20, 18, 2)])
        res = self.client.get(reverse("performance_analysis"), {"start": "2026-10-01", "end": "2026-10-31"})
        self.assertEqual(res.status_code, 200)
        self.assertContains(res, "虎ノ門")

    def test_bad_dates_show_error_without_numbers(self):
        self._perm(True)
        res = self.client.get(reverse("performance_analysis"), {"start": "2026-10-31", "end": "2026-10-01"})
        self.assertContains(res, "開始日が終了日より後です")
        self.assertIsNone(res.context["a"])
        self.assertEqual(res.context["start_value"], "2026-10-31")
        res = self.client.get(reverse("performance_analysis"), {"start": "abc", "end": ""})
        self.assertContains(res, "日付が不正です")


class BasisSettingTests(TestCase):
    def setUp(self):
        # 3日ずつ。1日平均 30,30,5,5／廃棄率 0%,25%,0%,66.7%
        plans = {"A店": (30, 30, 0), "B店": (40, 30, 10), "C店": (5, 5, 0), "D店": (15, 5, 10)}
        for day in (1, 2, 3):
            for name, (q, s, r) in plans.items():
                report(date(2026, 10, day), name, [(1, "唐揚げ", q, s, r)])

    def labels(self):
        return {l["name"]: l["label"] for l in analyze(date(2026, 10, 1), date(2026, 10, 31))["locations"]}

    def test_fixed_lines(self):
        AnalysisBasisSetting.objects.create(target="location", avg_mode="fixed", avg_fixed=Decimal("4"),
                                            waste_mode="fixed", waste_fixed=Decimal("30"))
        # 全店が1日平均4以上。廃棄率30%超はD店だけ
        self.assertEqual(self.labels(), {"A店": "優良", "B店": "優良", "C店": "優良", "D店": "持参を減らす"})

    def test_mean_and_total_waste(self):
        order(date(2026, 10, 2), 1000)
        AnalysisBasisSetting.objects.create(target="location", avg_mode="mean", waste_mode="total")
        r = analyze(date(2026, 10, 1), date(2026, 10, 31))
        self.assertEqual(r["location_basis"]["avg_sales"], 17.5)
        # 受注込みの全体廃棄率（60 / (270+1000)）が基準
        self.assertEqual(r["location_basis"]["waste_rate"], round(60 / 1270 * 100, 1))  # 表示と同じ小数1桁で判定

    def test_min_days(self):
        AnalysisBasisSetting.objects.create(target="location", min_days=4)
        self.assertEqual(set(self.labels().values()), {None})

    def test_menu_setting_is_separate(self):
        AnalysisBasisSetting.objects.create(target="menu", min_days=10)
        r = analyze(date(2026, 10, 1), date(2026, 10, 31))
        self.assertIsNotNone(r["locations"][0]["label"])
        self.assertIsNone(r["menus"][0]["label"])


class BasisSaveViewTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user("boss", password="pw-boss-123456")
        UserMenuPermission.objects.update_or_create(user=self.user, defaults={"can_view_dashboard": True})
        self.client.force_login(self.user)
        self.url = reverse("performance_analysis_basis_save", args=["location"])
        self.data = {"location-avg_mode": "fixed", "location-avg_fixed": "20", "location-waste_mode": "store",
                     "location-waste_fixed": "", "location-min_days": "5", "start": "2026-10-01", "end": "2026-10-31"}

    def test_non_admin_cannot_save(self):
        self.client.post(self.url, self.data)
        self.assertFalse(AnalysisBasisSetting.objects.exists())

    def test_admin_saves_and_logs(self):
        self.user.is_staff = True
        self.user.save()
        res = self.client.post(self.url, self.data)
        self.assertRedirects(res, reverse("performance_analysis") + "?start=2026-10-01&end=2026-10-31&saved=location#sec-locations",
                             fetch_redirect_response=False)
        s = AnalysisBasisSetting.objects.get(target="location")
        self.assertEqual((s.avg_mode, s.avg_fixed, s.min_days, s.updated_by), ("fixed", Decimal("20.0"), 5, self.user))
        log = AnalysisBasisLog.objects.get()
        self.assertIn("中央値", log.before)
        self.assertIn("固定 20.0食", log.after)

    def test_fixed_without_value_is_rejected(self):
        self.user.is_staff = True
        self.user.save()
        res = self.client.post(self.url, dict(self.data, **{"location-avg_fixed": ""}))
        self.assertFalse(AnalysisBasisSetting.objects.exists())
        self.assertIn("basis_error=location", res.url)

    def test_admin_sees_button_and_saved_mark(self):
        self.user.is_staff = True
        self.user.save()
        report(date(2026, 10, 1), "虎ノ門", [(1, "唐揚げ", 20, 18, 2)])
        res = self.client.get(reverse("performance_analysis"), {"start": "2026-10-01", "end": "2026-10-31", "saved": "location"})
        self.assertContains(res, 'id="pa-basis-location"')
        self.assertContains(res, 'id="pa-basis-menu"')
        self.assertContains(res, "保存済み", count=1)

    def test_viewer_has_no_modal(self):
        res = self.client.get(reverse("performance_analysis"), {"start": "2026-10-01", "end": "2026-10-31"})
        self.assertNotContains(res, "pa-basis-location")
        self.assertNotContains(res, "基準設定")

    def test_unknown_target(self):
        self.user.is_staff = True
        self.user.save()
        res = self.client.post(reverse("performance_analysis_basis_save", args=["other"]), self.data)
        self.assertEqual(res.status_code, 404)


class RuleModeTests(TestCase):
    def setUp(self):
        # 1日平均 30,30,5,5／廃棄率 0%,25%,0%,66.7%
        plans = {"A店": (30, 30, 0), "B店": (40, 30, 10), "C店": (5, 5, 0), "D店": (15, 5, 10)}
        for day in (1, 2, 3):
            for name, (q, s, r) in plans.items():
                report(date(2026, 10, day), name, [(1, "唐揚げ", q, s, r)])

    def labels(self):
        return {l["name"]: l["label"] for l in analyze(date(2026, 10, 1), date(2026, 10, 31))["locations"]}

    def test_rules_in_order_and_other(self):
        AnalysisBasisSetting.objects.create(target="location", rule_mode="rules", rules=[
            {"label": "優良", "avg_op": "gte", "avg_value": 25, "waste_op": "lte", "waste_value": 10},
            {"label": "持参を減らす", "avg_op": "gte", "avg_value": 20, "waste_op": "gt", "waste_value": 20},
            {"label": "撤退検討", "avg_op": "lt", "avg_value": 10, "waste_op": "gt", "waste_value": 50},
            {"label": "テコ入れ", "avg_op": "", "avg_value": None, "waste_op": "lte", "waste_value": 0},
        ])
        self.assertEqual(self.labels(), {"A店": "優良", "B店": "持参を減らす", "C店": "テコ入れ", "D店": "撤退検討"})

    def test_unmatched_is_other(self):
        AnalysisBasisSetting.objects.create(target="location", rule_mode="rules", rules=[
            {"label": "優良", "avg_op": "gte", "avg_value": 100, "waste_op": "", "waste_value": None},
            {"label": "持参を減らす", "avg_op": "gte", "avg_value": 100, "waste_op": "", "waste_value": None},
            {"label": "撤退検討", "avg_op": "gte", "avg_value": 100, "waste_op": "", "waste_value": None},
            {"label": "テコ入れ", "avg_op": "gte", "avg_value": 100, "waste_op": "", "waste_value": None},
        ])
        self.assertEqual(set(self.labels().values()), {"様子見"})

    def test_default_rules_keep_current_labels(self):
        from sales.performance_analysis import default_rules
        before = self.labels()
        basis = analyze(date(2026, 10, 1), date(2026, 10, 31))["location_basis"]
        AnalysisBasisSetting.objects.create(target="location", rule_mode="rules", rules=default_rules("location", basis))
        self.assertEqual(self.labels(), before)

    def post_rules(self, rules, target="location"):
        user = get_user_model().objects.get_or_create(username="boss", defaults={"is_staff": True})[0]
        UserMenuPermission.objects.update_or_create(user=user, defaults={"can_view_dashboard": True})
        self.client.force_login(user)
        data = {f"{target}-rule_mode": "rules", f"{target}-min_days": "3"}
        for i, (label, color, conds) in enumerate(rules):
            data.update({f"{target}-rule-{i}-label": label, f"{target}-rule-{i}-color": color})
            for j, (metric, op, value) in enumerate(conds):
                data.update({f"{target}-rule-{i}-cond-{j}-metric": metric, f"{target}-rule-{i}-cond-{j}-op": op,
                             f"{target}-rule-{i}-cond-{j}-value": value})
        return self.client.post(reverse("performance_analysis_basis_save", args=[target]), data)

    def test_save_rules_via_form(self):
        self.post_rules([("売れ筋", "primary", [("sales", "gte", "90"), ("waste_rate", "lte", "10")]),
                         ("要注意", "danger", [("remaining", "gt", "20")])])
        s = AnalysisBasisSetting.objects.get(target="location")
        self.assertEqual(s.rule_mode, "rules")
        self.assertEqual(s.rules, [
            {"label": "売れ筋", "color": "primary", "conds": [{"metric": "sales", "op": "gte", "value": 90},
                                                             {"metric": "waste_rate", "op": "lte", "value": 10.0}]},
            {"label": "要注意", "color": "danger", "conds": [{"metric": "remaining", "op": "gt", "value": 20}]},
        ])
        self.assertIn("売れ筋 販売数90食以上・廃棄率10.0%以下（青）", AnalysisBasisLog.objects.get().after)
        # 販売数 A店90・B店90・C店15・D店15、残数 0・30・0・30
        self.assertEqual(self.labels(), {"A店": "売れ筋", "B店": "要注意", "C店": "様子見", "D店": "要注意"})
        r = analyze(date(2026, 10, 1), date(2026, 10, 31))
        self.assertEqual([(c["label"], c["badge"], c["count"]) for c in r["location_counts"] if c["label"]],
                         [("売れ筋", "primary", 1), ("要注意", "danger", 2), ("様子見", "secondary", 1)])
        self.assertEqual({l["name"]: l["eval_rank"] for l in r["locations"]}, {"A店": 0, "B店": 1, "C店": 2, "D店": 1})

    def test_invalid_rules_not_saved(self):
        self.post_rules([("売れ筋", "primary", [("sales", "gte", "90")])])
        for rules in (
            [("売れ筋", "primary", [])],  # 条件なし
            [("", "primary", [("sales", "gte", "1")])],  # 名前なし
            [("とても長い評価の名前", "primary", [("sales", "gte", "1")])],  # 9文字
            [("A", "primary", [("sales", "gte", "1")]), ("A", "danger", [("sales", "gte", "1")])],  # 重複
            [("様子見", "primary", [("sales", "gte", "1")])],
            [("売れ筋", "pink", [("sales", "gte", "1")])],
            [("売れ筋", "primary", [("waste_rate", "gte", "101")])],
            [("売れ筋", "primary", [("sales", "eq", "1")])],
        ):
            self.post_rules(rules)
            self.assertEqual(AnalysisBasisSetting.objects.get(target="location").rules[0]["conds"][0]["value"], 90)
        # メニューに売上金額はない
        self.post_rules([("売れ筋", "primary", [("revenue", "gte", "1")])], target="menu")
        self.assertFalse(AnalysisBasisSetting.objects.filter(target="menu").exists())

    def test_between_inclusive(self):
        user = get_user_model().objects.get_or_create(username="boss", defaults={"is_staff": True})[0]
        UserMenuPermission.objects.update_or_create(user=user, defaults={"can_view_dashboard": True})
        self.client.force_login(user)
        url = reverse("performance_analysis_basis_save", args=["location"])
        # 1日平均 A30・B30・C5・D5。5〜30 の両端を含む
        data = {"location-rule_mode": "rules", "location-min_days": "3",
                "location-rule-0-label": "中くらい", "location-rule-0-color": "info",
                "location-rule-0-cond-0-metric": "avg_sales", "location-rule-0-cond-0-op": "between",
                "location-rule-0-cond-0-value": "5", "location-rule-0-cond-0-value2": "30"}
        self.client.post(url, data)
        s = AnalysisBasisSetting.objects.get(target="location")
        self.assertEqual(s.rules[0]["conds"], [{"metric": "avg_sales", "op": "between", "value": 5.0, "value2": 30.0}])
        self.assertEqual(set(self.labels().values()), {"中くらい"})
        self.assertIn("中くらい 1日平均5.0〜30.0食", AnalysisBasisLog.objects.get().after)
        data["location-rule-0-cond-0-value"] = "5.1"
        self.client.post(url, data)
        self.assertEqual(self.labels(), {"A店": "中くらい", "B店": "中くらい", "C店": "様子見", "D店": "様子見"})
        # 上限なし・上限が下限より小さいときは保存しない
        for v2 in ("", "3"):
            self.client.post(url, dict(data, **{"location-rule-0-cond-0-value2": v2}))
            self.assertEqual(AnalysisBasisSetting.objects.get(target="location").rules[0]["conds"][0]["value2"], 30.0)

    def test_eight_rules_max(self):
        self.post_rules([(f"評価{i}", "primary", [("sales", "gte", str(100 - i))]) for i in range(9)])
        self.assertEqual(len(AnalysisBasisSetting.objects.get(target="location").rules), 8)

    def test_legacy_rules_shown_in_modal(self):
        AnalysisBasisSetting.objects.create(target="location", rule_mode="rules", rules=[
            {"label": "優良", "avg_op": "gte", "avg_value": 25, "waste_op": "", "waste_value": None}])
        self.post_rules([])  # ログイン用（評価なしは保存されない）
        res = self.client.get(reverse("performance_analysis"), {"start": "2026-10-01", "end": "2026-10-31"})
        self.assertContains(res, 'id="location-rule-0-label" value="優良"')

    def test_no_median_break_in_rule_mode(self):
        from sales.performance_analysis import default_rules
        basis = analyze(date(2026, 10, 1), date(2026, 10, 31))["location_basis"]
        AnalysisBasisSetting.objects.create(target="location", rule_mode="rules", rules=default_rules("location", basis))
        locs = analyze(date(2026, 10, 1), date(2026, 10, 31))["locations"]
        self.assertFalse(any(l["median_break"] for l in locs))


class WasteColorTests(TestCase):
    def setUp(self):
        # 廃棄率 0%, 25%, 66.7%
        plans = {"A店": (30, 30, 0), "B店": (40, 30, 10), "D店": (15, 5, 10)}
        for day in (1, 2, 3):
            for name, (q, s, r) in plans.items():
                report(date(2026, 10, day), name, [(1, "唐揚げ", q, s, r)])

    def classes(self):
        return {l["name"]: l["waste_class"] for l in analyze(date(2026, 10, 1), date(2026, 10, 31))["locations"]}

    def test_default_is_red_above_basis(self):
        # 売り場全体の廃棄率 20/255 = 7.8%
        self.assertEqual(self.classes(), {"A店": "", "B店": "text-danger fw-bold", "D店": "text-danger fw-bold"})

    def test_bands_pick_highest_match(self):
        AnalysisBasisSetting.objects.create(target="location", waste_color_mode="bands", waste_bands=[
            {"min": 10, "color": "danger", "bold": True},
            {"min": 30, "color": "success", "bold": True},
            {"min": 20, "color": "primary", "bold": False},
        ])
        self.assertEqual(self.classes(), {"A店": "", "B店": "text-primary", "D店": "text-success fw-bold"})

    def test_menu_has_own_colors(self):
        AnalysisBasisSetting.objects.create(target="location", waste_color_mode="bands",
                                            waste_bands=[{"min": 0, "color": "secondary", "bold": False}])
        r = analyze(date(2026, 10, 1), date(2026, 10, 31))
        self.assertEqual(r["menus"][0]["waste_class"], "text-danger fw-bold")

    def test_save_bands_via_form(self):
        user = get_user_model().objects.create_user("boss", password="pw-boss-123456", is_staff=True)
        UserMenuPermission.objects.update_or_create(user=user, defaults={"can_view_dashboard": True})
        self.client.force_login(user)
        url = reverse("performance_analysis_basis_save", args=["menu"])
        data = {"menu-avg_mode": "median", "menu-waste_mode": "store", "menu-min_days": "3",
                "menu-waste_color_mode": "bands",
                "menu-band-0-min": "10", "menu-band-0-color": "danger", "menu-band-0-bold": "1",
                "menu-band-1-min": "30", "menu-band-1-color": "success",
                "menu-band-2-min": "", "menu-band-2-color": "danger"}
        self.client.post(url, data)
        s = AnalysisBasisSetting.objects.get(target="menu")
        self.assertEqual(s.waste_bands, [{"min": 30.0, "color": "success", "bold": False},
                                         {"min": 10.0, "color": "danger", "bold": True}])
        self.assertIn("30.0%以上緑", AnalysisBasisLog.objects.get().after)
        # 段が1つもないと保存しない
        self.client.post(url, dict(data, **{"menu-band-0-min": "", "menu-band-1-min": ""}))
        self.assertEqual(len(AnalysisBasisSetting.objects.get(target="menu").waste_bands), 2)

    def test_orange_is_real_orange(self):
        AnalysisBasisSetting.objects.create(target="location", waste_color_mode="bands",
                                            waste_bands=[{"min": 20, "color": "orange", "bold": False}])
        self.assertEqual(self.classes()["B店"], "text-orange")
