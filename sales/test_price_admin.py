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
