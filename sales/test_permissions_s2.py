"""#188 H10 S2：本体の画面は、サイドバーにリンクが出る権限がないと URL 直打ちでも入れない。"""
from django.test import TestCase

from sales.models import SalesLocation
from sales.test_permissions import make_user

LOCATION_ID = None


def screens(location_id):
    """(URL, 必要な権限) 。権限はサイドバーのリンクと同じ。"""
    return [
        ("/upload/", "can_view_upload"),
        ("/others_item_list/", "can_view_others_item"),
        ("/daily_report_list/", "can_view_daily_report_list"),
        ("/daily_report_list/2026-10-01/", "can_view_daily_report_list"),
        ("/download_csv_allreport/", "can_view_daily_report_list"),
        ("/download_csv/", "can_view_performance_data"),
        ("/products/", "can_view_product_list"),
        ("/products/2026-10-05/", "can_view_product_list"),
        ("/location_list", "can_view_location_list"),
        ("/item_quantity_list/", "can_view_item_quantity"),
        ("/item_quantity_list/2026-10-01%2000:00:00/", "can_view_item_quantity"),
        ("/item_quantity_list/update/999999/", "can_view_item_quantity"),
        ("/performance_data/", "can_view_performance_data"),
        ("/performance_data/2026/10/1/", "can_view_performance_data"),
        ("/performance_by_location/", "can_view_performance_by_location"),
        ("/location-ranking/", "can_view_performance_by_location"),
        ("/meal-forecast/", "can_view_performance_by_location"),
        (f"/location/{location_id}/performance/2026/10/", "can_view_performance_by_location"),
        ("/menu-history/", "can_view_menu_history"),
        ("/menu-ranking/", "can_view_menu_history"),
        ("/sales_dashboard/", "can_view_dashboard"),
    ]


class MainScreensPermissionTest(TestCase):
    def setUp(self):
        self.location = SalesLocation.objects.create(no=1, name="テスト店", type="A", price_type="A")

    def test_without_permission_redirects_to_dashboard(self):
        # 管理者（is_staff）でも、その画面の権限がなければ入れない
        self.client.force_login(make_user("staff_only", staff=True, can_view_daily_report_form=True))
        for url, _ in screens(self.location.pk):
            res = self.client.get(url)
            self.assertRedirects(res, "/dashboard/", fetch_redirect_response=False, msg_prefix=url)

    def test_with_permission_gets_in(self):
        for i, (url, field) in enumerate(screens(self.location.pk)):
            self.client.force_login(make_user(f"user{i}", **{field: True}))
            res = self.client.get(url)
            redirected_to_dashboard = res.status_code == 302 and res.url.startswith("/dashboard")
            self.assertFalse(redirected_to_dashboard, url)
            self.assertNotEqual(res.status_code, 403, url)

    def test_general_staff_calendar_from_general_page(self):
        # 一般向けの販売場所別実績（一般）からリンクされているので、一般の権限でも開ける
        self.client.force_login(make_user("general", can_view_performance_by_location_rol=True))
        res = self.client.get(f"/location/{self.location.pk}/performance/2026/10/")
        self.assertEqual(res.status_code, 200)
        self.assertContains(res, "/performance_by_location_rol/")  # 戻るは一般向けへ

    def test_login_required(self):
        res = self.client.get("/sales_dashboard/")
        self.assertEqual(res.status_code, 302)
        self.assertIn("login", res.url)
