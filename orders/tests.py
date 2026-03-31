from django.test import TestCase, RequestFactory
from django.urls import reverse
from django.utils import timezone
from django.contrib.messages.storage.fallback import FallbackStorage
from decimal import Decimal
from sales.models import CustomUser
from .models import Customer, Order, OrderExtraItem
from .views import order_edit


def make_user():
    return CustomUser.objects.create_user(
        username='testuser', password='pass', role='staff'
    )


def make_customer():
    return Customer.objects.create(
        customer_type='B2B',
        company_name='テスト会社',
        price_type='A',
        bento_type='REGULAR',
    )


def make_order(customer, notes=''):
    return Order.objects.create(
        customer=customer,
        order_date=timezone.now().date(),
        delivery_date=timezone.now().date(),
        notes=notes,
    )


def make_extra_item(order, name='お茶', price=100, qty=2):
    return OrderExtraItem.objects.create(
        order=order,
        product_name=name,
        unit_price=Decimal(price),
        quantity=qty,
        subtotal=Decimal(price * qty),
    )


def post_order_edit(user, order, data):
    """RequestFactory で order_edit ビューを直接呼び出す（ミドルウェアをバイパス）"""
    factory = RequestFactory()
    request = factory.post(
        reverse('orders:order_edit', kwargs={'pk': order.pk}),
        data=data,
    )
    request.user = user
    # messages フレームワークが要求するセッション・ストレージの最低限設定
    setattr(request, 'session', {})
    messages = FallbackStorage(request)
    setattr(request, '_messages', messages)
    return order_edit(request, pk=order.pk)


def _base_post_data(order):
    """order_edit で最低限必要な POST データ（商品明細なし）"""
    return {
        'customer': str(order.customer.pk),
        'order_date': order.order_date.strftime('%Y-%m-%d'),
        'delivery_date': order.delivery_date.strftime('%Y-%m-%d'),
        'notes': order.notes,
        # items formset management
        'items-TOTAL_FORMS': '0',
        'items-INITIAL_FORMS': '0',
        'items-MIN_NUM_FORMS': '0',
        'items-MAX_NUM_FORMS': '1000',
        # extra_items formset management
        'extra_items-TOTAL_FORMS': '0',
        'extra_items-INITIAL_FORMS': '0',
        'extra_items-MIN_NUM_FORMS': '0',
        'extra_items-MAX_NUM_FORMS': '1000',
    }


class OrderEditNotesTest(TestCase):
    """備考欄の保存テスト"""

    def setUp(self):
        self.user = make_user()
        self.customer = make_customer()

    def test_notes_saved_on_edit(self):
        """受注編集フォームから備考を変更すると保存されること"""
        order = make_order(self.customer, notes='元の備考')

        data = _base_post_data(order)
        data['notes'] = '新しい備考に変更'

        response = post_order_edit(self.user, order, data)
        self.assertEqual(response.status_code, 302)

        order.refresh_from_db()
        self.assertEqual(order.notes, '新しい備考に変更')

    def test_notes_cleared_on_edit(self):
        """備考を空にして保存できること"""
        order = make_order(self.customer, notes='消す備考')

        data = _base_post_data(order)
        data['notes'] = ''

        response = post_order_edit(self.user, order, data)
        self.assertEqual(response.status_code, 302)

        order.refresh_from_db()
        self.assertEqual(order.notes, '')


class OrderExtraItemDeleteTest(TestCase):
    """追加商品削除テスト"""

    def setUp(self):
        self.user = make_user()
        self.customer = make_customer()

    def test_extra_item_deleted_when_delete_flag_sent(self):
        """DELETE=on を送信すると追加商品が削除されること"""
        order = make_order(self.customer)
        ei = make_extra_item(order, name='お茶', price=100, qty=2)
        self.assertEqual(order.extra_items.count(), 1)

        data = _base_post_data(order)
        # 既存1件: DELETE=on で削除指示
        data.update({
            'extra_items-TOTAL_FORMS': '1',
            'extra_items-INITIAL_FORMS': '1',
            'extra_items-0-id': str(ei.pk),
            'extra_items-0-product_name': 'お茶',
            'extra_items-0-unit_price': '100',
            'extra_items-0-quantity': '2',
            'extra_items-0-subtotal': '200',
            'extra_items-0-DELETE': 'on',
        })

        response = post_order_edit(self.user, order, data)
        self.assertEqual(response.status_code, 302)

        order.refresh_from_db()
        self.assertEqual(order.extra_items.count(), 0, '追加商品が削除されていない')

    def test_extra_item_kept_when_no_delete_flag(self):
        """DELETE なしで送信すると追加商品が残ること"""
        order = make_order(self.customer)
        ei = make_extra_item(order, name='お茶', price=100, qty=2)

        data = _base_post_data(order)
        data.update({
            'extra_items-TOTAL_FORMS': '1',
            'extra_items-INITIAL_FORMS': '1',
            'extra_items-0-id': str(ei.pk),
            'extra_items-0-product_name': 'お茶',
            'extra_items-0-unit_price': '100',
            'extra_items-0-quantity': '2',
            'extra_items-0-subtotal': '200',
            'extra_items-0-DELETE': '',
        })

        response = post_order_edit(self.user, order, data)
        self.assertEqual(response.status_code, 302)

        self.assertEqual(order.extra_items.count(), 1, '追加商品が意図せず削除された')

    def test_notes_saved_with_extra_item_delete(self):
        """備考変更と追加商品削除を同時に行っても両方反映されること"""
        order = make_order(self.customer, notes='元の備考')
        ei = make_extra_item(order)

        data = _base_post_data(order)
        data['notes'] = '更新後の備考'
        data.update({
            'extra_items-TOTAL_FORMS': '1',
            'extra_items-INITIAL_FORMS': '1',
            'extra_items-0-id': str(ei.pk),
            'extra_items-0-product_name': 'お茶',
            'extra_items-0-unit_price': '100',
            'extra_items-0-quantity': '2',
            'extra_items-0-subtotal': '200',
            'extra_items-0-DELETE': 'on',
        })

        response = post_order_edit(self.user, order, data)
        self.assertEqual(response.status_code, 302)

        order.refresh_from_db()
        self.assertEqual(order.notes, '更新後の備考', '備考が保存されていない')
        self.assertEqual(order.extra_items.count(), 0, '追加商品が削除されていない')
