from django.test import TestCase, RequestFactory
from django.contrib.auth.models import User
from django.core.exceptions import PermissionDenied
from django.contrib.messages.storage.fallback import FallbackStorage
from django.utils import timezone

from sales.models import DailyReport
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
