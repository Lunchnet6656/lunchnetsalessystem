"""日計表の「売場の様子」：判定・保存・編集・設定画面・食数予測への反映のテスト。"""
import datetime

from django.contrib.auth.models import User
from django.http import QueryDict
from django.test import TestCase

from sales import daily_questions as dq
from sales import daily_questions_form as dqf
from sales.meal_forecast import estimated_demand
from sales.models import (DailyReport, DailyReportQuestionSetting, ItemQuantity, PriceRank, Product,
                          SalesLocation, StaffQuantityPlan, StoreEvent)
from sales.permissions_testing import grant

DAY = datetime.date(2026, 10, 21)   # 水曜


def make_report(name, date, sold, remaining, **kw):
    return DailyReport.objects.create(
        date=date, location=name, location_no=1, person_in_charge="", total_quantity=sold + remaining,
        total_sales_quantity=sold, total_remaining=remaining, total_revenue=0, total_others_sales=0,
        total_discount=0, sales_difference=0, **kw)


def seed_history(name, sold_values):
    d = DAY
    for sold in sold_values:
        d -= datetime.timedelta(days=1)
        make_report(name, d, sold, 60 - sold)


class JudgeTest(TestCase):
    def test_usual_needs_enough_days(self):
        seed_history("広尾", [50] * 5)
        self.assertIsNone(dq.usual_for("広尾", DAY))

    def test_usual_and_threshold_follow_store_spread(self):
        seed_history("広尾", [50, 52, 48, 51, 49, 50, 53, 47, 50, 30])
        info = dq.usual_for("広尾", DAY, one_in=10)
        self.assertEqual(info["usual"], 50)
        self.assertLess(info["threshold"], 0.95)

    def test_judge(self):
        info = {"usual": 50, "threshold": 0.8}
        early = datetime.time(12, 20)
        self.assertEqual(dq.judge(38, 10, None, info, early), "low")
        self.assertEqual(dq.judge(45, 10, None, info, early), "")
        self.assertEqual(dq.judge(38, 0, None, info, early), "")          # 完売は「売れなかった」ではない
        self.assertEqual(dq.judge(60, 0, datetime.time(11, 50), info, early), "early")
        self.assertEqual(dq.judge(60, 0, datetime.time(12, 30), info, early), "")

    def test_emptied_choice_list_is_only_no_question(self):
        setting = DailyReportQuestionSetting.load()
        setting.choices = {"indoor_low": []}
        self.assertEqual(dq.choices_for(setting, "indoor", "low"), [dq.NO_QUESTION])
        self.assertEqual(dq.choices_for(setting, "outdoor", "low")[-1], dq.NO_QUESTION)

    def test_lost_customers_make_demand(self):
        t = datetime.time
        self.assertEqual(estimated_demand(50, 0, t(12, 0), t(11, 0), t(13, 0), lost=5), 55)
        self.assertAlmostEqual(estimated_demand(50, 0, t(12, 0), t(11, 0), t(13, 0)), 55)   # 推定（+10%）
        self.assertEqual(estimated_demand(40, 5, None, None, None, lost=5), 40)        # 完売でない日は使わない


