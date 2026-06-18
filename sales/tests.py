from django.test import TestCase, RequestFactory
from django.contrib.auth.models import User
from django.core.exceptions import PermissionDenied
from django.contrib.messages.storage.fallback import FallbackStorage
from django.utils import timezone

from sales.models import DailyReport, DailyReportEntry, OthersItem, SalesLocation, ItemQuantity, Product
from lunchnetsale.forms import DailyReportForm
from lunchnetsale.utils import is_report_owner
from lunchnetsale.views import daily_report_edit_rol, others_list_view, location_list_view


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


class OthersItemSaveTest(TestCase):
    """その他項目設定（others_list_view）の「上書き保存」回帰テスト。

    リファクタ前は update_or_create がforループの外にあり、複数行を保存しても
    最後の1行しか保存されない不具合があった。全行が保存されることを保証する。"""

    def setUp(self):
        self.user = make_employee('others_admin')

    def _post(self, data):
        request = RequestFactory().post('/others_item_list/', data)
        request.user = self.user
        setattr(request, 'session', {})
        setattr(request, '_messages', FallbackStorage(request))
        return others_list_view(request)

    def test_overwrite_saves_all_rows(self):
        """複数行を送信すると全行が保存されること（旧バグ: 最後の1行のみ）。"""
        data = {
            'others[0][no]': '1', 'others[0][name]': 'レジ袋',   'others[0][price]': '5',
            'others[1][no]': '2', 'others[1][name]': '保冷剤',   'others[1][price]': '20',
            'others[2][no]': '3', 'others[2][name]': '割り箸',   'others[2][price]': '0',
            'csrfmiddlewaretoken': 'dummy',  # 実POSTに混ざるキーを無視できるか確認
        }
        response = self._post(data)
        self.assertEqual(response.status_code, 302)
        self.assertEqual(OthersItem.objects.count(), 3)
        self.assertEqual(OthersItem.objects.get(no=1).name, 'レジ袋')
        self.assertEqual(OthersItem.objects.get(no=2).name, '保冷剤')
        self.assertEqual(int(OthersItem.objects.get(no=2).price), 20)

    def test_empty_rows_are_skipped(self):
        """NO・商品名が両方空の行は保存されないこと。"""
        data = {
            'others[0][no]': '1', 'others[0][name]': 'レジ袋', 'others[0][price]': '5',
            'others[1][no]': '',  'others[1][name]': '',       'others[1][price]': '',
        }
        self._post(data)
        self.assertEqual(OthersItem.objects.count(), 1)

    def test_existing_row_is_updated_by_id_not_duplicated(self):
        """id付きで送れば新規作成ではなく該当レコードが更新されること。"""
        obj = OthersItem.objects.create(no=1, name='旧名称', price=5)
        data = {
            'others[0][id]': str(obj.id),
            'others[0][no]': '1', 'others[0][name]': '新名称', 'others[0][price]': '8',
        }
        self._post(data)
        self.assertEqual(OthersItem.objects.count(), 1)
        obj.refresh_from_db()
        self.assertEqual(obj.name, '新名称')

    def test_reorder_swaps_no_but_keeps_record_identity(self):
        """並べ替え（NO入れ替え）でも各レコードのidは保たれ、中身が混ざらないこと。

        旧実装（NOキー保存）では no=1 の行を保存すると別レコードを上書きし、
        中身がぐちゃぐちゃになっていた。id基準なら同一レコードのNOだけが変わる。"""
        a = OthersItem.objects.create(no=1, name='Aアイテム', price=10)
        b = OthersItem.objects.create(no=2, name='Bアイテム', price=20)
        # 画面でA↔Bを並べ替え → JSがNOを振り直し、idはそのまま送られる
        data = {
            'others[0][id]': str(b.id), 'others[0][no]': '1', 'others[0][name]': 'Bアイテム', 'others[0][price]': '20',
            'others[1][id]': str(a.id), 'others[1][no]': '2', 'others[1][name]': 'Aアイテム', 'others[1][price]': '10',
        }
        self._post(data)
        self.assertEqual(OthersItem.objects.count(), 2)
        a.refresh_from_db(); b.refresh_from_db()
        # idは不変、NOだけ入れ替わり、名前は各レコードに正しく残る
        self.assertEqual(a.name, 'Aアイテム')
        self.assertEqual(a.no, 2)
        self.assertEqual(b.name, 'Bアイテム')
        self.assertEqual(b.no, 1)

    def test_no_input_with_name_is_warned_not_500(self):
        """NO未入力＋商品名ありの行は500にならずスキップされること。"""
        data = {'others[0][id]': '', 'others[0][no]': '', 'others[0][name]': '名前だけ', 'others[0][price]': '5'}
        response = self._post(data)
        self.assertEqual(response.status_code, 302)
        self.assertEqual(OthersItem.objects.count(), 0)


