"""クーポン・サービス販売の750円枠（2026年10月〜 価格帯750/700/650）の回帰テスト。"""
import csv
import io

from django.contrib.auth.models import User
from django.contrib.messages.storage.fallback import FallbackStorage
from django.test import TestCase, RequestFactory
from django.utils import timezone

from lunchnetsale.forms import DailyReportForm
from lunchnetsale.views import daily_report_edit_rol, download_csv_allreport
from sales.models import DailyReport, DailyReportEntry


class Coupon750Test(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username='coupon', password='pass', is_staff=True)
        self.report = DailyReport.objects.create(
            date=timezone.now().date(),
            location='テスト売り場',
            total_revenue=0,
            coupon_type_600=1,
            coupon_type_700=2,
            coupon_type_750=3,
            service_type_750=4,
            submitted_by=self.user,
        )
        DailyReportEntry.objects.create(
            report=self.report, product_no=1, product='からあげ弁当',
            quantity=10, sales_quantity=8, remaining_number=2, total_sales=4000,
        )

    def _request(self, method, path, data=None):
        request = getattr(RequestFactory(), method)(path, data or {})
        request.user = self.user
        setattr(request, 'session', {})
        setattr(request, '_messages', FallbackStorage(request))
        return request

    def test_edit_saves_750_fields(self):
        """編集フォームから750円のクーポン枚数・サービス販売個数を保存できること。"""
        form = DailyReportForm(instance=self.report)
        data = {name: '' if form[name].value() is None else str(form[name].value()) for name in form.fields}
        # 割引は割引欄（disc_*）から送り、旧カラムはサーバーが明細から書く（S2）
        data['disc_coupon_750'] = '5'
        data['disc_service_750'] = '6'
        request = self._request('post', f'/daily_report_detail_rol/{self.report.pk}/edit/', data)
        response = daily_report_edit_rol(request, pk=self.report.pk)
        self.assertEqual(response.status_code, 302)
        self.report.refresh_from_db()
        self.assertEqual(self.report.coupon_type_750, 5)
        self.assertEqual(self.report.service_type_750, 6)

    def test_csv_keeps_existing_columns_and_appends_750(self):
        """CSV：既存列の位置は変えず、クーポン750は全行で末尾（ヘッダーと同じ位置）に出ること。"""
        response = download_csv_allreport(self._request('get', '/download_csv_allreport/'))
        rows = list(csv.reader(io.StringIO(response.content.decode('utf-8-sig'))))
        header, row = rows[0], rows[1]

        self.assertEqual(len(header), len(row))
        self.assertEqual(header[-2:], ['クーポン750', 'サービス750'])
        self.assertEqual(row[-2:], ['3', '4'])
        # 既存のクーポン列は同じ位置のまま（見出しだけ 600→650）
        self.assertEqual(header.index('クーポン650'), 23)
        self.assertEqual(header.index('クーポン700'), 24)
        self.assertEqual(row[23], '1')
        self.assertEqual(row[24], '2')
        self.assertNotIn('クーポン600', header)
