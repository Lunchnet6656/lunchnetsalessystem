"""販売場所データへの一本化（旧シフト管理の販売場所設定・ルート設定）と、持参数の便の区切りのテスト。"""
import datetime
from unittest import mock

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from sales.item_quantity_import import ParsedItemQuantity, save_item_quantities
from sales.models import CarpoolRoute, Product, SalesLocation, UserMenuPermission
from sales.permissions_testing import grant
from shifts.models import SchedulePeriod

User = get_user_model()


def _row(i, loc, **shift):
    """location_list の1行分のPOST。shift を渡すと［シフト・便］の欄も描かれた扱い。"""
    data = {
        f"location[{i}][id]": loc.id, f"location[{i}][no]": loc.no, f"location[{i}][name]": loc.name,
        f"location[{i}][type]": loc.type, f"location[{i}][price_type]": loc.price_type,
        f"location[{i}][service_name]": loc.service_name, f"location[{i}][service_price]": 0,
        f"location[{i}][service_style]": "なし", f"location[{i}][direct_return]": 0,
        f"location[{i}][expects_cash]": "1",
    }
    if shift:
        data[f"location[{i}][shift]"] = "1"
        data.update({f"location[{i}][{k}]": v for k, v in shift.items()})
    return data


class LocationListShiftFieldsTest(TestCase):
    def setUp(self):
        self.user = User.objects.create_user("jimu", password="pw-jimu-123456")
        grant(self.user, "can_view_location_list")
        self.client.force_login(self.user)
        self.route = CarpoolRoute.objects.create(name="新川", departure_time=datetime.time(9, 15), display_order=2)
        self.loc = SalesLocation.objects.create(no=1, name="新川", type="", price_type="A", service_name="")

    def test_shift_tab_saves_all_moved_fields(self):
        res = self.client.post(reverse("location_list"), {"tab": "shift", **_row(
            0, self.loc, carpool_route=self.route.id, priority="S", requires_drive="1",
            excluded_from_shift="1", weather_area="shinyokohama")})
        self.assertRedirects(res, reverse("location_list") + "?tab=shift", fetch_redirect_response=False)
        self.loc.refresh_from_db()
        self.assertEqual(
            (self.loc.carpool_route, self.loc.priority, self.loc.requires_drive, self.loc.excluded_from_shift,
             self.loc.weather_area),
            (self.route, "S", True, True, "shinyokohama"))
        # チェックを外す・便なし・知らない値は無視
        self.client.post(reverse("location_list"), _row(
            0, self.loc, carpool_route="", priority="Z", weather_area="osaka"))
        self.loc.refresh_from_db()
        self.assertEqual(
            (self.loc.carpool_route, self.loc.priority, self.loc.requires_drive, self.loc.excluded_from_shift,
             self.loc.weather_area),
            (None, "S", False, False, "shinyokohama"))

    def test_rows_without_shift_fields_keep_them(self):
        SalesLocation.objects.filter(id=self.loc.id).update(
            carpool_route=self.route, requires_drive=True, priority="B")
        self.client.post(reverse("location_list"), _row(0, self.loc))
        self.loc.refresh_from_db()
        self.assertEqual((self.loc.carpool_route, self.loc.requires_drive, self.loc.priority), (self.route, True, "B"))

    def test_unknown_route_id_becomes_none(self):
        self.client.post(reverse("location_list"), _row(0, self.loc, carpool_route="99999", priority="A"))
        self.loc.refresh_from_db()
        self.assertIsNone(self.loc.carpool_route)

    def test_screen_has_tabs_and_help(self):
        res = self.client.get(reverse("location_list") + "?tab=routes")
        for text in ("シフト・便", "便の一覧", "優先度", "運転必須", "シフト対象外", "天気の地点", "新川（9:15）", "hp-help-locations"):
            self.assertContains(res, text)

    def test_routes_create_update_delete(self):
        url = reverse("location_routes")
        SalesLocation.objects.filter(id=self.loc.id).update(carpool_route=self.route)
        res = self.client.post(url, {f"route_{self.route.id}_name": "新川便", f"route_{self.route.id}_time": "09:20",
                                     f"route_{self.route.id}_order": "3",
                                     "new_name": "中里", "new_time": "10:00", "new_order": "11"})
        self.assertRedirects(res, reverse("location_list") + "?tab=routes", fetch_redirect_response=False)
        self.route.refresh_from_db()
        self.assertEqual((self.route.name, self.route.departure_time, self.route.display_order),
                         ("新川便", datetime.time(9, 20), 3))
        self.assertTrue(CarpoolRoute.objects.filter(name="中里", departure_time=datetime.time(10, 0)).exists())
        # 便名・時刻が空の行は保存しない
        self.client.post(url, {f"route_{self.route.id}_name": "", f"route_{self.route.id}_time": "09:20"})
        self.route.refresh_from_db()
        self.assertEqual(self.route.name, "新川便")
        # 削除すると店の便は「なし」
        self.client.post(url, {"delete_route": self.route.id})
        self.assertFalse(CarpoolRoute.objects.filter(id=self.route.id).exists())
        self.loc.refresh_from_db()
        self.assertIsNone(self.loc.carpool_route)

    def test_without_permission(self):
        UserMenuPermission.objects.filter(user=self.user).update(can_view_location_list=False)
        self.user._state.fields_cache.pop("menu_permission", None)
        res = self.client.post(reverse("location_routes"), {"new_name": "x", "new_time": "09:00"})
        self.assertRedirects(res, "/dashboard/", fetch_redirect_response=False)
        self.assertFalse(CarpoolRoute.objects.filter(name="x").exists())


