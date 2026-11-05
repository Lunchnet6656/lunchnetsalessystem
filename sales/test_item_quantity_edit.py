"""持参数の修正・消し込み（#203）のテスト。番号は要件定義の検証基準。"""
import datetime
import json
from unittest import mock

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import NoReverseMatch, reverse

from sales.item_quantity_import import ParsedItemQuantity, save_item_quantities
from sales.models import (
    DailyReport, ItemQuantity, ItemQuantityChange, ItemQuantityUpload, Product, SalesLocation, UserMenuPermission,
)
from sales.permissions_testing import grant

TODAY = datetime.date(2026, 11, 5)      # 木曜
TOMORROW = datetime.date(2026, 11, 6)   # 金曜
WEEK = datetime.date(2026, 11, 2)


def _receive(d, quantities):
    """振分表からの受信（API・画面アップロードと同じ保存処理）。quantities = {(product, loc): 数}"""
    parsed = ParsedItemQuantity(target_date=d, target_week=WEEK,
                                entries=[(p, loc, q) for (p, loc), q in quantities.items()])
    save_item_quantities(parsed)
    return ItemQuantityUpload.objects.create(target_date=d, source="api", ok=True, saved_count=len(parsed.entries))


@mock.patch("django.utils.timezone.localdate", return_value=TODAY)
class ItemQuantityEditTest(TestCase):
    def setUp(self):
        # 出店状況の反映（GitHub への push）をテストから本当に呼ばない
        patcher = mock.patch("sales.item_quantity_edit.call_command")
        patcher.start()
        self.addCleanup(patcher.stop)
        self.user =get_user_model().objects.create_user("furiwake", password="pw-furiwake-123456",
                                                         last_name="振分", first_name="タブレット")
        grant(self.user, "can_view_item_quantity")
        self.client.force_login(self.user)
        self.tonkatsu = Product.objects.create(no=1, week=WEEK.isoformat(), name="とんかつカレーソース")
        self.saba = Product.objects.create(no=2, week=WEEK.isoformat(), name="サバの竜田揚げ香味ソース")
        self.shinkawa = SalesLocation.objects.create(no=1, name="新川", type="", price_type="A", service_name="")
        self.ningyo = SalesLocation.objects.create(no=2, name="人形町", type="", price_type="A", service_name="")
        self.honcho = SalesLocation.objects.create(no=3, name="本町", type="", price_type="A", service_name="")
        self.base = {
            (self.tonkatsu, self.shinkawa): 12, (self.tonkatsu, self.ningyo): 6, (self.tonkatsu, self.honcho): 0,
            (self.saba, self.shinkawa): 5, (self.saba, self.honcho): 0,  # サバ×人形町は振分表が送っていない
        }
        self.upload = _receive(TODAY, self.base)

    def iq(self, p, loc, d=TODAY):
        return ItemQuantity.objects.get(target_date=d.isoformat(), product=p, sales_location=loc)

    def save(self, changes, d=TODAY, upload_id="same"):
        body = {"upload_id": self.upload.id if upload_id == "same" else upload_id, "changes": [
            {"product_id": p.id, "location_id": loc.id, "value": v, "base": b} for p, loc, v, b in changes]}
        return self.client.post(reverse("item_quantity_edit_save", args=[d.isoformat()]),
                                json.dumps(body), content_type="application/json")

    def checks(self, items, d=TODAY):
        body = {"checks": [{"product_id": p.id, "location_id": loc.id, "checked": c} for p, loc, c in items]}
        return self.client.post(reverse("item_quantity_checks", args=[d.isoformat()]),
                                json.dumps(body), content_type="application/json")

    # 1
    def test_after_receive_plan_equals_current(self, _):
        for iq in ItemQuantity.objects.all():
            self.assertEqual(iq.planned_quantity, iq.quantity)

    # 2・3
    def test_save_changes_current_only_and_records_history(self, _):
        res = self.save([(self.tonkatsu, self.shinkawa, 10, 12)])
        self.assertEqual(res.status_code, 200)
        iq = self.iq(self.tonkatsu, self.shinkawa)
        self.assertEqual((iq.quantity, iq.planned_quantity), (10, 12))
        ch = ItemQuantityChange.objects.get()
        self.assertEqual((ch.target_date, ch.sales_location, ch.product, ch.before, ch.after, ch.changed_by),
                         (TODAY, self.shinkawa, self.tonkatsu, 12, 10, self.user))
        self.assertIsNotNone(ch.changed_at)
        page = self.client.get(reverse("item_quantity_edit", args=[TODAY.isoformat()]))
        self.assertContains(page, "保存 1件")
        self.assertContains(page, "12 → 10")
        self.assertContains(page, "振分 タブレット")

    # 4
    def test_two_edits_two_history_rows_plan_unchanged(self, _):
        self.save([(self.tonkatsu, self.shinkawa, 10, 12)])
        self.save([(self.tonkatsu, self.shinkawa, 9, 10)])
        self.assertEqual(ItemQuantityChange.objects.count(), 2)
        self.assertEqual(self.iq(self.tonkatsu, self.shinkawa).planned_quantity, 12)

    # 5（数と目印は画面のJSがこのデータから描く）
    def test_screen_data(self, _):
        self.save([(self.tonkatsu, self.shinkawa, 10, 12)])
        res = self.client.get(reverse("item_quantity_edit", args=[TODAY.isoformat()]))
        data = res.context["grid_data"]
        self.assertEqual([p["name"] for p in data["products"]], ["とんかつカレーソース", "サバの竜田揚げ香味ソース"])
        self.assertEqual([l["name"] for l in data["locations"]], ["新川", "人形町", "本町"])
        self.assertEqual(data["cells"][f"{self.tonkatsu.id}:{self.shinkawa.id}"], {"cur": 10, "plan": 12, "ck": False})
        self.assertEqual([l["off"] for l in data["locations"]], [False, False, True])
        self.assertTrue(data["editable"])
        self.assertEqual(res.context["change_count"], 1)
        self.assertContains(res, "ログイン 振分 タブレット")

    # 6
    def test_daily_report_uses_current(self, _):
        self.save([(self.tonkatsu, self.shinkawa, 10, 12)])
        res = self.client.post("/daily_report/", {"action": "display", "date": TODAY.isoformat(), "location": "新川"})
        quantities = {it["menu_no"]: it["quantity"] for it in res.context["item_data"]}
        self.assertEqual(quantities[1], 10)

    # 7
    def test_without_permission(self, _):
        UserMenuPermission.objects.filter(user=self.user).update(can_view_item_quantity=False)
        self.user._state.fields_cache.pop("menu_permission", None)
        res = self.client.get(reverse("item_quantity_edit", args=[TODAY.isoformat()]))
        self.assertRedirects(res, "/dashboard/", fetch_redirect_response=False)
        self.assertEqual(self.save([(self.tonkatsu, self.shinkawa, 10, 12)]).status_code, 403)
        self.assertEqual(self.checks([(self.tonkatsu, self.shinkawa, True)]).status_code, 403)
        self.assertEqual(self.iq(self.tonkatsu, self.shinkawa).quantity, 12)

    # 8
    def test_negative_or_text_saves_nothing(self, _):
        for bad in (-1, "3", 1.5, None, True):
            res = self.save([(self.tonkatsu, self.ningyo, 4, 6), (self.tonkatsu, self.shinkawa, bad, 12)])
            self.assertEqual(res.status_code, 400)
        self.assertEqual(self.iq(self.tonkatsu, self.ningyo).quantity, 6)
        self.assertFalse(ItemQuantityChange.objects.exists())

    def test_conflict_saves_nothing(self, _):
        # 電話番のPCが先に新川を保存 → タブレットは開いた時の数(12)のまま送る
        self.save([(self.tonkatsu, self.shinkawa, 11, 12)])
        res = self.save([(self.tonkatsu, self.ningyo, 4, 6), (self.tonkatsu, self.shinkawa, 10, 12)])
        self.assertEqual(res.status_code, 409)
        self.assertEqual(res.json()["conflicts"], [{
            "product_id": self.tonkatsu.id, "location_id": self.shinkawa.id, "current": 11,
            "label": "新川×とんかつカレーソース"}])
        self.assertEqual(self.iq(self.tonkatsu, self.ningyo).quantity, 6)
        self.assertEqual(ItemQuantityChange.objects.count(), 1)

    def test_reupload_since_open_saves_nothing(self, _):
        _receive(TODAY, self.base)
        res = self.save([(self.tonkatsu, self.ningyo, 4, 6)])
        self.assertEqual(res.status_code, 409)
        self.assertTrue(res.json()["reupload"])
        self.assertEqual(self.iq(self.tonkatsu, self.ningyo).quantity, 6)

    # 10
    def test_reupload_overwrites_plan_and_current(self, _):
        self.save([(self.tonkatsu, self.shinkawa, 10, 12)])
        _receive(TODAY, {**self.base, (self.tonkatsu, self.shinkawa): 14})
        iq = self.iq(self.tonkatsu, self.shinkawa)
        self.assertEqual((iq.quantity, iq.planned_quantity), (14, 14))
        # 再送より前の修正は履歴で灰色（上書きで消えている）
        res = self.client.get(reverse("item_quantity_edit", args=[TODAY.isoformat()]))
        self.assertTrue(res.context["history"][1]["stale"])
        self.assertContains(res, "iq-history-upload")

    def test_reupload_clears_check_only_when_number_changed(self, _):
        self.checks([(self.tonkatsu, self.shinkawa, True), (self.tonkatsu, self.ningyo, True)])
        _receive(TODAY, {**self.base, (self.tonkatsu, self.shinkawa): 14})
        self.assertIsNone(self.iq(self.tonkatsu, self.shinkawa).checked_at)
        self.assertIsNotNone(self.iq(self.tonkatsu, self.ningyo).checked_at)

    def test_plan_zero_cell_can_be_added(self, _):
        res = self.save([(self.saba, self.ningyo, 3, 0)])
        self.assertEqual(res.status_code, 200)
        iq = self.iq(self.saba, self.ningyo)
        self.assertEqual((iq.quantity, iq.planned_quantity, iq.target_week), (3, 0, WEEK.isoformat()))

    def test_cancelled_store_cannot_be_edited_or_checked(self, _):
        self.assertEqual(self.save([(self.tonkatsu, self.honcho, 3, 0)]).status_code, 400)
        self.assertEqual(self.iq(self.tonkatsu, self.honcho).quantity, 0)
        self.checks([(self.tonkatsu, self.honcho, True)])
        self.assertIsNone(self.iq(self.tonkatsu, self.honcho).checked_at)

    def test_store_reduced_to_zero_stays_editable(self, _):
        self.save([(self.tonkatsu, self.ningyo, 0, 6)])
        res = self.save([(self.tonkatsu, self.ningyo, 2, 0)])
        self.assertEqual(res.status_code, 200)

    def test_past_and_far_dates_are_read_only(self, _):
        past = datetime.date(2026, 11, 4)
        _receive(past, self.base)
        res = self.client.get(reverse("item_quantity_edit", args=[past.isoformat()]))
        self.assertFalse(res.context["editable"])
        self.assertContains(res, "閲覧のみ")
        self.assertNotContains(res, 'id="iq-mode-check"')
        self.assertEqual(self.save([(self.tonkatsu, self.shinkawa, 1, 12)], d=past, upload_id=None).status_code, 400)
        self.assertEqual(self.checks([(self.tonkatsu, self.shinkawa, True)], d=past).status_code, 400)
        self.assertEqual(self.iq(self.tonkatsu, self.shinkawa, past).quantity, 12)

    def test_tomorrow_is_editable(self, _):
        up = _receive(TOMORROW, self.base)
        res = self.save([(self.tonkatsu, self.shinkawa, 11, 12)], d=TOMORROW, upload_id=up.id)
        self.assertEqual(res.status_code, 200)

    def test_not_received_day(self, _):
        res = self.client.get(reverse("item_quantity_edit", args=[TOMORROW.isoformat()]))
        self.assertContains(res, "11/6(金) の持参数 未受信")
        self.assertNotContains(res, 'id="iq-bar"')

    def test_submitted_store_is_marked(self, _):
        DailyReport.objects.create(date=TODAY, location="新川", total_revenue=0)
        res = self.client.get(reverse("item_quantity_edit", args=[TODAY.isoformat()]))
        self.assertEqual([l["submitted"] for l in res.context["grid_data"]["locations"]], [True, False, False])

    # 出店状況：今日分で店合計が 0⇔1以上 をまたいだ時だけ
    @mock.patch("sales.item_quantity_edit.call_command")
    def test_publish_only_when_store_opens_or_closes(self, cmd, _):
        self.save([(self.tonkatsu, self.shinkawa, 10, 12)])
        cmd.assert_not_called()
        res = self.save([(self.tonkatsu, self.ningyo, 0, 6)])  # 人形町が0食＝休み
        cmd.assert_called_once_with("publish_status_json")
        page = self.client.get(reverse("item_quantity_edit", args=[TODAY.isoformat()]))
        self.assertContains(page, "保存 1件・出店状況 更新")
        self.assertEqual(res.status_code, 200)

    @mock.patch("sales.item_quantity_edit.call_command", side_effect=RuntimeError)
    def test_publish_failure_keeps_save(self, cmd, _):
        res = self.save([(self.tonkatsu, self.ningyo, 0, 6)])
        self.assertEqual(res.status_code, 200)
        self.assertEqual(self.iq(self.tonkatsu, self.ningyo).quantity, 0)
        page = self.client.get(reverse("item_quantity_edit", args=[TODAY.isoformat()]))
        self.assertContains(page, "出店状況の更新失敗")

    @mock.patch("sales.item_quantity_edit.call_command")
    def test_tomorrow_does_not_publish(self, cmd, _):
        up = _receive(TOMORROW, self.base)
        self.save([(self.tonkatsu, self.ningyo, 0, 6)], d=TOMORROW, upload_id=up.id)
        cmd.assert_not_called()

    # 11・12
    def test_checks_saved_and_listed(self, _):
        res = self.checks([(self.tonkatsu, self.shinkawa, True), (self.tonkatsu, self.ningyo, True),
                           (self.saba, self.shinkawa, True)])
        self.assertEqual(res.json()["saved"], 3)
        iq = self.iq(self.tonkatsu, self.shinkawa)
        self.assertEqual(iq.checked_by, self.user)
        self.checks([(self.tonkatsu, self.ningyo, False)])
        got = self.client.get(reverse("item_quantity_checks", args=[TODAY.isoformat()])).json()["checked"]
        self.assertEqual(sorted(got), sorted([f"{self.tonkatsu.id}:{self.shinkawa.id}", f"{self.saba.id}:{self.shinkawa.id}"]))
        # 別の端末（別ブラウザ）で開いても✓が出る
        data = self.client.get(reverse("item_quantity_edit", args=[TODAY.isoformat()])).context["grid_data"]
        self.assertTrue(data["cells"][f"{self.tonkatsu.id}:{self.shinkawa.id}"]["ck"])

    def test_zero_cells_are_done_without_check(self, _):
        # 0のマスは✓不要（画面は済み扱いで押せない）。送られても記録しない・送っていないマスに行を作らない
        self.save([(self.tonkatsu, self.ningyo, 0, 6)])
        res = self.checks([(self.tonkatsu, self.ningyo, True), (self.saba, self.ningyo, True)])
        self.assertEqual(res.json()["saved"], 0)
        self.assertIsNone(self.iq(self.tonkatsu, self.ningyo).checked_at)
        self.assertFalse(ItemQuantity.objects.filter(product=self.saba, sales_location=self.ningyo).exists())
        # 1以上に戻したら通常どおり✓できる
        self.save([(self.tonkatsu, self.ningyo, 2, 0)])
        self.assertEqual(self.checks([(self.tonkatsu, self.ningyo, True)]).json()["saved"], 1)

    def test_check_time_kept_when_checked_again(self, _):
        self.checks([(self.tonkatsu, self.shinkawa, True)])
        first = self.iq(self.tonkatsu, self.shinkawa).checked_at
        self.checks([(self.tonkatsu, self.shinkawa, True)])
        self.assertEqual(self.iq(self.tonkatsu, self.shinkawa).checked_at, first)

    def test_bad_json(self, _):
        url = reverse("item_quantity_edit_save", args=[TODAY.isoformat()])
        self.assertEqual(self.client.post(url, "x", content_type="application/json").status_code, 400)
        self.assertEqual(self.client.get(url).status_code, 405)
        self.assertEqual(self.client.get(reverse("item_quantity_edit", args=["2026-13-01"])).status_code, 404)

    # 入口
    def test_list_entry_buttons(self, _):
        _receive(datetime.date(2026, 11, 4), self.base)
        res = self.client.get(reverse("item_quantity_list"))
        self.assertContains(res, "今日の修正")
        self.assertContains(res, reverse("item_quantity_edit", args=["2026-11-05"]))
        self.assertNotContains(res, reverse("item_quantity_edit", args=["2026-11-04"]))
        self.assertNotContains(res, "Actions")

    def test_detail_entry_button(self, _):
        res = self.client.get(reverse("item_quantity_detail", args=["2026-11-05 00:00:00"]))
        self.assertContains(res, reverse("item_quantity_edit", args=["2026-11-05"]))

    def test_old_one_row_edit_removed(self, _):
        with self.assertRaises(NoReverseMatch):
            reverse("item_quantity_update", args=[1])
