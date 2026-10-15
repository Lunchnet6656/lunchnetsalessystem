"""#188 H10 S1b：ユーザー管理・新規登録の作り直し（画面設計 D9〜D19）。"""
import re
from datetime import timedelta

from axes.models import AccessAttempt
from django.contrib.auth.backends import ModelBackend
from django.contrib.auth.models import User
from django.test import TestCase
from django.utils import timezone

from sales import user_admin as ua
from sales.models import UserChangeLog, UserMenuPermission
from sales.test_permissions import make_user

PASSWORD = "Lunch-net-2026"


class PermissionGroupsTest(TestCase):
    def test_every_permission_is_on_the_edit_screen_once(self):
        fields = [f.name for f in UserMenuPermission._meta.fields if f.name.startswith("can_view_")]
        self.assertCountEqual(ua.PERM_FIELDS, fields)


class Base(TestCase):
    def setUp(self):
        self.honbu = make_user("honbu", staff=True, can_view_user_list=True, can_view_register=True)
        self.honbu.last_name, self.honbu.first_name = "本部", "花子"
        self.honbu.save()
        self.client.force_login(self.honbu)

    def staff(self, username, last="山田", first="太郎", **perms):
        user = make_user(username, **perms)
        user.last_name, user.first_name = last, first
        user.save()
        return User.objects.get(pk=user.pk)  # シグナルが作った権限がキャッシュに残るので読み直す


class ListTest(Base):
    def test_search_and_filters(self):
        yamada = self.staff("yamada", direct_return=False)
        suzuki = self.staff("suzuki", last="鈴木", first="花子")
        UserMenuPermission.objects.filter(user=suzuki).update(direct_return=True)
        User.objects.filter(pk=yamada.pk).update(last_login=timezone.now() - timedelta(days=40))
        User.objects.filter(pk=suzuki.pk).update(last_login=timezone.now())

        res = self.client.get("/users/", {"q": "山田"})
        self.assertContains(res, "yamada")
        self.assertNotContains(res, "suzuki")

        res = self.client.get("/users/", {"filter": "direct"})
        names = [r["user"].username for r in res.context["rows"]]
        self.assertEqual(names, ["suzuki"])

        res = self.client.get("/users/", {"filter": "idle"})
        names = [r["user"].username for r in res.context["rows"]]
        self.assertIn("yamada", names)
        self.assertNotIn("suzuki", names)

    def test_empty_search(self):
        res = self.client.get("/users/", {"q": "存在しない人"})
        self.assertContains(res, "該当するユーザーはいません")
        self.assertContains(res, "条件を解除")

    def test_no_delete_button_and_inactive_section(self):
        gone = self.staff("gone", last="佐藤", first="次郎")
        gone.is_active = False
        gone.save()
        res = self.client.get("/users/")
        self.assertNotContains(res, ">削除<")
        self.assertEqual([r["user"].username for r in res.context["inactive"]], ["gone"])
        self.assertNotIn("gone", [r["user"].username for r in res.context["rows"]])
        self.assertContains(res, "無効のユーザー（1名）")


class RegisterTest(Base):
    def post(self, **extra):
        data = {"last_name": "新人", "first_name": "一郎", "username": "shinjin", "new_password": PASSWORD,
                "confirm_password": PASSWORD, "role": "general", "copy_from": ""}
        data.update(extra)
        return self.client.post("/register/", data)

    def test_standard_permissions(self):
        res = self.post(direct_return="on")
        user = User.objects.get(username="shinjin")
        self.assertRedirects(res, f"/user/edit/{user.pk}/?created=1", fetch_redirect_response=False)
        perm = user.menu_permission
        self.assertTrue(perm.can_view_daily_report_form)
        self.assertTrue(perm.can_view_shift_app)
        self.assertFalse(perm.can_view_user_list)
        self.assertTrue(perm.direct_return)
        self.assertFalse(user.is_staff)
        self.assertTrue(user.check_password(PASSWORD))
        self.assertEqual(UserChangeLog.objects.get(target=user).action, "create")

    def test_copy_from_existing_user_and_admin(self):
        source = self.staff("source", can_view_orders=True, can_view_dashboard=True)
        self.post(role="admin", copy_from=str(source.pk))
        user = User.objects.get(username="shinjin")
        self.assertTrue(user.is_staff)
        for f in ua.PERM_FIELDS:
            self.assertEqual(getattr(user.menu_permission, f), getattr(source.menu_permission, f), f)

    def test_errors_are_japanese_and_keep_input(self):
        self.staff("taken")
        res = self.post(username="taken", new_password="12345678", confirm_password="12345678", shin_yokohama="on")
        self.assertEqual(res.status_code, 200)
        self.assertContains(res, "このユーザー名はすでに使われています")
        self.assertContains(res, "数字だけのパスワードは使えません")
        self.assertContains(res, 'value="新人"')
        self.assertContains(res, 'name="shin_yokohama" class="form-check-input" id="id_shin_yokohama" checked')
        self.assertNotContains(res, 'value="12345678"')  # パスワードは画面に戻さない

    def test_mismatch_and_required(self):
        res = self.post(confirm_password="different-pass-9", last_name="", first_name="")
        self.assertContains(res, "確認のパスワードが一致しません")
        self.assertContains(res, "入力してください", count=2)
        self.assertNotContains(res, "This field is required")
        self.assertFalse(User.objects.filter(username="shinjin").exists())

    def test_created_notice_does_not_show_password(self):
        self.post()
        user = User.objects.get(username="shinjin")
        res = self.client.get(f"/user/edit/{user.pk}/?created=1")
        self.assertContains(res, "本人に伝えること")
        self.assertContains(res, "/login/")
        self.assertNotContains(res, PASSWORD)


