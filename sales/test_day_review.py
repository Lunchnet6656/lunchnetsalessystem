"""日計表一覧の「その日の振り返り」（保存・削除・表示・権限）のテスト。"""
from datetime import date, time

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from sales.models import DailyReport, DayReview, SalesLocation
from sales.permissions_testing import grant

DAY = date(2026, 9, 7)
URL = reverse("daily_report_review_save", args=["2026-09-07"])


def report(d, name, no):
    DailyReport.objects.create(
        date=d, location=name, location_no=no, total_quantity=50, total_sales_quantity=45, total_remaining=5,
        total_revenue=0, opening_time=time(11, 0), closing_time=time(13, 0),
    )


class DayReviewTests(TestCase):
    def setUp(self):
        self.a = SalesLocation.objects.create(no=1, name="A店", type="車", price_type="A", service_name="")
        self.b = SalesLocation.objects.create(no=2, name="B店", type="室内", price_type="A", service_name="")
        self.c = SalesLocation.objects.create(no=3, name="C店", type="テーブル", price_type="A", service_name="")
        for loc in (self.a, self.b, self.c):
            report(DAY, loc.name, loc.no)
        User = get_user_model()
        self.owner = User.objects.create_user("owner", password="pw-owner-123456")
        grant(self.owner, "can_view_daily_report_list", "can_view_performance_by_location")
        self.viewer = User.objects.create_user("viewer", password="pw-viewer-123456")
        grant(self.viewer, "can_view_daily_report_list")

    def post(self, data, user=None):
        self.client.force_login(user or self.owner)
        return self.client.post(URL, data)

    def test_save_comment_and_events(self):
        res = self.post({
            "comment": "B店が急に休み", "ev-TOTAL": "2",
            "ev-0-kind": "absence", "ev-0-scope": "select", "ev-0-locations": [self.b.id], "ev-0-exclude": "1", "ev-0-note": "ドタキャン",
            "ev-1-kind": "reallocation", "ev-1-scope": "select", "ev-1-locations": [self.a.id, self.c.id],
            "search_date_start": "2026-09-01",
        })
        self.assertRedirects(res, reverse("daily_report_list") + "?search_date_start=2026-09-01&saved=2026-09-07#day-2026-09-07",
                             fetch_redirect_response=False)
        r = DayReview.objects.get(date=DAY)
        self.assertEqual(r.comment, "B店が急に休み")
        self.assertEqual(r.updated_by, self.owner)
        ev = list(r.events.all())
        self.assertEqual([e.kind for e in ev], ["absence", "reallocation"])
        self.assertTrue(ev[0].exclude)
        self.assertFalse(ev[1].exclude)
        self.assertEqual({l.name for l in ev[1].locations.all()}, {"A店", "C店"})

    def test_all_locations_does_not_store_locations(self):
        self.post({"comment": "", "ev-TOTAL": "1", "ev-0-kind": "stop", "ev-0-scope": "all",
                   "ev-0-locations": [self.a.id], "ev-0-exclude": "1"})
        ev = DayReview.objects.get(date=DAY).events.get()
        self.assertTrue(ev.all_locations)
        self.assertEqual(ev.locations.count(), 0)

    def test_events_are_replaced_and_deleted_cards_skipped(self):
        self.post({"comment": "x", "ev-TOTAL": "1", "ev-0-kind": "stop", "ev-0-scope": "all", "ev-0-exclude": "1"})
        # 1件目は削除予定（送られない）・2件目だけ送る
        self.post({"comment": "x", "ev-TOTAL": "2", "ev-1-kind": "delivery", "ev-1-scope": "all"})
        self.assertEqual([e.kind for e in DayReview.objects.get(date=DAY).events.all()], ["delivery"])

    def test_validation_errors_keep_data(self):
        res = self.post({"comment": "", "ev-TOTAL": "0"})
        self.assertIn("review_error=2026-09-07", res["Location"])
        self.assertFalse(DayReview.objects.exists())
        res = self.post({"comment": "x", "ev-TOTAL": "1", "ev-0-kind": "", "ev-0-scope": "select"})
        self.assertEqual(self.client.session["day_review_errors"], ["種類を選択", "店を1つ以上選択"])
        self.assertFalse(DayReview.objects.exists())
        page = self.client.get(res["Location"])
        self.assertContains(page, "保存失敗：種類を選択・店を1つ以上選択")

    def test_delete(self):
        self.post({"comment": "x", "ev-TOTAL": "0"})
        res = self.post({"action": "delete"})
        self.assertIn("deleted=2026-09-07", res["Location"])
        self.assertFalse(DayReview.objects.exists())

    def test_viewer_cannot_save(self):
        self.post({"comment": "x", "ev-TOTAL": "0"}, user=self.viewer)
        self.assertFalse(DayReview.objects.exists())

    def _review(self):
        r = DayReview.objects.create(date=DAY, comment="バジルスパが傷んでいた")
        r.events.create(kind="stop", all_locations=True, exclude=True)
        e = r.events.create(kind="delivery", exclude=False)
        e.locations.add(self.c)
        return r

    def test_list_shows_review_and_modal_for_owner(self):
        self._review()
        self.client.force_login(self.owner)
        res = self.client.get(reverse("daily_report_list"))
        self.assertContains(res, "バジルスパが傷んでいた")
        self.assertContains(res, "商品の販売中止")
        self.assertContains(res, "予測除外")
        self.assertContains(res, "出来事2")
        self.assertContains(res, 'id="drv-modal"')
        self.assertContains(res, "daily_report_review.js")
        from django.utils import timezone
        updated = res.context["reviews_json"]["2026-09-07"]["updated"]
        self.assertTrue(updated.startswith(f"{timezone.localtime(DayReview.objects.get().updated_at):%Y/%m/%d %H:%M}"))

    def test_viewer_sees_review_without_modal(self):
        self._review()
        self.client.force_login(self.viewer)
        res = self.client.get(reverse("daily_report_list"))
        self.assertContains(res, "バジルスパが傷んでいた")
        self.assertNotContains(res, 'id="drv-modal"')
        self.assertNotContains(res, "daily_report_review.js")
        self.assertNotContains(res, "drv-act")

    def test_filtered_view_marks_affected_location(self):
        self._review()
        self.client.force_login(self.owner)
        res = self.client.get(reverse("daily_report_list"), {"search_location": "C店"})
        self.assertEqual(res.context["filtered_reports"][0].review_mark, "exclude")  # 全店の除外が優先
        self.assertNotContains(res, 'id="drv-modal"')
        r = DayReview.objects.get(date=DAY)
        r.events.filter(kind="stop").delete()
        res = self.client.get(reverse("daily_report_list"), {"search_location": "C店"})
        self.assertEqual(res.context["filtered_reports"][0].review_mark, "event")
        res = self.client.get(reverse("daily_report_list"), {"search_location": "A店"})
        self.assertIsNone(res.context["filtered_reports"][0].review_mark)


