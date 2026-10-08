"""S3：価格表・割引設定の画面のテスト。"""
from django.contrib.auth.models import User
from django.test import TestCase

from sales.models import UserMenuPermission


def make_user(username, price_master=False, staff=True):
    user = User.objects.create_user(username=username, password="pass", is_staff=staff)
    perm, _ = UserMenuPermission.objects.get_or_create(user=user)
    perm.can_view_price_master = price_master
    perm.save()
    return user


class PermissionTest(TestCase):
    """S3-1：権限のない人は、URLを直接開いても入れない（サイドバーで隠すだけにしない）。"""

    def test_without_permission_redirects_to_dashboard(self):
        self.client.force_login(make_user("staff_only"))  # staff でも権限OFFなら入れない
        for url in ("/prices/", "/discounts/"):
            res = self.client.get(url)
            self.assertRedirects(res, "/dashboard/", fetch_redirect_response=False)

    def test_with_permission(self):
        self.client.force_login(make_user("honbu", price_master=True))
        for url in ("/prices/", "/discounts/"):
            self.assertEqual(self.client.get(url).status_code, 200)

    def test_login_required(self):
        res = self.client.get("/prices/")
        self.assertEqual(res.status_code, 302)
        self.assertIn("login", res.url)

    def test_new_staff_does_not_get_permission_automatically(self):
        user = User.objects.create_user(username="newstaff", password="pass", is_staff=True)
        self.assertFalse(user.menu_permission.can_view_price_master)


class ChangeLogTest(TestCase):
    """S3-2：変更履歴。"""

    def test_log_and_recent(self):
        from sales.price_admin import log_change, recent_changes, user_label
        user = make_user("honbu2", price_master=True)
        user.last_name, user.first_name = "田中", "花子"
        user.save()
        log_change(user, "price_table", "11/1からの価格表を登録")
        log_change(None, "discount", "ご飯なし ▲100円→▲120円")
        logs = recent_changes("price_table")
        self.assertEqual([l.summary for l in logs], ["11/1からの価格表を登録"])
        self.assertEqual(user_label(logs[0].user), "田中 花子")
        self.assertIsNone(recent_changes("discount")[0].user)
        self.assertEqual(user_label(None), "（不明）")


import datetime
from unittest import mock

from sales.models import PriceChangeLog, PriceRank, PriceTable, SalesLocation

TODAY = datetime.date(2026, 10, 7)  # 水曜


def cell_post(table_overrides=None, valid_from="2026-11-01", note="2026年11月 値上げ"):
    """2026-10-01版（初期データ）の値段に、変えたいマスだけ上書きしたPOST。"""
    base = {r.name: r for r in PriceRank.objects.all()}
    prices = {"特選": (750, 700, 700), "通常": (700, 650, 600), "お手頃": (650, 650, 600), "大盛り": (50, 50, 50)}
    data = {"valid_from": valid_from, "note": note}
    for name, triple in prices.items():
        for pattern, price in zip("ABC", triple):
            data[f"cell_{base[name].id}_{pattern}"] = str(price)
    for (name, pattern), price in (table_overrides or {}).items():
        data[f"cell_{base[name].id}_{pattern}"] = str(price)
    return data