class EditTest(Base):
    def setUp(self):
        super().setUp()
        self.target = self.staff("yamada", can_view_daily_report_form=True)

    def form_data(self, **extra):
        perm = self.target.menu_permission
        data = {"last_name": self.target.last_name, "first_name": self.target.first_name,
                "username": self.target.username, "role": "general"}
        data.update({f: "on" for f in ua.PERM_FIELDS if getattr(perm, f)})
        data.update(extra)
        return data

    def test_permission_change_is_logged_one_line_each(self):
        res = self.client.post(f"/user/edit/{self.target.pk}/", self.form_data(can_view_orders="on", shin_yokohama="on"))
        self.assertRedirects(res, f"/user/edit/{self.target.pk}/", fetch_redirect_response=False)
        details = list(UserChangeLog.objects.filter(target=self.target).values_list("detail", flat=True))
        self.assertCountEqual(details, ["受注管理をON", "新横浜担当をON"])
        self.assertEqual(UserChangeLog.objects.first().changed_by, self.honbu)
        res = self.client.get(f"/user/edit/{self.target.pk}/")
        self.assertContains(res, "受注管理をON")

    def test_labels_are_linked_and_in_sidebar_order(self):
        res = self.client.get(f"/user/edit/{self.target.pk}/")
        self.assertContains(res, 'for="id_can_view_orders"')
        html = res.content.decode()
        self.assertLess(html.index("id_can_view_mypage"), html.index("id_can_view_user_list"))
        self.assertLess(html.index("id_can_view_user_list"), html.index("id_can_view_price_master"))
        self.assertLess(html.index("id_can_view_dashboard"), html.index("id_can_view_daily_report_list"))

    def test_self_permissions_are_disabled(self):
        html = self.client.get(f"/user/edit/{self.honbu.pk}/").content.decode()
        tag = re.search(r"<input[^>]*id=\"id_can_view_user_list\"[^>]*>", html).group(0)
        self.assertIn("disabled", tag)
        self.assertIn("checked", tag)

    def test_password_set_without_current(self):
        res = self.client.post(f"/user/{self.target.pk}/password/",
                               {"new_password": PASSWORD, "confirm_password": PASSWORD})
        self.assertRedirects(res, f"/user/edit/{self.target.pk}/", fetch_redirect_response=False)
        self.target.refresh_from_db()
        self.assertTrue(self.target.check_password(PASSWORD))
        self.assertIsNotNone(ModelBackend().authenticate(None, username="yamada", password=PASSWORD))

    def test_short_password_is_rejected(self):
        res = self.client.post(f"/user/{self.target.pk}/password/", {"new_password": "ab1", "confirm_password": "ab1"})
        self.assertEqual(res.status_code, 400)
        self.assertContains(res, "8文字以上にしてください", status_code=400)
        self.target.refresh_from_db()
        self.assertFalse(self.target.check_password("ab1"))

    def test_deactivate_and_activate(self):
        self.client.post(f"/user/{self.target.pk}/deactivate/")
        self.target.refresh_from_db()
        self.assertFalse(self.target.is_active)
        self.assertIsNone(ModelBackend().authenticate(None, username="yamada", password="pass-1234-abcd"))
        res = self.client.post(f"/user/{self.target.pk}/activate/", {"next": "list"})
        self.assertRedirects(res, "/users/", fetch_redirect_response=False)
        self.target.refresh_from_db()
        self.assertTrue(self.target.is_active)
        actions = list(UserChangeLog.objects.filter(target=self.target).values_list("action", flat=True))
        self.assertEqual(actions, ["activate", "deactivate"])

    def test_inactive_user_logged_out_on_next_request(self):
        self.client.force_login(self.target)
        User.objects.filter(pk=self.target.pk).update(is_active=False)
        res = self.client.get("/dashboard/")
        self.assertEqual(res.status_code, 302)
        self.assertIn("login", res.url)

    def test_inactive_user_hidden_from_shin_yokohama_candidates(self):
        UserMenuPermission.objects.filter(user=self.target).update(shin_yokohama=True)
        self.assertEqual(len(self.client.get("/api/shin_yokohama_users/").json()), 1)
        User.objects.filter(pk=self.target.pk).update(is_active=False)
        self.assertEqual(self.client.get("/api/shin_yokohama_users/").json(), [])

    def test_lock_and_unlock(self):
        AccessAttempt.objects.create(username="yamada", ip_address="10.0.0.1", user_agent="x", http_accept="x",
                                     path_info="/login/", get_data="", post_data="", failures_since_start=5)
        self.assertTrue(ua.is_locked(self.target))
        res = self.client.get("/users/", {"filter": "locked"})
        self.assertEqual([r["user"].username for r in res.context["rows"]], ["yamada"])
        self.assertContains(self.client.get(f"/user/edit/{self.target.pk}/"), "ロック解除")
        self.client.post(f"/user/{self.target.pk}/unlock/")
        self.assertFalse(AccessAttempt.objects.exists())
        self.assertFalse(ua.is_locked(self.target))
        self.assertEqual(UserChangeLog.objects.get(target=self.target).action, "unlock")

    def test_old_lock_is_not_shown(self):
        attempt = AccessAttempt.objects.create(username="yamada", ip_address="10.0.0.1", user_agent="x", http_accept="x",
                                               path_info="/login/", get_data="", post_data="", failures_since_start=5)
        AccessAttempt.objects.filter(pk=attempt.pk).update(attempt_time=timezone.now() - timedelta(hours=2))
        self.assertFalse(ua.is_locked(self.target))

    def test_duplicate_username_on_edit(self):
        self.staff("suzuki", last="鈴木")
        res = self.client.post(f"/user/edit/{self.target.pk}/", self.form_data(username="SUZUKI"))
        self.assertContains(res, "このユーザー名はすでに使われています")
