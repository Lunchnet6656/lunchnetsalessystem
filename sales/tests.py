from django.test import TestCase, RequestFactory
from django.contrib.auth.models import User
from django.core.exceptions import PermissionDenied
from django.contrib.messages.storage.fallback import FallbackStorage
from django.utils import timezone

from sales.models import DailyReport, DailyReportEntry
from lunchnetsale.forms import DailyReportForm
from lunchnetsale.utils import is_report_owner
from lunchnetsale.views import daily_report_edit_rol


def make_employee(username):
    """非staffの一般従業員を作る（create_user の既定で is_staff=False）。
    submitted_by は AUTH_USER_MODEL = 標準の auth.User を指すため User を使う。"""
    return User.objects.create_user(username=username, password='pass')


class DailyReportIDORTest(TestCase):
    """日計表編集のIDOR（pk書き換えで他人の日計表を編集できる穴）への回帰テスト。"""

    def setUp(self):
        self.alice = make_employee('alice')
        self.bob = make_employee('bob')
        self.report = DailyReport.objects.create(
            date=timezone.now().date(),
            location='テスト売り場',
            total_revenue=0,
            submitted_by=self.alice,
        )

    def _request_edit_rol(self, user, pk):
        """daily_report_edit_rol を RequestFactory で直接呼ぶ（ミドルウェアをバイパス）。"""
        request = RequestFactory().get(f'/daily_report_detail_rol/{pk}/edit/')
        request.user = user
        setattr(request, 'session', {})
        setattr(request, '_messages', FallbackStorage(request))
        return daily_report_edit_rol(request, pk=pk)

    def test_other_employee_cannot_edit(self):
        """他人（bob）がaliceの日計表編集を開こうとすると拒否されること。"""
        with self.assertRaises(PermissionDenied):
            self._request_edit_rol(self.bob, self.report.pk)

    def test_owner_is_recognized(self):
        """所有者判定: aliceは本人と認識され、bobは認識されないこと。"""
        self.assertTrue(is_report_owner(self.alice, self.report))
        self.assertFalse(is_report_owner(self.bob, self.report))


class DailyReportSaveTest(TestCase):
    """日計表編集（従業員用 daily_report_edit_rol）の保存が正しく動くことの回帰テスト。

    スプリント2の C6（重複ビュー統合）・C7（トランザクション化）は日計表の保存処理
    という心臓部に手を入れる。リファクタで保存が壊れた場合にここで検知する安全網。"""

    def setUp(self):
        self.user = make_employee('saver')
        self.report = DailyReport.objects.create(
            date=timezone.now().date(),
            location='テスト売り場',
            total_revenue=0,
            submitted_by=self.user,
        )
        self.entry = DailyReportEntry.objects.create(
            report=self.report,
            product_no=1,
            product='からあげ弁当',
            quantity=10,
            sales_quantity=0,
            remaining_number=10,
            total_sales=0,
        )

    def _post_edit_rol(self, data):
        request = RequestFactory().post(
            f'/daily_report_detail_rol/{self.report.pk}/edit/', data
        )
        request.user = self.user
        setattr(request, 'session', {})
        setattr(request, '_messages', FallbackStorage(request))
        return daily_report_edit_rol(request, pk=self.report.pk)

    def _valid_post(self):
        """DailyReportForm の全フィールドを既存reportの初期値で埋めたPOST辞書を作る。
        フォーム自身の初期値から組み立てるので、モデルにフィールドが増減しても壊れない。"""
        form = DailyReportForm(instance=self.report)
        data = {}
        for name in form.fields:
            value = form[name].value()
            data[name] = '' if value is None else str(value)
        return data

    def test_post_updates_report_and_entry(self):
        """編集POSTで日計表ヘッダと明細の両方が更新されること。"""
        data = self._valid_post()
        # 日計表ヘッダ: 総売上を変更
        data['total_revenue'] = '50000'
        # 明細1件（product_no=1）: 販売数・残数・売上を変更
        data['product_1'] = 'からあげ弁当'
        data['quantity_1'] = '10'
        data['sales_quantity_1'] = '8'
        data['remaining_1'] = '2'
        data['total_sales_1'] = '4000'

        response = self._post_edit_rol(data)

        # 保存成功なら詳細ページへリダイレクト（302）。200は再描画＝保存失敗
        self.assertEqual(response.status_code, 302)
        self.report.refresh_from_db()
        self.entry.refresh_from_db()
        self.assertEqual(self.report.total_revenue, 50000)
        self.assertEqual(self.entry.sales_quantity, 8)
        self.assertEqual(self.entry.remaining_number, 2)
        self.assertEqual(self.entry.total_sales, 4000)
