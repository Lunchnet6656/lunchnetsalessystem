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
        'billing_pattern': order.billing_pattern,
        'receipt_memo': order.receipt_memo,
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


class OrderAdjustmentTest(TestCase):
    """受注調整（返品・取消・返金・値引き）の登録・相殺・一覧・サマリー。"""

    def setUp(self):
        from .models import OrderItem
        from django.contrib.auth import get_user_model
        # AUTH_USER_MODEL は素の auth.User（sales.CustomUser とは別）。認証・created_by は本物のUserで。
        User = get_user_model()
        self.user = User.objects.create_user(username='adjuser', password='pass')
        self.client.force_login(self.user)
        self.customer = make_customer()
        self.order = make_order(self.customer)
        # 弁当明細（税込）500円×5＝2,500円
        self.item = OrderItem.objects.create(
            order=self.order, product_name='弁当', quantity_regular=5,
            unit_price=Decimal(500), subtotal=Decimal(2500),
        )
        self.today = timezone.now().date().strftime('%Y-%m-%d')

    def _create_url(self):
        return reverse('orders:adjustment_create', kwargs={'order_pk': self.order.pk})

    def test_default_billing_pattern_is_month_end(self):
        self.assertEqual(self.order.billing_pattern, 'MONTH_END')

    def test_free_amount_adjustment(self):
        from .models import OrderAdjustment
        resp = self.client.post(self._create_url(), {
            'kind': 'DISCOUNT', 'settlement': 'CREDIT', 'occurred_on': self.today,
            'reason': 'お詫び値引き', 'mode': 'free', 'amount': '800',
        })
        self.assertRedirects(resp, reverse('orders:order_detail', kwargs={'pk': self.order.pk}))
        adj = OrderAdjustment.objects.get(order=self.order)
        self.assertEqual(adj.amount, Decimal(800))
        self.assertEqual(self.order.net_total, Decimal(2500) - Decimal(800))
        self.assertEqual(self.order.credit_total, Decimal(800))

    def test_item_mode_computes_amount_from_lines(self):
        from .models import OrderAdjustment
        resp = self.client.post(self._create_url(), {
            'kind': 'RETURN', 'settlement': 'CASH', 'occurred_on': self.today,
            'reason': '2個返品', 'mode': 'items', f'ret_item_{self.item.id}': '2',
        })
        self.assertRedirects(resp, reverse('orders:order_detail', kwargs={'pk': self.order.pk}))
        adj = OrderAdjustment.objects.get(order=self.order)
        self.assertEqual(adj.amount, Decimal(1000))       # 500 × 2
        self.assertEqual(adj.lines.count(), 1)
        self.assertEqual(self.order.cash_refund_total, Decimal(1000))

    def test_item_mode_rejects_qty_over_ordered(self):
        from .models import OrderAdjustment
        resp = self.client.post(self._create_url(), {
            'kind': 'RETURN', 'settlement': 'CREDIT', 'occurred_on': self.today,
            'reason': '過大返品', 'mode': 'items', f'ret_item_{self.item.id}': '9',
        })
        self.assertEqual(resp.status_code, 200)           # 再表示（登録されない）
        self.assertEqual(OrderAdjustment.objects.count(), 0)

    def test_cancel_mode_nets_to_zero(self):
        from .models import OrderAdjustment
        resp = self.client.post(self._create_url(), {
            'kind': 'CANCEL', 'settlement': 'CREDIT', 'occurred_on': self.today,
            'reason': '配達ミスで取消', 'mode': 'cancel',
        })
        self.assertRedirects(resp, reverse('orders:order_detail', kwargs={'pk': self.order.pk}))
        adj = OrderAdjustment.objects.get(order=self.order)
        self.assertEqual(adj.amount, Decimal(2500))
        self.assertEqual(self.order.net_total, Decimal(0))
        # 取消でも受注レコードは残る
        self.assertTrue(Order.objects.filter(pk=self.order.pk).exists())

    def test_free_amount_zero_rejected(self):
        from .models import OrderAdjustment
        resp = self.client.post(self._create_url(), {
            'kind': 'DISCOUNT', 'settlement': 'CREDIT', 'occurred_on': self.today,
            'reason': 'ゼロ', 'mode': 'free', 'amount': '0',
        })
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(OrderAdjustment.objects.count(), 0)

    def test_list_and_csv(self):
        self.client.post(self._create_url(), {
            'kind': 'DISCOUNT', 'settlement': 'CREDIT', 'occurred_on': self.today,
            'reason': '値引き', 'mode': 'free', 'amount': '300',
        })
        resp = self.client.get(reverse('orders:adjustment_list'))
        self.assertEqual(resp.status_code, 200)
        csv_resp = self.client.get(reverse('orders:adjustment_list'), {'export': 'csv'})
        self.assertEqual(csv_resp.status_code, 200)
        self.assertIn('text/csv', csv_resp['Content-Type'])

    def test_monthly_summary(self):
        self.client.post(self._create_url(), {
            'kind': 'RETURN', 'settlement': 'CREDIT', 'occurred_on': self.today,
            'reason': '相殺', 'mode': 'free', 'amount': '400',
        })
        resp = self.client.get(reverse('orders:adjustment_monthly_summary'))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, '400')