class InputFormTest(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="staff", password="pass")
        self.client.force_login(self.user)
        rank = PriceRank.objects.get(name="通常")
        self.menu = [Product.objects.create(no=i, week="2026-10-19", name=f"弁当{i}", rank=rank) for i in (1, 2)]
        self.loc = SalesLocation.objects.create(no=1, name="広尾", type="テーブル", price_type="A",
                                                service_name="なし", service_price=0)
        for p in self.menu:
            ItemQuantity.objects.create(target_date=DAY.isoformat(), target_week="2026-10-19",
                                        product=p, sales_location=self.loc, quantity=30)
        seed_history("広尾", [50, 52, 48, 51, 49, 50, 53, 47, 50, 51])

    def post(self, action, remaining=(10, 10), extra=None):
        data = {"action": action, "date": DAY.isoformat(), "location": "広尾", "paypay": "0",
                "digital_payment": "0", "cash": "0", "discount_signature": "new-form", "closing_time": "13:30"}
        for p, r in zip(self.menu, remaining):
            data[f"quantity_{p.no}"] = "30"
            data[f"remaining_{p.no}"] = str(r)
        data.update(extra or {})   # 値は一覧で渡す（同じ名前の欄が複数ある）
        return self.client.post("/daily_report/", data)

    def test_display_embeds_store_values_and_outdoor_choices(self):
        StaffQuantityPlan.objects.create(target_date=DAY + datetime.timedelta(days=1), location="広尾",
                                         qty_min=40, qty_max=45, written_on=DAY - datetime.timedelta(days=1))
        res = self.post("display")
        self.assertEqual(res.context["q_group"], "outdoor")
        self.assertEqual(res.context["q_js"]["usual"], 50)
        self.assertContains(res, "近くにキッチンカー・競合のお店が出ていた")
        self.assertContains(res, "販売中の天気")
        self.assertContains(res, "11〜13時頃")
        self.assertNotContains(res, "見通し")
        self.assertEqual(res.context["q_first_written"], "10/20に書かれた数：40〜45")

    def test_send_saves_answers_plans_and_events(self):
        self.post("send", remaining=(10, 10), extra={
            "weather_during_sales": ["light_rain"], "feels": ["cold"],
            "reasons_low": ["雨・寒さ・暑さで人通りが少なかった"], "unusual_note": "向かいで工事",
            "plan_date": ["2026-10-22", "2026-10-23", "2026-10-24"],
            "plan_qty_min": ["45", "", "50"], "plan_qty_max": ["40", "", ""],
            "plan_kept": ["10", "", ""], "plan_rain_min": ["", "", ""], "plan_rain_max": ["", "", ""],
            "event_date": ["2026-10-28"], "event_note": ["ビルの点検"],
            "food_count_setting": ["水曜は多めに"],
        })
        r = DailyReport.objects.get(date=DAY, location="広尾")
        self.assertEqual((r.weather_during_sales, r.feels, r.weather, r.temp), ("light_rain", "cold", "小雨", "寒い"))
        self.assertEqual(r.unusual_kind, "low")                  # 40食 ≤ 50×割合
        self.assertEqual(r.usual_sales, 50)
        self.assertEqual(r.unusual_reasons, ["雨・寒さ・暑さで人通りが少なかった"])
        self.assertIsNone(r.lost_customers)                      # 完売していない
        plans = {p.target_date.isoformat(): p for p in r.quantity_plans.all()}
        self.assertEqual(set(plans), {"2026-10-22"})             # 空の行・土曜（営業日でない）は送らない
        self.assertEqual((plans["2026-10-22"].qty_min, plans["2026-10-22"].qty_max, plans["2026-10-22"].kept), (40, 45, 10))
        self.assertEqual(list(r.store_events.values_list("note", flat=True)), ["ビルの点検"])

    def test_resend_replaces_plans_and_memo_only_is_ok(self):
        extra = {"plan_date": ["2026-10-22"], "plan_qty_min": ["40"], "plan_qty_max": [""],
                 "plan_kept": [""], "plan_rain_min": [""], "plan_rain_max": [""]}
        self.post("send", extra=extra)
        self.post("send", extra={**extra, "plan_qty_min": ["42"]})
        self.assertEqual(list(StaffQuantityPlan.objects.values_list("qty_min", flat=True)), [42])
        self.post("send", extra={"food_count_setting": ["決められないので相談"]})
        self.assertFalse(StaffQuantityPlan.objects.exists())
        self.assertEqual(DailyReport.objects.get(date=DAY).food_count_setting, "決められないので相談")

    def test_sold_out_keeps_lost_customers_and_no_reason_still_saves(self):
        self.post("send", remaining=(0, 0), extra={"lost_customers": ["５"], "sold_out_time": ["11:50"]})
        r = DailyReport.objects.get(date=DAY)
        self.assertEqual(r.lost_customers, 5)                    # 全角でも受け付ける
        self.assertEqual(r.unusual_kind, "early")
        self.assertEqual(r.unusual_reasons, [])                  # 理由が無くても送信は止めない

    def test_no_question_excludes_others(self):
        self.post("send", extra={"reasons_low": ["雨・寒さ・暑さで人通りが少なかった", dq.NO_QUESTION]})
        self.assertEqual(DailyReport.objects.get(date=DAY).unusual_reasons, [dq.NO_QUESTION])


