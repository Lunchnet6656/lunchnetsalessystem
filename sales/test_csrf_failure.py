"""合言葉（CSRF）エラー時の画面。2026-10-16 ログインの二度押しで「セッション期限切れ」が出続けた件。"""
import re

from django.contrib.auth.models import User
from django.test import Client, TestCase

from sales.test_permissions import make_user


def login_page_token(client):
    html = client.get("/login/").content.decode()
    return re.search(r'name="csrfmiddlewaretoken" value="([^"]+)"', html).group(1)


class CsrfFailureTest(TestCase):
    def setUp(self):
        self.client = Client(enforce_csrf_checks=True)
        self.user = make_user("daily", can_view_daily_report_form=True)
        self.user.set_password("Lunch-net-2026")
        self.user.save()

    def test_double_submit_login_goes_in_instead_of_error(self):
        token = login_page_token(self.client)
        data = {"username": "daily", "password": "Lunch-net-2026", "csrfmiddlewaretoken": token}
        first = self.client.post("/login/", data)
        self.assertEqual(first.status_code, 302)
        # 2回目は古い合言葉のまま（1回目のログインで新しくなっている）
        second = self.client.post("/login/", data)
        self.assertRedirects(second, "/dashboard/", fetch_redirect_response=False)

    def test_double_submit_keeps_next(self):
        token = login_page_token(self.client)
        data = {"username": "daily", "password": "Lunch-net-2026", "csrfmiddlewaretoken": token,
                "next": "/daily_report/"}
        self.client.post("/login/", data)
        self.assertRedirects(self.client.post("/login/", data), "/daily_report/", fetch_redirect_response=False)

    def test_stale_login_page_when_logged_out(self):
        res = self.client.post("/login/", {"username": "daily", "password": "x", "csrfmiddlewaretoken": "bad"})
        self.assertEqual(res.status_code, 403)
        self.assertContains(res, "もう一度ログインしてください", status_code=403)
        self.assertContains(res, 'content="3;url=/login/"', status_code=403)

    def test_logged_in_form_does_not_auto_redirect(self):
        self.client.force_login(self.user)
        res = self.client.post("/daily_report/", {"csrfmiddlewaretoken": "bad"})
        self.assertEqual(res.status_code, 403)
        self.assertContains(res, "前の画面に戻って", status_code=403)
        self.assertNotContains(res, "http-equiv", status_code=403)  # 自動で移動すると入力が消える

    def test_stamp_page_does_not_send_customers_to_404(self):
        res = self.client.post("/stamp/me/", {})
        self.assertEqual(res.status_code, 403)
        self.assertContains(res, "LINEのトーク画面のメニュー", status_code=403)
        self.assertNotContains(res, 'url=/"', status_code=403)

    def test_login_button_guard_is_on_page(self):
        self.assertContains(self.client.get("/login/"), "ログインしています…")