@mock.patch("django.utils.timezone.localdate", return_value=TODAY)
class PriceTableScreenTest(TestCase):
    """S3-3：価格表画面。"""

    def setUp(self):
        self.client.force_login(make_user("honbu", price_master=True))
        for i, pt in enumerate(["A"] * 3 + ["B", "C"]):
            SalesLocation.objects.create(no=i, name=f"売り場{i}", type="移動販売", price_type=pt,
                                         service_name="なし", service_price=0, service_style="なし")

    def test_list_shows_current_table(self, _):
        res = self.client.get("/prices/")
        self.assertContains(res, "今の価格表（2026/10/1から）")
        self.assertContains(res, "3か所")
        self.assertContains(res, "ご飯大盛りの追加料金")

    def test_confirm_sentences(self, _):
        res = self.client.post("/prices/confirm/", cell_post({("通常", "A"): 750, ("特選", "A"): 800}))
        c = res.context["c"]
        self.assertEqual(c["start"], "11/1（日）")
        self.assertEqual(c["until"], "10/31（土）")
        self.assertEqual(c["lines"], ["特選 の 価格A　750円 → 800円（3か所）", "通常 の 価格A　700円 → 750円（3か所）"])
        self.assertEqual(c["unchanged"], 10)
        self.assertEqual(c["coupons"], ["価格Aの販売所　750/700/650円 → 800/750/650円"])
        self.assertTrue(c["mid_week"])  # 11/1は日曜＝週の途中
        self.assertEqual(c["warnings"], [])
        self.assertFalse(PriceTable.objects.filter(valid_from="2026-11-01").exists())  # 確認画面ではまだ保存しない

    def test_big_change_warns_and_needs_check(self, _):
        post = cell_post({("通常", "B"): 6500})
        res = self.client.post("/prices/confirm/", post)
        self.assertEqual(len(res.context["c"]["warnings"]), 1)
        self.assertIn("今の10倍", res.context["c"]["warnings"][0])
        self.client.post("/prices/register/", post)  # チェックなし
        self.assertFalse(PriceTable.objects.filter(valid_from="2026-11-01").exists())
        self.client.post("/prices/register/", {**post, "checked": "1"})
        self.assertTrue(PriceTable.objects.filter(valid_from="2026-11-01").exists())

    def test_register_and_log(self, _):
        res = self.client.post("/prices/register/", cell_post({("通常", "A"): 750}))
        self.assertRedirects(res, "/prices/", fetch_redirect_response=False)
        table = PriceTable.objects.get(valid_from="2026-11-01")
        tsujo = PriceRank.objects.get(name="通常")
        self.assertEqual(table.cells.get(rank=tsujo, pattern="A").price, 750)
        self.assertEqual(table.cells.count(), 12)
        log = PriceChangeLog.objects.get()
        self.assertEqual(log.summary, "11/1（日）からの価格表（通常 の 価格A　700円 → 750円）を登録しました")

    def test_validation_errors(self, _):
        res = self.client.post("/prices/confirm/", cell_post(valid_from="2026-10-06"))
        self.assertIn("開始日に過去の日付は指定できません。過去の値段を変更する場合は開発部に相談してください。", res.context["errors"])
        res = self.client.post("/prices/confirm/", cell_post())
        self.assertIn("今の価格表と同じ値段です。変更するマスを編集してください。", res.context["errors"])
        res = self.client.post("/prices/confirm/", cell_post({("通常", "A"): "0"}))
        self.assertIn("値段は1円以上の整数で入力してください。", res.context["errors"])
        res = self.client.post("/prices/confirm/", cell_post({("通常", "A"): 750}, valid_from="2026-10-01"))
        self.assertTrue(any("過去" in e for e in res.context["errors"]))

    def test_today_needs_check(self, _):
        post = cell_post({("通常", "A"): 750}, valid_from="2026-10-07")
        self.assertTrue(self.client.post("/prices/confirm/", post).context["c"]["is_today"])
        self.client.post("/prices/register/", post)
        self.assertFalse(PriceTable.objects.filter(valid_from="2026-10-07").exists())

    def test_edit_and_cancel_pending(self, _):
        self.client.post("/prices/register/", cell_post({("通常", "A"): 750}))
        table = PriceTable.objects.get(valid_from="2026-11-01")
        self.assertEqual(self.client.get(f"/prices/{table.pk}/edit/").status_code, 200)
        self.client.post("/prices/register/", {**cell_post({("通常", "A"): 760}), "editing": table.pk})
        self.assertEqual(PriceTable.objects.filter(valid_from="2026-11-01").count(), 1)
        self.assertEqual(table.cells.get(rank__name="通常", pattern="A").price, 760)
        self.assertTrue(PriceChangeLog.objects.first().summary.endswith("を更新しました"))   # 編集は「更新」
        self.client.post(f"/prices/{table.pk}/cancel/")
        self.assertFalse(PriceTable.objects.filter(pk=table.pk).exists())
        self.assertEqual(PriceChangeLog.objects.first().summary, "11/1（日）からの価格表を取り消しました")

    def test_started_table_cannot_be_edited(self, _):
        started = PriceTable.objects.get(valid_from="2026-10-01")
        res = self.client.get(f"/prices/{started.pk}/edit/")
        self.assertRedirects(res, "/prices/", fetch_redirect_response=False)
        self.client.post(f"/prices/{started.pk}/cancel/")
        self.assertTrue(PriceTable.objects.filter(pk=started.pk).exists())


from sales.discounts import items_on
from sales.models import DiscountItem