class EditTest(TestCase):
    def setUp(self):
        self.admin = User.objects.create_user(username="boss", password="pass", is_staff=True)
        self.client.force_login(self.admin)
        SalesLocation.objects.create(no=1, name="コムチュア", type="室内", price_type="A",
                                     service_name="なし", service_price=0)

    def test_old_report_opens_with_no_record(self):
        r = make_report("コムチュア", DAY - datetime.timedelta(days=30), 40, 5, weather="晴れ", temp="暑い")
        res = self.client.get(f"/daily_report_list/{r.pk}/edit/")
        self.assertEqual(res.status_code, 200)
        self.assertContains(res, "記録なし（この機能より前の日計表）")
        self.assertContains(res, "以前の書き方：晴れ")
        self.assertContains(res, "この日計表には記録がありません")

    def test_auto_weather_text_shows_dash_for_missing(self):
        r = make_report("コムチュア", DAY, 40, 5, auto_lunch_precip=0.0, auto_lunch_temp=22.4, auto_lunch_feels=21.0)
        self.assertEqual(dqf.auto_weather_text(r), "雨 0.0mm／気温 22℃／体感 21℃／風 —／雪 —")

    def test_removed_choice_stays_selectable(self):
        r = make_report("コムチュア", DAY, 30, 20, unusual_kind="low", usual_sales=50,
                        unusual_reasons=["昔の選択肢"])
        ctx = dqf.edit_context(r)
        self.assertIn(("昔の選択肢", "昔の選択肢（今は選択肢にありません）"), ctx["q_choices_edit"])
        self.assertEqual(ctx["q_choices_edit"][-1][0], dq.NO_QUESTION)

    def test_apply_edit_keeps_legacy_weather_when_not_chosen(self):
        r = make_report("コムチュア", DAY, 30, 20)
        post = QueryDict(mutable=True)
        post.setlist("reasons_edit", ["会議・研修・行事で人がいなかった（外出・出張が多い）"])
        post["lost_customers"] = "3"
        dqf.apply_edit_answers(r, post, "晴れ、曇り", "暑い")
        self.assertEqual((r.weather, r.temp, r.weather_during_sales), ("晴れ、曇り", "暑い", ""))
        self.assertEqual(r.lost_customers, 3)
        post["weather_during_sales"] = "rain"
        dqf.apply_edit_answers(r, post, "晴れ、曇り", "暑い")
        self.assertEqual((r.weather, r.weather_during_sales), ("雨", "rain"))


class SettingsViewTest(TestCase):
    URL = "/daily-report-questions/"

    def setUp(self):
        self.user = User.objects.create_user(username="mgr", password="pass")
        self.client.force_login(self.user)

    def form_data(self, **override):
        data = {"low_one_in": "10", "early_before": "12:20"}
        for key, _ in dq.CHOICE_KEYS:
            data[f"choice_{key}"] = dq.DEFAULT_CHOICES[key]
        data.update(override)
        return data

    def test_needs_forecast_permission(self):
        self.assertNotEqual(self.client.get(self.URL).status_code, 200)
        self.client.post(self.URL, self.form_data(low_one_in="5"))
        self.assertEqual(DailyReportQuestionSetting.load().low_one_in, 10)

    def test_save_and_validation(self):
        grant(self.user, "can_view_performance_by_location")
        res = self.client.get(self.URL)
        self.assertEqual(res.status_code, 200)
        self.assertContains(res, "日計表の質問の設定")

        bad = self.form_data(choice_indoor_low=["会議", ""])
        res = self.client.post(self.URL, bad)
        self.assertEqual(res.status_code, 200)
        self.assertContains(res, "直してほしいところが1つあります")
        self.assertEqual(DailyReportQuestionSetting.load().choices, {})

        res = self.client.post(self.URL, self.form_data(low_one_in="20", early_before="12:05",
                                                        choice_indoor_low=["会議が多い日"]))
        self.assertEqual(res.status_code, 302)
        s = DailyReportQuestionSetting.load()
        self.assertEqual((s.low_one_in, s.early_before), (20, datetime.time(12, 5)))
        self.assertEqual(dq.choices_for(s, "indoor", "low"), ["会議が多い日", dq.NO_QUESTION])