class OrderBillingLinkTest(TestCase):
    """顧客の請求書発行タイミングと新規受注フォームの請求パターンの連動。"""

    def setUp(self):
        from django.contrib.auth import get_user_model
        User = get_user_model()
        self.user = User.objects.create_user(username='bluser', password='pass')
        self.client.force_login(self.user)

    def _customer(self, timing):
        return Customer.objects.create(
            customer_type='B2B', company_name='即時商事', price_type='A',
            bento_type='REGULAR', invoice_timing=timing,
        )

    def test_api_customer_info_includes_invoice_timing(self):
        c = self._customer('IMMEDIATE')
        resp = self.client.get(reverse('orders:api_customer_info', kwargs={'pk': c.pk}))
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()['invoice_timing'], 'IMMEDIATE')

    def test_order_create_for_customer_prefills_billing_pattern(self):
        c = self._customer('IMMEDIATE')
        # 請求書払いにしないと請求パターンは表示されないため、請求書支払いに設定
        from .models import PaymentMethod
        c.payment_method = PaymentMethod.objects.create(name='請求書')
        c.save()
        resp = self.client.get(reverse('orders:order_create_for_customer', kwargs={'customer_id': c.pk}))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'value="IMMEDIATE" selected')

    def test_non_invoice_customer_fixes_billing_display(self):
        from .models import PaymentMethod
        pm = PaymentMethod.objects.create(name='現金')
        c = Customer.objects.create(customer_type='B2B', company_name='現金商店',
                                    price_type='A', bento_type='REGULAR', payment_method=pm)
        resp = self.client.get(reverse('orders:order_create_for_customer', kwargs={'customer_id': c.pk}))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, '現金')                 # 支払い方法を固定表示
        self.assertContains(resp, '請求書発行なし')        # 請求パターン指定不要の注記
        self.assertContains(resp, 'outline-none hidden')  # 請求パターンselectは非表示

    def test_invoice_customer_shows_billing_select(self):
        from .models import PaymentMethod
        pm = PaymentMethod.objects.create(name='請求書')
        c = Customer.objects.create(customer_type='B2B', company_name='請求商事',
                                    price_type='A', bento_type='REGULAR',
                                    payment_method=pm, invoice_timing='MONTH_END')
        resp = self.client.get(reverse('orders:order_create_for_customer', kwargs={'customer_id': c.pk}))
        self.assertEqual(resp.status_code, 200)
        # 固定表示divの方がhidden＝請求パターンselectが表示されている
        self.assertContains(resp, 'id="billing_pattern_fixed" class="hidden')