@mock.patch("django.utils.timezone.localdate", return_value=TODAY)
class DiscountScreenTest(TestCase):
    """S3-4：割引設定画面。"""

    def setUp(self):
        self.client.force_login(make_user("honbu", price_master=True))
        self.no_rice = DiscountItem.objects.get(group="rice", label="なし")

    def labels_on(self, day):
        return [(i.label, i.unit_amount) for i in items_on(day) if i.group == "rice"]

    def test_list(self, _):
        res = self.client.get("/discounts/")
        self.assertContains(res, "クーポンとサービスの金額は、ここでは設定しません。")
        self.assertContains(res, "▲100円")

    def test_change_amount(self, _):
        post = {"amount": "120", "valid_from": "2026-11-01"}
        res = self.client.post(f"/discounts/{self.no_rice.pk}/change/", post)
        self.assertContains(res, "11/1（日）から「なし」は ▲100円 → ▲120円 になります。10/31（土）までは ▲100円 のままです。")
        self.assertEqual(DiscountItem.objects.filter(label="なし").count(), 1)  # 確認画面ではまだ保存しない
        self.client.post("/discounts/apply/", {**post, "action": "change", "item": self.no_rice.pk})
        self.assertEqual(self.labels_on(datetime.date(2026, 10, 31))[0], ("なし", -100))
        self.assertEqual(self.labels_on(datetime.date(2026, 11, 1))[0], ("なし", -120))
        new = DiscountItem.objects.get(label="なし", valid_from="2026-11-01")
        self.assertEqual((new.legacy_field, new.csv_label), ("no_rice_quantity", "ご飯なし"))  # CSVの列を引き継ぐ
        self.assertEqual(PriceChangeLog.objects.get().summary, "「なし」の金額変更を登録しました（11/1（日）から ▲100円 → ▲120円）")

    def test_cancel_change_restores(self, _):
        self.client.post("/discounts/apply/", {"amount": "120", "valid_from": "2026-11-01",
                                               "action": "change", "item": self.no_rice.pk})
        new = DiscountItem.objects.get(label="なし", valid_from="2026-11-01")
        self.client.post(f"/discounts/{new.pk}/cancel-plan/")
        self.no_rice.refresh_from_db()
        self.assertIsNone(self.no_rice.valid_to)
        self.assertFalse(DiscountItem.objects.filter(pk=new.pk).exists())

    def test_add_item_appears_from_start_and_in_csv(self, _):
        post = {"group": "rice", "label": "半額", "direction": "minus", "amount": "300", "valid_from": "2026-10-08"}
        res = self.client.post("/discounts/add/", post)
        self.assertContains(res, "日計表送信データ（CSV）の一番後ろに『半額』の列が増えます。")
        self.client.post("/discounts/apply/", {**post, "action": "add"})
        self.assertNotIn(("半額", -300), self.labels_on(datetime.date(2026, 10, 7)))
        self.assertIn(("半額", -300), self.labels_on(datetime.date(2026, 10, 8)))
        # CSV：内訳列の後ろに「半額」列
        import csv as csvmod, io
        from sales.discounts import save_lines
        from sales.daily_report_calc import LineSpec
        from sales.models import DailyReport
        report = DailyReport.objects.create(date=datetime.date(2026, 10, 8), location="広尾", total_revenue=0)
        item = DiscountItem.objects.get(label="半額")
        save_lines(report, [LineSpec("rice", item, "半額", 300, -300, 2)])
        rows = list(csvmod.reader(io.StringIO(self.client.get("/download_csv_allreport/").content.decode("utf-8-sig"))))
        self.assertEqual(rows[0][-1], "半額")
        self.assertEqual(rows[1][-1], "2")

    def test_end_item(self, _):
        self.client.post("/discounts/apply/", {"valid_to": "2026-10-31", "action": "end", "item": self.no_rice.pk})
        self.assertIn(("なし", -100), self.labels_on(datetime.date(2026, 10, 31)))
        self.assertNotIn(("なし", -100), self.labels_on(datetime.date(2026, 11, 1)))

    def test_validation(self, _):
        res = self.client.post(f"/discounts/{self.no_rice.pk}/change/", {"amount": "0", "valid_from": "2026-10-01"})
        self.assertIn("金額は1円以上の整数で入力してください。", res.context["errors"])
        self.assertTrue(any("過去" in e for e in res.context["errors"]))
        res = self.client.post("/discounts/add/", {"group": "rice", "label": "なし", "direction": "minus",
                                                   "amount": "50", "valid_from": "2026-11-01"})
        self.assertIn("『ご飯』にはすでに『なし』があります。別の項目名を入力してください。", res.context["errors"])

    def test_move(self, _):
        extra = DiscountItem.objects.get(group="rice", label="追加")
        self.client.post(f"/discounts/{extra.pk}/move/up/")
        self.assertEqual([i.label for i in items_on(TODAY) if i.group == "rice"], ["追加", "なし"])
