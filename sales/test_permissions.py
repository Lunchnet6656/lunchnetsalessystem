"""#188 H10 S1a：ユーザー管理と単発の穴を URL直打ち・POST直打ちでも塞ぐ。"""
import datetime
import json

from django.contrib.auth.models import User
from django.test import TestCase

from sales.models import DailyReport, Product, UserMenuPermission

PERM_FIELDS = [f.name for f in UserMenuPermission._meta.fields if f.name.startswith("can_view_")]


def make_user(username, staff=False, superuser=False, **perms):
    user = User.objects.create_user(username=username, password="pass-1234-abcd",
                                    is_staff=staff, is_superuser=superuser)
    perm, _ = UserMenuPermission.objects.get_or_create(user=user)
    for field in PERM_FIELDS:
        setattr(perm, field, perms.get(field, False))
    perm.save()
    return user


def edit_post(user, **extra):
    """編集画面の POST。全部の権限を ON にして管理者にもする（乗っ取りの形）。"""
    data = {"username": user.username, "first_name": "太郎", "last_name": "山田", "is_staff": "on"}
    data.update({field: "on" for field in PERM_FIELDS})
    data.update(extra)
    return data


class UserManagementPermissionTest(TestCase):
    def setUp(self):
        self.general = make_user("general", can_view_daily_report_form=True)
        # 権限のチェックだけ ON にした一般スタッフ（is_staff ではない）
        self.flag_only = make_user("flag_only", can_view_user_list=True, can_view_register=True)
        self.honbu = make_user("honbu", staff=True, can_view_user_list=True, can_view_register=True)
        self.staff_no_flag = make_user("staff_no_flag", staff=True)
        self.owner = make_user("owner", staff=True, superuser=True, can_view_user_list=True)

    def test_general_staff_cannot_open_user_screens(self):
        for user in (self.general, self.flag_only, self.staff_no_flag):
            self.client.force_login(user)
            for url in ("/users/", f"/user/edit/{user.pk}/", f"/user/delete/{self.general.pk}/", "/register/"):
                res = self.client.get(url)
                self.assertRedirects(res, "/dashboard/", fetch_redirect_response=False, msg_prefix=f"{user} {url}")

    def test_general_staff_cannot_make_self_admin(self):
        self.client.force_login(self.general)
        self.client.post(f"/user/edit/{self.general.pk}/", edit_post(self.general))
        self.general.refresh_from_db()
        self.assertFalse(self.general.is_staff)
        self.assertFalse(self.general.menu_permission.can_view_user_list)

    def test_general_staff_cannot_register_admin(self):
        self.client.force_login(self.flag_only)
        self.client.post("/register/", {"username": "evil", "password1": "Xy7-long-pass", "password2": "Xy7-long-pass",
                                        "user_role": "admin", "first_name": "a", "last_name": "b"})
        self.assertFalse(User.objects.filter(username="evil").exists())

    def test_general_staff_cannot_delete_or_toggle(self):
        self.client.force_login(self.general)
        self.client.post(f"/user/delete/{self.honbu.pk}/")
        self.assertTrue(User.objects.filter(pk=self.honbu.pk).exists())
        res = self.client.post(f"/user/toggle_direct_return/{self.honbu.pk}/", {"checked": "true"})
        self.assertEqual(res.status_code, 403)
        self.assertFalse(UserMenuPermission.objects.get(user=self.honbu).direct_return)

    def test_honbu_can_manage_others(self):
        self.client.force_login(self.honbu)
        self.assertEqual(self.client.get("/users/").status_code, 200)
        self.assertEqual(self.client.get("/register/").status_code, 200)
        res = self.client.post(f"/user/edit/{self.general.pk}/", edit_post(self.general))
        self.assertRedirects(res, "/users/", fetch_redirect_response=False)
        self.general.refresh_from_db()
        self.assertTrue(self.general.is_staff)  # D4：本部は他の人を管理者にできる
        self.assertTrue(self.general.menu_permission.can_view_orders)

    def test_honbu_cannot_change_own_permissions(self):
        self.client.force_login(self.honbu)
        res = self.client.get(f"/user/edit/{self.honbu.pk}/")
        self.assertContains(res, "自分の管理者権限・サイドバー表示設定は変更できません")
        data = edit_post(self.honbu, last_name="本部")
        data.pop("is_staff")  # 管理者を外そうとしても変わらない
        data.pop("can_view_user_list")
        self.client.post(f"/user/edit/{self.honbu.pk}/", data)
        self.honbu.refresh_from_db()
        perm = self.honbu.menu_permission
        self.assertEqual(self.honbu.last_name, "本部")  # 名前は変えられる
        self.assertTrue(self.honbu.is_staff)
        self.assertTrue(perm.can_view_user_list)
        self.assertFalse(perm.can_view_orders)  # 広げることもできない

    def test_superuser_account_is_only_editable_by_owner(self):
        self.client.force_login(self.honbu)
        self.assertRedirects(self.client.get(f"/user/edit/{self.owner.pk}/"), "/users/", fetch_redirect_response=False)
        self.client.post(f"/user/edit/{self.owner.pk}/", edit_post(self.owner, last_name="変更"))
        res = self.client.post(f"/user/delete/{self.owner.pk}/")
        self.assertEqual(res.status_code, 403)
        self.owner.refresh_from_db()
        self.assertNotEqual(self.owner.last_name, "変更")
        self.client.force_login(self.owner)
        self.assertEqual(self.client.get(f"/user/edit/{self.owner.pk}/").status_code, 200)

    def test_cannot_delete_self(self):
        self.client.force_login(self.honbu)
        res = self.client.post(f"/user/delete/{self.honbu.pk}/")
        self.assertEqual(res.status_code, 403)
        self.assertTrue(User.objects.filter(pk=self.honbu.pk).exists())

    def test_login_required(self):
        res = self.client.get("/users/")
        self.assertEqual(res.status_code, 302)
        self.assertIn("login", res.url)