class ShiftScreensMovedTest(TestCase):
    def setUp(self):
        self.staff = User.objects.create_user("boss", password="pw-boss-123456", is_staff=True)
        self.client.force_login(self.staff)

    def test_old_urls_redirect(self):
        self.assertRedirects(self.client.get("/shifts/admin/location-settings/"),
                             reverse("location_list") + "?tab=shift", fetch_redirect_response=False)
        self.assertRedirects(self.client.get("/shifts/admin/carpool-routes/"),
                             reverse("location_list") + "?tab=routes", fetch_redirect_response=False)

    def test_nav_links_removed(self):
        res = self.client.get(reverse("shifts:admin_periods"))
        self.assertEqual(res.status_code, 200)
        self.assertNotContains(res, "ルート設定")
        self.assertNotContains(res, ">販売場所設定<")

    def test_assignment_grid_groups_by_route_set_in_location_list(self):
        """販売場所データで付けた便が、シフトの割当画面の便ごとの表示にそのまま出る。"""
        route = CarpoolRoute.objects.create(name="道玄坂", departure_time=datetime.time(9, 45), display_order=9)
        SalesLocation.objects.create(no=1, name="道玄坂", type="", price_type="A", service_name="", carpool_route=route)
        SalesLocation.objects.create(no=2, name="広尾", type="", price_type="A", service_name="")
        day = datetime.date(2026, 10, 5)
        period = SchedulePeriod.objects.create(start_date=day, end_date=day, submission_open_at=timezone.now(),
                                               submission_close_at=timezone.now(), status="REVIEW")
        res = self.client.get(reverse("shifts:admin_period_assignment", args=[period.id]))
        self.assertEqual(res.status_code, 200)
        groups = res.context["route_groups"]
        self.assertEqual(groups[0]["route"], route)
        self.assertEqual([r["location"].name for r in groups[0]["rows"]], ["道玄坂"])
        self.assertIsNone(groups[1]["route"])
        self.assertEqual([r["location"].name for r in groups[1]["rows"]], ["広尾"])


@mock.patch("django.utils.timezone.localdate", return_value=datetime.date(2026, 11, 5))
class ItemQuantityRouteOrderTest(TestCase):
    def test_locations_in_no_order_with_route(self, _):
        user = User.objects.create_user("t", password="pw-t-123456")
        grant(user, "can_view_item_quantity")
        self.client.force_login(user)
        week = datetime.date(2026, 11, 2)
        p = Product.objects.create(no=1, week=week.isoformat(), name="唐揚げ")
        shinkawa = CarpoolRoute.objects.create(name="新川", departure_time=datetime.time(9, 15))
        locs = [
            SalesLocation.objects.create(no=3, name="京橋", type="", price_type="A", service_name="", carpool_route=shinkawa),
            SalesLocation.objects.create(no=1, name="新川", type="", price_type="A", service_name="", carpool_route=shinkawa),
            SalesLocation.objects.create(no=2, name="広尾", type="", price_type="A", service_name=""),
        ]
        save_item_quantities(ParsedItemQuantity(target_date=datetime.date(2026, 11, 5), target_week=week,
                                                entries=[(p, loc, 3) for loc in locs]))
        data = self.client.get(reverse("item_quantity_edit", args=["2026-11-05"])).context["grid_data"]
        self.assertEqual([(l["name"], l["routeName"], l["routeTime"]) for l in data["locations"]],
                         [("新川", "新川", "9:15"), ("広尾", "", ""), ("京橋", "新川", "9:15")])