class ReflectionTests(TestCase):
    """振り返りを食数予測（同じ曜日の実績の印）と実績分析（特記事項）に出す。"""

    def setUp(self):
        self.a = SalesLocation.objects.create(no=1, name="A店", type="車", price_type="A", service_name="")
        self.b = SalesLocation.objects.create(no=2, name="B店", type="室内", price_type="A", service_name="")

    def test_stop_notes_only_stop_kinds_in_period(self):
        from sales.day_review import stop_notes
        r = DayReview.objects.create(date=DAY, comment="バジルスパが傷んでいた")
        r.events.create(kind="stop", all_locations=True)
        r.events.create(kind="reallocation").locations.add(self.a)
        r2 = DayReview.objects.create(date=date(2026, 9, 9), comment="")
        r2.events.create(kind="absence", note="ドタキャン").locations.add(self.b)
        DayReview.objects.create(date=date(2026, 10, 1)).events.create(kind="stop", all_locations=True)
        r.events.create(kind="absence").locations.add(self.a)
        notes = stop_notes(date(2026, 9, 1), date(2026, 9, 30))
        self.assertEqual(notes["count"], 3)
        self.assertEqual([d["date"] for d in notes["days"]], [DAY, date(2026, 9, 9)])
        self.assertEqual([e["label"] for e in notes["days"][0]["events"]], ["商品の販売中止", "販売員の欠勤で販売中止"])
        self.assertEqual(notes["days"][0]["comment"], "バジルスパが傷んでいた")
        self.assertEqual(notes["days"][1]["events"][0]["names"], ["B店"])
        self.assertEqual(notes["more"], [])

    def test_stop_notes_fold_after_20_at_day_boundary(self):
        from datetime import timedelta
        from sales.day_review import stop_notes
        for i in range(8):
            r = DayReview.objects.create(date=date(2026, 9, 1) + timedelta(days=i))
            for _ in range(3):
                r.events.create(kind="stop", all_locations=True)
        notes = stop_notes(date(2026, 9, 1), date(2026, 9, 30))
        self.assertEqual((len(notes["days"]), len(notes["more"]), notes["more_count"]), (7, 1, 3))

    def test_analysis_page_shows_notes(self):
        r = DayReview.objects.create(date=DAY, comment="バジルスパが傷んでいた")
        r.events.create(kind="stop", all_locations=True)
        r.events.create(kind="delivery", note="配送メモ", all_locations=True)
        user = get_user_model().objects.create_user("boss", password="pw-boss-123456")
        grant(user, "can_view_dashboard")
        self.client.force_login(user)
        res = self.client.get(reverse("performance_analysis"), {"start": "2026-09-01", "end": "2026-09-30"})
        self.assertContains(res, 'id="sec-notes"')
        self.assertContains(res, "販売中止 1件")
        self.assertContains(res, "バジルスパが傷んでいた")
        self.assertNotContains(res, "配送メモ")
        self.assertNotContains(res, "daily_report_list/?search_date_start=2026-09-07")  # 日計表一覧の権限なし
        grant(user, "can_view_daily_report_list")
        res = self.client.get(reverse("performance_analysis"), {"start": "2026-09-01", "end": "2026-09-30"})
        self.assertContains(res, "daily_report_list/?search_date_start=2026-09-07")
        res = self.client.get(reverse("performance_analysis"), {"start": "2026-10-01", "end": "2026-10-31"})
        self.assertContains(res, "販売中止 0件")

    def test_forecast_keeps_excluded_day_with_label_but_not_in_calc(self):
        from datetime import timedelta
        from unittest.mock import patch
        from sales.meal_forecast import load_history
        today = date(2026, 10, 2)
        d = today - timedelta(days=1)
        days = []
        while len(days) < 30:
            if d.weekday() < 5:
                days.append(d)
            d -= timedelta(days=1)
        for x in days:
            report(x, "A店", 1)
        mon = max(x for x in days if x.weekday() == 0)
        DayReview.objects.create(date=mon).events.create(kind="stop", all_locations=True)
        self.assertNotIn(mon, {r["date"] for r in load_history(today - timedelta(days=60), today)["A店"]})
        user = get_user_model().objects.create_user("fc", password="pw-fc-123456")
        grant(user, "can_view_performance_by_location")
        self.client.force_login(user)
        with patch("lunchnetsale.views.timezone.localdate", return_value=today), \
             patch("lunchnetsale.views._fetch_lunch_weather", return_value={}):
            res = self.client.get(reverse("meal_forecast"))
        monday = next(dd for dd in res.context["fc"]["days"] if dd["date"].weekday() == 0)
        last3 = next(l for l in monday["locs"] if l["name"] == "A店")["last3"]
        self.assertEqual((last3[0]["date"], last3[0]["excluded"]), (mon, "商品の販売中止"))
        self.assertIsNone(last3[1]["excluded"])
        self.assertContains(res, 'aria-label="除外：商品の販売中止"')