class SingleHoleTest(TestCase):
    def setUp(self):
        self.report = DailyReport.objects.create(date=datetime.date(2026, 10, 1), location="テスト店", total_revenue=0)
        self.week = datetime.date(2026, 10, 5)
        Product.objects.create(no=1, week=self.week, name="唐揚げ弁当", price_A=650, price_B=650, price_C=650)

    def post_confirm(self):
        return self.client.post(f"/update_confirmation/{self.report.pk}/", json.dumps({"confirmed": True}),
                                 content_type="application/json")

    def test_confirmation_requires_login(self):
        res = self.post_confirm()
        self.assertEqual(res.status_code, 403)
        self.report.refresh_from_db()
        self.assertFalse(self.report.confirmed)

    def test_confirmation_requires_permission(self):
        self.client.force_login(make_user("general", can_view_daily_report_form=True))
        self.assertEqual(self.post_confirm().status_code, 403)
        self.client.force_login(make_user("checker", staff=True, can_view_daily_report_list=True))
        self.assertEqual(self.post_confirm().status_code, 200)
        self.report.refresh_from_db()
        self.assertTrue(self.report.confirmed)

    def test_product_delete_needs_post_and_permission(self):
        url = f"/products/delete/{self.week.isoformat()}/"
        self.client.force_login(make_user("editor", can_view_product_list=True))
        self.assertEqual(self.client.get(url).status_code, 405)
        self.assertTrue(Product.objects.filter(week=self.week).exists())
        self.client.force_login(make_user("general", can_view_daily_report_form=True))
        self.client.post(url)
        self.assertTrue(Product.objects.filter(week=self.week).exists())
        self.client.force_login(User.objects.get(username="editor"))
        self.assertRedirects(self.client.post(url), "/products/", fetch_redirect_response=False)
        self.assertFalse(Product.objects.filter(week=self.week).exists())