class LocationStatusTabTest(TestCase):
    """販売場所データの［出店状況］タブ（公開ページの並び・掲載・本日の上書き・QR受付・QRのPDF）。#207"""

    def setUp(self):
        self.user = User.objects.create_user("jimu", password="pw-jimu-123456")
        grant(self.user, "can_view_location_list")
        self.client.force_login(self.user)
        self.a = SalesLocation.objects.create(no=1, name="新川", type="", price_type="A", service_name="")
        self.b = SalesLocation.objects.create(no=2, name="人形町", type="", price_type="A", service_name="")
        self.off = SalesLocation.objects.create(no=3, name="配達", type="", price_type="A", service_name="",
                                                excluded_from_shift=True, qr_enabled=True)
        patcher = mock.patch("lunchnetsale.views.call_command")
        self.publish = patcher.start()
        self.addCleanup(patcher.stop)

    def post(self, rows, **extra):
        return self.client.post(reverse("location_list"), {"tab": "status", "pub": "1", "pub_row": rows, **extra})

    def test_saves_order_show_override_qr_and_publishes(self):
        res = self.post([self.b.id, self.off.id, self.a.id], **{
            f"pub_show_{self.a.id}": "1", f"pub_override_{self.a.id}": "sold_out",
            f"pub_override_{self.b.id}": "", f"pub_qr_{self.b.id}": "1",
            f"pub_show_{self.off.id}": "1", f"pub_override_{self.off.id}": "closed",
        })
        self.assertRedirects(res, reverse("location_list") + "?tab=status", fetch_redirect_response=False)
        for x in (self.a, self.b, self.off):
            x.refresh_from_db()
        today = timezone.localdate()
        self.assertEqual((self.b.public_order, self.off.public_order, self.a.public_order), (1, 2, 3))
        self.assertEqual((self.a.today_override, self.a.today_override_date, self.a.excluded_from_public_status),
                         ("sold_out", today, False))
        self.assertEqual((self.b.excluded_from_public_status, self.b.qr_enabled), (True, True))
        # シフト対象外でも掲載・本日・QR受付は変えられる（公開ページはシフト対象外と切り離し）
        self.assertEqual((self.off.excluded_from_public_status, self.off.today_override, self.off.qr_enabled),
                         (False, "closed", False))
        self.publish.assert_called_once_with("publish_status_json")

    def test_no_publish_when_nothing_public_changes(self):
        same = {f"pub_show_{self.a.id}": "1", f"pub_show_{self.b.id}": "1", f"pub_qr_{self.off.id}": "1"}
        self.post([self.a.id, self.b.id, self.off.id], **same)  # 初回で並び・掲載が入る
        self.publish.reset_mock()
        self.post([self.a.id, self.b.id, self.off.id], **{**same, f"pub_qr_{self.b.id}": "1"})  # QR受付だけ変更
        self.publish.assert_not_called()

    def test_stale_override_shown_as_none_and_kept_none(self):
        SalesLocation.objects.filter(pk=self.a.pk).update(today_override="closed",
                                                          today_override_date=datetime.date(2020, 1, 1))
        res = self.client.get(reverse("location_list") + "?tab=status")
        row = next(r for r in res.context["public_rows"] if r["loc"].id == self.a.id)
        self.assertEqual(row["override"], "")

    def test_qr_pdf_for_selected(self):
        with mock.patch("sales.qr_pdf.build_qr_pdf", return_value=b"%PDF-1.4") as build:
            res = self.client.post(reverse("location_qr_pdf"), {"pub_sel": [self.b.id]})
        self.assertEqual(res["Content-Type"], "application/pdf")
        self.assertEqual([l.name for l in build.call_args[0][0]], ["人形町"])
        res = self.client.post(reverse("location_qr_pdf"), {})
        self.assertRedirects(res, reverse("location_list") + "?tab=status", fetch_redirect_response=False)

    def test_tab_rendered(self):
        res = self.client.get(reverse("location_list") + "?tab=status")
        self.assertContains(res, "出店状況")
        self.assertContains(res, "QRのPDF")
