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
        self.assertIn("開始日に過去の日付は選べません。過去の値段を直すときは開発部に相談してください。", res.context["errors"])
        res = self.client.post("/prices/confirm/", cell_post())
        self.assertIn("今の価格表と同じ値段です。変えるマスを直してください。", res.context["errors"])
        res = self.client.post("/prices/confirm/", cell_post({("通常", "A"): "0"}))
        self.assertIn("値段は1円以上の整数で入れてください。", res.context["errors"])
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
        self.client.post(f"/prices/{table.pk}/cancel/")
        self.assertFalse(PriceTable.objects.filter(pk=table.pk).exists())
        self.assertIn("取り消し", PriceChangeLog.objects.first().summary)

    def test_started_table_cannot_be_edited(self, _):
        started = PriceTable.objects.get(valid_from="2026-10-01")
        res = self.client.get(f"/prices/{started.pk}/edit/")
        self.assertRedirects(res, "/prices/", fetch_redirect_response=False)
        self.client.post(f"/prices/{started.pk}/cancel/")
        self.assertTrue(PriceTable.objects.filter(pk=started.pk).exists())