class SalesLocationSaveTest(TestCase):
    """販売場所リスト（location_list_view）の「上書き保存」回帰テスト。

    リファクタ前は while ループで「NOが空の行が来たら break」しており、途中行の
    NOを空にすると以降の行が保存されない静かな欠落があった。全行が保存されることを保証。"""

    def setUp(self):
        self.user = make_employee('loc_admin')

    def _row(self, i, no, name):
        return {
            f'location[{i}][no]': str(no),
            f'location[{i}][name]': name,
            f'location[{i}][type]': '常設',
            f'location[{i}][price_type]': 'A',
            f'location[{i}][service_name]': '',
            f'location[{i}][service_price]': '0',
            f'location[{i}][service_style]': 'なし',
            f'location[{i}][direct_return]': '0',
        }

    def _post(self, data):
        request = RequestFactory().post('/location_list', data)
        request.user = self.user
        setattr(request, 'session', {})
        setattr(request, '_messages', FallbackStorage(request))
        return location_list_view(request)

    def test_overwrite_saves_all_rows(self):
        """複数行を送信すると全行が保存されること。"""
        data = {'csrfmiddlewaretoken': 'dummy'}
        data.update(self._row(0, 1, 'A拠点'))
        data.update(self._row(1, 2, 'B拠点'))
        data.update(self._row(2, 3, 'C拠点'))
        response = self._post(data)
        self.assertEqual(response.status_code, 302)
        self.assertEqual(SalesLocation.objects.count(), 3)
        self.assertEqual(SalesLocation.objects.get(no=2).name, 'B拠点')

    def test_empty_gap_row_does_not_drop_later_rows(self):
        """途中に空行があっても後続の行が保存されること（旧バグ: breakで脱落）。"""
        data = {}
        data.update(self._row(0, 1, 'A拠点'))
        # index 1 は完全な空行（NO・名前なし）
        data.update({
            'location[1][no]': '', 'location[1][name]': '',
            'location[1][type]': '', 'location[1][price_type]': '',
            'location[1][service_name]': '', 'location[1][service_price]': '',
            'location[1][service_style]': 'なし', 'location[1][direct_return]': '',
        })
        data.update(self._row(2, 3, 'C拠点'))
        self._post(data)
        # 空行はスキップ、A拠点とC拠点の2件が残る
        self.assertEqual(SalesLocation.objects.count(), 2)
        self.assertTrue(SalesLocation.objects.filter(no=3, name='C拠点').exists())

    def test_digital_payment_checkbox(self):
        """電子決済チェックの有無が正しく反映されること。"""
        data = self._row(0, 1, 'A拠点')
        data['location[0][accepts_digital_payment]'] = '1'
        self._post(data)
        self.assertTrue(SalesLocation.objects.get(no=1).accepts_digital_payment)

    def test_negative_service_price_is_saved(self):
        """サービス価格は割引でマイナス（例 -100）になり得るため、負の値も保存できること。"""
        data = self._row(0, 1, 'A拠点')
        data['location[0][service_price]'] = '-100'
        self._post(data)
        self.assertEqual(int(SalesLocation.objects.get(no=1).service_price), -100)

    def test_existing_row_updated_by_id(self):
        """id付きで送ると該当レコードが更新され、重複しないこと。"""
        loc = SalesLocation.objects.create(no=1, name='旧名', type='常設')
        data = self._row(0, 1, '新名')
        data['location[0][id]'] = str(loc.id)
        self._post(data)
        self.assertEqual(SalesLocation.objects.count(), 1)
        loc.refresh_from_db()
        self.assertEqual(loc.name, '新名')

    def test_reorder_keeps_itemquantity_association(self):
        """並べ替え（NO入れ替え）後も、持参数(ItemQuantity)が正しい拠点に紐づき続けること。

        SalesLocation を id でFK参照する ItemQuantity が、別拠点に化けない保証。
        旧実装（NOキー保存）ではここが壊れていた。"""
        a = SalesLocation.objects.create(no=1, name='A拠点', type='常設')
        b = SalesLocation.objects.create(no=2, name='B拠点', type='常設')
        product = Product.objects.create(no=1, week='20260101', name='からあげ弁当')
        iq = ItemQuantity.objects.create(
            target_date='20260101', target_week='20260101',
            product=product, sales_location=a, quantity=30,
        )

        # 画面でA↔Bを並べ替え → JSがNO振り直し、idはそのまま送られる
        data = {}
        row_b = self._row(0, 1, 'B拠点'); row_b['location[0][id]'] = str(b.id)
        row_a = self._row(1, 2, 'A拠点'); row_a['location[1][id]'] = str(a.id)
        data.update(row_b); data.update(row_a)
        self._post(data)

        a.refresh_from_db(); b.refresh_from_db(); iq.refresh_from_db()
        # NOだけ入れ替わり、レコード identity（名前・id）は不変
        self.assertEqual(a.name, 'A拠点'); self.assertEqual(a.no, 2)
        self.assertEqual(b.name, 'B拠点'); self.assertEqual(b.no, 1)
        # 持参数は依然としてA拠点（同一id）に紐づいている
        self.assertEqual(iq.sales_location_id, a.id)
        self.assertEqual(iq.sales_location.name, 'A拠点')
