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


class InvoiceTest(TestCase):
    """請求書発行(v2)：集計・日別明細・控除・ロック・繰越・VOID・漏れ検知。"""

    def setUp(self):
        from .models import OrderItem, PaymentMethod
        from django.contrib.auth import get_user_model
        from datetime import date
        User = get_user_model()
        self.user = User.objects.create_user(username='invuser', password='pass')
        self.client.force_login(self.user)
        pm = PaymentMethod.objects.create(name='請求書')
        self.customer = Customer.objects.create(
            customer_type='B2B', company_name='請求テスト社', price_type='A',
            bento_type='REGULAR', payment_method=pm, invoice_timing='MONTH_END',
        )
        today = timezone.now().date()
        self.d1 = date(today.year, today.month, 10)
        self.d2 = date(today.year, today.month, 11)
        self.month_str = today.strftime('%Y-%m')

    def _order(self, delivery_date, billing='MONTH_END'):
        return Order.objects.create(
            customer=self.customer, order_date=delivery_date,
            delivery_date=delivery_date, billing_pattern=billing,
        )

    def _item(self, order, name, price, qty):
        from .models import OrderItem
        return OrderItem.objects.create(
            order=order, product_name=name, quantity_regular=qty,
            unit_price=Decimal(price), subtotal=Decimal(price * qty),
        )

    def _credit(self, order, amount, occurred=None):
        from .models import OrderAdjustment
        return OrderAdjustment.objects.create(
            order=order, kind='RETURN', settlement='CREDIT',
            amount=Decimal(amount), reason='返品', occurred_on=occurred or self.d1,
        )

    def test_issue_aggregates_and_locks(self):
        from . import services
        from .models import Invoice
        o1 = self._order(self.d1); self._item(o1, '弁当', 500, 5)   # 2500
        o2 = self._order(self.d2); self._item(o2, '弁当', 600, 3)   # 1800
        inv = services.issue_invoice(
            self.customer, [o1.pk, o2.pk], pattern=Invoice.PATTERN_MONTH_END,
            issued_by=self.user,
        )
        self.assertEqual(int(inv.charge_total), 4300)
        self.assertEqual(int(inv.net_amount), 4300)
        # 日別明細が受注明細ぶん展開されている（2行）
        self.assertEqual(inv.lines.filter(source_type='ORDER').count(), 2)
        # ロック：受注に invoice が張られ、未請求クエリから消える
        o1.refresh_from_db(); o2.refresh_from_db()
        self.assertEqual(o1.invoice_id, inv.pk)
        self.assertTrue(o1.is_invoiced)
        self.assertFalse(Order.objects.uninvoiced().filter(pk=o1.pk).exists())

    def test_daily_line_has_qty_and_unit_price(self):
        from . import services
        o1 = self._order(self.d1); self._item(o1, '幕の内', 500, 5)
        inv = services.issue_invoice(self.customer, [o1.pk], issued_by=self.user)
        line = inv.lines.get(source_type='ORDER')
        self.assertEqual(line.quantity, 5)
        self.assertEqual(int(line.unit_price), 500)
        self.assertEqual(int(line.amount), 2500)
        self.assertEqual(line.line_date, self.d1)

    def test_catering_qty_zero_amount_only(self):
        from . import services
        from .models import OrderItem
        o1 = self._order(self.d1)
        # 仕出し：数量0で小計直接
        OrderItem.objects.create(order=o1, product_name='仕出し', quantity_regular=0,
                                 unit_price=Decimal(0), subtotal=Decimal(8000))
        inv = services.issue_invoice(self.customer, [o1.pk], issued_by=self.user)
        line = inv.lines.get(source_type='ORDER')
        self.assertIsNone(line.quantity)
        self.assertIsNone(line.unit_price)
        self.assertEqual(int(line.amount), 8000)

    def test_credit_deducted_and_locked(self):
        from . import services
        o1 = self._order(self.d1); self._item(o1, '弁当', 500, 5)  # 2500
        adj = self._credit(o1, 500)
        inv = services.issue_invoice(self.customer, [o1.pk], issued_by=self.user)
        self.assertEqual(int(inv.charge_total), 2500)
        self.assertEqual(int(inv.credit_total), 500)
        self.assertEqual(int(inv.net_amount), 2000)
        # 控除行はマイナス
        cline = inv.lines.get(source_type='ADJUSTMENT')
        self.assertEqual(int(cline.amount), -500)
        # 調整がロックされる
        adj.refresh_from_db()
        self.assertEqual(adj.billed_invoice_id, inv.pk)

    def test_cash_refund_not_on_invoice(self):
        from . import services
        from .models import OrderAdjustment
        o1 = self._order(self.d1); self._item(o1, '弁当', 500, 5)
        cash = OrderAdjustment.objects.create(
            order=o1, kind='RETURN', settlement='CASH', amount=Decimal(500),
            reason='現金返金', occurred_on=self.d1,
        )
        inv = services.issue_invoice(self.customer, [o1.pk], issued_by=self.user)
        self.assertEqual(int(inv.credit_total), 0)
        self.assertEqual(int(inv.net_amount), 2500)
        self.assertEqual(inv.lines.filter(source_type='ADJUSTMENT').count(), 0)
        cash.refresh_from_db()
        self.assertIsNone(cash.billed_invoice_id)

    def test_double_invoice_prevented(self):
        from . import services
        o1 = self._order(self.d1); self._item(o1, '弁当', 500, 5)
        services.issue_invoice(self.customer, [o1.pk], issued_by=self.user)
        with self.assertRaises(services.InvoiceError):
            services.issue_invoice(self.customer, [o1.pk], issued_by=self.user)

    def test_void_unlocks(self):
        from . import services
        o1 = self._order(self.d1); self._item(o1, '弁当', 500, 5)
        adj = self._credit(o1, 300)
        inv = services.issue_invoice(self.customer, [o1.pk], issued_by=self.user)
        services.void_invoice(inv, reason='金額誤り', user=self.user)
        inv.refresh_from_db(); o1.refresh_from_db(); adj.refresh_from_db()
        self.assertEqual(inv.status, 'VOID')
        self.assertIsNone(o1.invoice_id)
        self.assertIsNone(adj.billed_invoice_id)
        # 再び未請求として選べる
        self.assertTrue(Order.objects.uninvoiced().filter(pk=o1.pk).exists())

    def test_carryover_credit_to_next_invoice(self):
        from . import services
        # 受注A を請求済みにする
        oa = self._order(self.d1); self._item(oa, '弁当', 500, 5)
        inv1 = services.issue_invoice(self.customer, [oa.pk], issued_by=self.user)
        # 請求後にクレーム発生（CREDIT）→ 繰越候補
        late = self._credit(oa, 500, occurred=self.d2)
        carry = services.carryover_credits_for_customer(self.customer)
        self.assertIn(late, carry)
        # 受注B を新規発行し、繰越を取込む
        ob = self._order(self.d2); self._item(ob, '弁当', 600, 3)  # 1800
        inv2 = services.issue_invoice(
            self.customer, [ob.pk], [late.pk], issued_by=self.user,
        )
        self.assertEqual(int(inv2.credit_total), 500)
        self.assertEqual(int(inv2.net_amount), 1300)   # 1800 - 500
        late.refresh_from_db()
        self.assertEqual(late.billed_invoice_id, inv2.pk)
        # 過去の請求書(inv1)は不変
        inv1.refresh_from_db()
        self.assertEqual(int(inv1.net_amount), 2500)

    def test_pending_immediate_detection(self):
        from . import services
        o1 = self._order(self.d1, billing='IMMEDIATE'); self._item(o1, '弁当', 500, 2)
        self.assertTrue(Order.objects.pending_immediate().filter(pk=o1.pk).exists())
        services.issue_invoice(self.customer, [o1.pk], issued_by=self.user)
        self.assertFalse(Order.objects.pending_immediate().filter(pk=o1.pk).exists())

    def test_invoiced_order_edit_blocked(self):
        from . import services
        o1 = self._order(self.d1); self._item(o1, '弁当', 500, 5)
        services.issue_invoice(self.customer, [o1.pk], issued_by=self.user)
        resp = self.client.get(reverse('orders:order_edit', kwargs={'pk': o1.pk}))
        self.assertRedirects(resp, reverse('orders:order_detail', kwargs={'pk': o1.pk}))

    def test_customer_bank_account_snapshot(self):
        """顧客に紐づけた振込先口座が発行時にスナップショットされる（未設定はデフォルトへフォールバック）。"""
        from . import services
        from .models import BankAccount, OrderSettings
        s = OrderSettings.load(); s.bank_info = 'デフォルト銀行 000'; s.save()
        ba = BankAccount.objects.create(label='A社向け', info='みずほ銀行 港北支店 普通 999')
        # 口座未設定 → デフォルト
        o1 = self._order(self.d1); self._item(o1, '弁当', 500, 5)
        inv1 = services.issue_invoice(self.customer, [o1.pk], issued_by=self.user)
        self.assertEqual(inv1.bank_info, 'デフォルト銀行 000')
        # 顧客に口座を紐づけ → その口座
        self.customer.bank_account = ba; self.customer.save()
        o2 = self._order(self.d2); self._item(o2, '弁当', 500, 5)
        inv2 = services.issue_invoice(self.customer, [o2.pk], issued_by=self.user)
        self.assertEqual(inv2.bank_info, 'みずほ銀行 港北支店 普通 999')

    def test_seal_only_for_email_delivery(self):
        from . import services
        from .models import OrderSettings
        from .pdf import build_invoice_context
        s = OrderSettings.load()
        s.seal_image_data = 'data:image/png;base64,ABC'; s.save()
        # 郵送顧客 → 社印なし
        self.customer.invoice_delivery = 'MAIL'; self.customer.save()
        o1 = self._order(self.d1); self._item(o1, '弁当', 500, 5)
        inv1 = services.issue_invoice(self.customer, [o1.pk], issued_by=self.user)
        self.assertEqual(build_invoice_context(inv1)['seal_data'], '')
        # メール顧客 → 社印あり
        self.customer.invoice_delivery = 'EMAIL'; self.customer.save()
        o2 = self._order(self.d2); self._item(o2, '弁当', 500, 5)
        inv2 = services.issue_invoice(self.customer, [o2.pk], issued_by=self.user)
        self.assertTrue(build_invoice_context(inv2)['seal_data'].startswith('data:image'))

    def test_batch_zip(self):
        import zipfile, io as _io
        from . import services
        o1 = self._order(self.d1); self._item(o1, '弁当', 500, 5)
        o2 = self._order(self.d2); self._item(o2, '弁当', 600, 3)
        i1 = services.issue_invoice(self.customer, [o1.pk], issued_by=self.user)
        i2 = services.issue_invoice(self.customer, [o2.pk], issued_by=self.user)
        resp = self.client.post(reverse('orders:invoice_batch_zip'),
                                {'invoice_pks': [i1.pk, i2.pk]})
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp['Content-Type'], 'application/zip')
        zf = zipfile.ZipFile(_io.BytesIO(resp.content))
        self.assertEqual(len(zf.namelist()), 2)
        self.assertTrue(all(zf.read(n)[:4] == b'%PDF' for n in zf.namelist()))

    def test_batch_print_combined_and_dl(self):
        from . import services
        from .pdf import generate_invoices_combined
        o1 = self._order(self.d1); self._item(o1, '弁当', 500, 5)
        o2 = self._order(self.d2); self._item(o2, '弁当', 600, 3)
        i1 = services.issue_invoice(self.customer, [o1.pk], issued_by=self.user)
        i2 = services.issue_invoice(self.customer, [o2.pk], issued_by=self.user)
        combined = generate_invoices_combined([i1, i2]).getvalue()
        self.assertTrue(combined.startswith(b'%PDF'))
        # batch print はインラインPDF
        resp = self.client.post(reverse('orders:invoice_batch_print'),
                                {'invoice_pks': [i1.pk, i2.pk]})
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp['Content-Type'], 'application/pdf')
        self.assertIn('inline', resp['Content-Disposition'])
        # ?dl=1 は添付（保存）、無しはインライン
        r_dl = self.client.get(reverse('orders:invoice_pdf', kwargs={'pk': i1.pk}) + '?dl=1')
        self.assertIn('attachment', r_dl['Content-Disposition'])
        r_in = self.client.get(reverse('orders:invoice_pdf', kwargs={'pk': i1.pk}))
        self.assertIn('inline', r_in['Content-Disposition'])

    def test_bank_account_crud_views(self):
        from .models import BankAccount
        # 作成
        resp = self.client.post(reverse('orders:bank_account_create'),
                                {'label': 'テスト口座', 'info': '銀行 支店 普通 123', 'sort_order': 1})
        self.assertRedirects(resp, reverse('orders:order_settings'))
        ba = BankAccount.objects.get(label='テスト口座')
        # 無効化
        resp = self.client.post(reverse('orders:bank_account_delete', kwargs={'pk': ba.pk}))
        ba.refresh_from_db()
        self.assertFalse(ba.is_active)

    def test_pdf_generates(self):
        from . import services
        from .pdf import generate_invoice
        o1 = self._order(self.d1); self._item(o1, '弁当', 500, 5)
        self._credit(o1, 300)
        inv = services.issue_invoice(self.customer, [o1.pk], issued_by=self.user)
        buf = generate_invoice(inv)
        data = buf.getvalue()
        self.assertTrue(data.startswith(b'%PDF'))

    def test_pdf_full_month_fits_one_page(self):
        """31日フル＋大盛り＋複数単価＋控除でも請求書PDFは1ページに収まる。"""
        from datetime import date
        import calendar
        import weasyprint
        from django.template.loader import render_to_string
        from . import services
        from .models import OrderItem
        from .pdf import build_invoice_context
        from .models import OrderExtraItem
        y, m = self.d1.year, self.d1.month
        last = calendar.monthrange(y, m)[1]
        ids = []
        for d in range(1, last + 1):
            o = Order.objects.create(customer=self.customer, order_date=date(y, m, d),
                                     delivery_date=date(y, m, d))
            OrderItem.objects.create(order=o, product_name='普通', quantity_large=3,
                                     quantity_regular=7, unit_price=650, subtotal=650 * 10 + 50 * 3)
            if d % 3 == 0:  # 2つ目の単価
                OrderItem.objects.create(order=o, product_name='特上', quantity_regular=2,
                                         unit_price=700, subtotal=1400)
            if d % 5 == 0:  # 3つ目の単価
                OrderItem.objects.create(order=o, product_name='廉価', quantity_regular=1,
                                         unit_price=500, subtotal=500)
            if d % 6 == 0:  # 追加商品
                OrderExtraItem.objects.create(order=o, product_name='お茶', unit_price=100,
                                              quantity=2, subtotal=200)
            ids.append(o.pk)
        note = ('翌月より一部メニューの価格改定を予定しております。\n'
                '詳細は別途ご案内いたします。何卒よろしくお願い申し上げます。')
        inv = services.issue_invoice(
            self.customer, ids, pattern='MONTH_END',
            period_start=date(y, m, 1), period_end=date(y, m, last),
            issued_by=self.user, notes=note)
        ctx = build_invoice_context(inv)
        self.assertTrue(ctx['bento_total_qty'] > 0)   # お弁当総食数
        html = render_to_string('orders/invoice.html', ctx)
        pages = len(weasyprint.HTML(string=html).render().pages)
        self.assertEqual(pages, 1)   # 単段でも31日フル＋全部乗せで1ページ

    def test_views_list_detail_and_monthly(self):
        from . import services
        from .models import Invoice
        o1 = self._order(self.d1); self._item(o1, '弁当', 500, 5)
        inv = services.issue_invoice(self.customer, [o1.pk], issued_by=self.user)
        self.assertEqual(self.client.get(reverse('orders:invoice_list')).status_code, 200)
        self.assertEqual(self.client.get(reverse('orders:invoice_detail', kwargs={'pk': inv.pk})).status_code, 200)
        self.assertEqual(self.client.get(reverse('orders:invoice_issue_monthly')).status_code, 200)

    def test_price_matrix_columns_and_cells(self):
        from . import services
        from datetime import date
        import calendar
        o1 = self._order(self.d1)
        self._item(o1, '普通', 500, 5)   # 2500
        self._item(o1, '大盛', 600, 2)   # 1200 → 同日・2価格帯
        last = calendar.monthrange(self.d1.year, self.d1.month)[1]
        pstart = date(self.d1.year, self.d1.month, 1)
        pend = date(self.d1.year, self.d1.month, last)
        inv = services.issue_invoice(
            self.customer, [o1.pk], pattern='MONTH_END',
            period_start=pstart, period_end=pend, issued_by=self.user,
        )
        m = services.build_price_matrix(
            services.lines_as_entries(inv.lines.all()), inv.period_start, inv.period_end)
        # 単価列は降順（高い順）
        self.assertEqual(m['price_columns'], [600, 500])
        # 対象月の全日付ぶん
        self.assertEqual(len(m['day_rows']), last)
        row = next(r for r in m['day_rows'] if r['date'] == self.d1)
        # cells_list は price_columns と同じ並び：600→2個, 500→5個
        self.assertEqual(row['cells_list'], [2, 5])
        self.assertEqual(int(row['amount']), 3700)
        self.assertFalse(m['has_extras'])
        # 合計行：各列の数量合計
        self.assertEqual(m['col_totals_list'], [2, 5])
        self.assertEqual(int(m['charge_total']), 3700)
        # 受注の無い日は空セル
        self.assertTrue(any(not r['has_order'] for r in m['day_rows']))

    def test_price_matrix_large_column(self):
        from . import services
        from .models import OrderItem
        o1 = self._order(self.d1)
        # 弁当10個（うち大盛り3個）＝ 基本650 + 大盛り+50×3
        OrderItem.objects.create(
            order=o1, product_name='弁当', quantity_large=3, quantity_regular=7,
            unit_price=650, subtotal=650 * 10 + 50 * 3,  # 6650
        )
        inv = services.issue_invoice(self.customer, [o1.pk], issued_by=self.user)
        m = services.build_price_matrix(services.lines_as_entries(inv.lines.all()))
        self.assertEqual(m['price_columns'], [650])
        self.assertTrue(m['has_large'])
        self.assertEqual(m['large_unit'], 50)      # 上乗せ額/大盛り数 = 150/3
        self.assertEqual(m['large_total'], 3)
        row = m['day_rows'][0]
        self.assertEqual(row['cells_list'], [10])  # 基本列は総数10
        self.assertEqual(row['large'], 3)          # 大盛り列は3
        self.assertEqual(int(row['amount']), 6650)
        # 整合：650×10 + 50×3 = 6650
        self.assertEqual(650 * 10 + m['large_unit'] * m['large_total'], int(m['charge_total']))

    def test_price_matrix_integrated_large_no_upcharge(self):
        from . import services
        from .models import OrderItem
        o1 = self._order(self.d1)
        # 一体型チャーハン：大盛りでも割増なし → subtotal = 650×数量（+50なし）
        OrderItem.objects.create(
            order=o1, product_name='海鮮チャーハン', quantity_large=4, quantity_regular=6,
            unit_price=650, subtotal=650 * 10,  # 割増なし
        )
        inv = services.issue_invoice(self.customer, [o1.pk], issued_by=self.user)
        m = services.build_price_matrix(services.lines_as_entries(inv.lines.all()))
        self.assertEqual(m['price_columns'], [650])
        # 上乗せ0なので大盛り列には出さない（通常の¥650弁当として計上）
        self.assertFalse(m['has_large'])
        self.assertEqual(m['large_total'], 0)
        row = m['day_rows'][0]
        self.assertEqual(row['cells_list'], [10])
        self.assertEqual(int(row['amount']), 6500)

    def test_price_matrix_extras_column(self):
        from . import services
        from .models import OrderExtraItem
        o1 = self._order(self.d1); self._item(o1, '弁当', 500, 5)   # 2500
        # 追加商品（お茶）→ 単価列に混ぜず「追加」金額へ
        OrderExtraItem.objects.create(order=o1, product_name='お茶', unit_price=100, quantity=2, subtotal=200)
        # もう1種の追加（デザート）も同日に
        OrderExtraItem.objects.create(order=o1, product_name='デザート', unit_price=150, quantity=1, subtotal=150)
        inv = services.issue_invoice(self.customer, [o1.pk], issued_by=self.user)
        m = services.build_price_matrix(services.lines_as_entries(inv.lines.all()))
        self.assertEqual(m['price_columns'], [500])   # お茶/デザートは単価列を作らない
        self.assertTrue(m['has_extras'])
        row = m['day_rows'][0]
        self.assertEqual(row['cells_list'], [5])
        self.assertEqual(int(row['extras']), 350)      # 200 + 150
        self.assertEqual(int(row['amount']), 2850)     # 2500 + 350
        self.assertEqual(int(m['extras_total']), 350)
        # 追加は品名ごとに明記（複数種対応）
        items = {it['name']: (it['qty'], int(it['amount'])) for it in row['extra_items']}
        self.assertEqual(items['お茶'], (2, 200))
        self.assertEqual(items['デザート'], (1, 150))
        names = {t['name'] for t in m['extra_name_totals']}
        self.assertEqual(names, {'お茶', 'デザート'})

    def test_immediate_preview_and_confirm_flow(self):
        from .models import Invoice
        o1 = self._order(self.d1, billing='IMMEDIATE'); self._item(o1, '弁当', 500, 4)  # 2000
        # プレビュー画面が描画される
        resp = self.client.get(reverse('orders:invoice_issue_order', kwargs={'order_pk': o1.pk}))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, '2,000')
        # 確定発行
        resp2 = self.client.post(reverse('orders:invoice_issue_confirm'), {
            'customer_id': self.customer.pk, 'order_ids': [o1.pk],
            'pattern': 'IMMEDIATE', 'due_date': self.d2.strftime('%Y-%m-%d'),
        })
        inv = Invoice.objects.get(customer=self.customer)
        self.assertRedirects(resp2, reverse('orders:invoice_detail', kwargs={'pk': inv.pk}))
        self.assertEqual(int(inv.net_amount), 2000)

    def test_delete_adjustment_after_void(self):
        """請求書取消後、それに含まれていた調整を削除できる（PROTECTで500にならない）。"""
        from . import services
        from .models import OrderAdjustment, InvoiceLine
        o1 = self._order(self.d1); self._item(o1, '弁当', 500, 5)
        adj = self._credit(o1, 150)
        inv = services.issue_invoice(self.customer, [o1.pk], issued_by=self.user)
        self.assertTrue(InvoiceLine.objects.filter(adjustment=adj).exists())
        services.void_invoice(inv, reason='テスト', user=self.user)
        # 取消後は調整の削除ができる（画面フロー）
        resp = self.client.post(reverse('orders:adjustment_delete', kwargs={'pk': adj.pk}))
        self.assertRedirects(resp, reverse('orders:order_detail', kwargs={'pk': o1.pk}))
        self.assertFalse(OrderAdjustment.objects.filter(pk=adj.pk).exists())
        # 取消請求書の明細（履歴）は残り、リンクだけ外れている
        lines = InvoiceLine.objects.filter(invoice=inv, source_type='ADJUSTMENT')
        self.assertTrue(lines.exists())
        self.assertTrue(all(l.adjustment_id is None for l in lines))

    def test_void_view_flow(self):
        from . import services
        o1 = self._order(self.d1); self._item(o1, '弁当', 500, 5)
        inv = services.issue_invoice(self.customer, [o1.pk], issued_by=self.user)
        self.assertEqual(self.client.get(reverse('orders:invoice_void', kwargs={'pk': inv.pk})).status_code, 200)
        resp = self.client.post(reverse('orders:invoice_void', kwargs={'pk': inv.pk}), {'void_reason': 'test'})
        self.assertRedirects(resp, reverse('orders:invoice_detail', kwargs={'pk': inv.pk}))
        inv.refresh_from_db()
        self.assertEqual(inv.status, 'VOID')

    def test_monthly_page_lists_invoice_payers_only(self):
        """未請求サマリーは請求書払いの顧客のみ（現金等は除外）。"""
        from .models import PaymentMethod
        cash = PaymentMethod.objects.create(name='現金')
        cash_cust = Customer.objects.create(customer_type='B2B', company_name='現金商店',
                                            price_type='A', bento_type='REGULAR', payment_method=cash)
        oc = Order.objects.create(customer=cash_cust, order_date=self.d1, delivery_date=self.d1)
        self._item(oc, '弁当', 500, 5)
        # 請求書払いの self.customer にも未請求受注
        oi = self._order(self.d1); self._item(oi, '弁当', 500, 5)
        resp = self.client.get(reverse('orders:invoice_issue_monthly'), {'month': self.month_str})
        self.assertEqual(resp.status_code, 200)
        rows = resp.context['summary_rows']
        names = {str(r['customer']) for r in rows}
        self.assertIn(str(self.customer), names)       # 請求書払いは出る
        self.assertNotIn('現金商店', names)             # 現金は出ない
        # 顧客ドロップダウンにも現金顧客は出ない
        cust_names = {str(c) for c in resp.context['customers']}
        self.assertNotIn('現金商店', cust_names)

    def test_monthly_batch_issues_per_customer(self):
        from .models import Invoice
        o1 = self._order(self.d1); self._item(o1, '弁当', 500, 5)
        o2 = self._order(self.d2); self._item(o2, '弁当', 600, 2)
        resp = self.client.post(reverse('orders:invoice_issue_monthly_batch'), {'month': self.month_str})
        self.assertRedirects(resp, reverse('orders:invoice_list'))
        # この顧客の受注2件が1枚にまとまる
        self.assertEqual(Invoice.objects.filter(customer=self.customer).count(), 1)
        inv = Invoice.objects.get(customer=self.customer)
        self.assertEqual(int(inv.charge_total), 3700)

    def test_spot_multi_customer_rejected(self):
        from .models import Invoice
        other = Customer.objects.create(customer_type='B2B', company_name='別社',
                                        price_type='A', bento_type='REGULAR')
        o1 = self._order(self.d1); self._item(o1, '弁当', 500, 5)
        o2 = Order.objects.create(customer=other, order_date=self.d1, delivery_date=self.d1)
        self._item(o2, '弁当', 500, 5)
        self.client.post(reverse('orders:invoice_issue_spot'),
                         {'order_pks': [o1.pk, o2.pk]})
        # 顧客混在はエラーでリダイレクト（発行されない）
        self.assertEqual(Invoice.objects.count(), 0)
