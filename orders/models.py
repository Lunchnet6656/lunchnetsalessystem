from django.db import models
from django.conf import settings
from django.utils import timezone


class PaymentMethod(models.Model):
    name = models.CharField(max_length=50, unique=True, verbose_name="支払い方法名")
    is_active = models.BooleanField(default=True, verbose_name="有効")
    sort_order = models.PositiveIntegerField(default=0, verbose_name="表示順")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['sort_order', 'name']
        verbose_name = "支払い方法"
        verbose_name_plural = "支払い方法"

    def __str__(self):
        return self.name


class DeliveryBin(models.Model):
    name = models.CharField(max_length=50, unique=True, verbose_name="便名")
    is_active = models.BooleanField(default=True, verbose_name="有効")
    sort_order = models.PositiveIntegerField(default=0, verbose_name="表示順")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['sort_order', 'name']
        verbose_name = "配達便"
        verbose_name_plural = "配達便"

    def __str__(self):
        return self.name


class Customer(models.Model):
    CUSTOMER_TYPE_CHOICES = [
        ('B2B', '法人'),
        ('INDIVIDUAL', '個人'),
    ]
    PRICE_TYPE_CHOICES = [
        ('A', '価格A'),
        ('B', '価格B'),
        ('C', '価格C'),
        ('CUSTOM', '独自価格'),
    ]
    BENTO_TYPE_CHOICES = [
        ('REGULAR', '通常弁当'),
        ('CATERING', '仕出し弁当'),
    ]
    INVOICE_TIMING_CHOICES = [
        ('IMMEDIATE', '即時発行'),
        ('MONTH_END', '月末締め発行'),
    ]

    customer_type = models.CharField(
        max_length=10, choices=CUSTOMER_TYPE_CHOICES, default='B2B',
        verbose_name="顧客種別"
    )
    company_name = models.CharField(max_length=200, blank=True, verbose_name="会社名")
    department = models.CharField(max_length=100, blank=True, verbose_name="部署名")
    contact_person = models.CharField(max_length=100, blank=True, verbose_name="担当者名")
    name = models.CharField(max_length=100, blank=True, default='', verbose_name="顧客名")
    postal_code = models.CharField(max_length=10, blank=True, verbose_name="郵便番号")
    address = models.TextField(blank=True, verbose_name="住所")
    phone = models.CharField(max_length=20, blank=True, verbose_name="電話番号")
    fax = models.CharField(max_length=20, blank=True, verbose_name="FAX番号")
    email = models.EmailField(blank=True, verbose_name="メールアドレス")
    price_type = models.CharField(
        max_length=6, choices=PRICE_TYPE_CHOICES, default='A',
        verbose_name="価格タイプ"
    )
    custom_price_normal = models.DecimalField(
        max_digits=10, decimal_places=0, null=True, blank=True,
        verbose_name="独自価格（★なし）"
    )
    custom_price_star = models.DecimalField(
        max_digits=10, decimal_places=0, null=True, blank=True,
        verbose_name="独自価格（★あり）"
    )
    custom_price_large = models.DecimalField(
        max_digits=10, decimal_places=0, null=True, blank=True,
        verbose_name="独自大盛り割増"
    )
    payment_method = models.ForeignKey(
        PaymentMethod, on_delete=models.SET_NULL, null=True, blank=True,
        verbose_name="支払い方法"
    )
    invoice_timing = models.CharField(
        max_length=10, choices=INVOICE_TIMING_CHOICES, blank=True, default='',
        verbose_name="請求書発行タイミング"
    )
    delivery_bin = models.ForeignKey(
        'DeliveryBin', on_delete=models.SET_NULL, null=True, blank=True,
        verbose_name="配達便"
    )
    notes = models.TextField(blank=True, verbose_name="備考")
    bento_type = models.CharField(
        max_length=10, choices=BENTO_TYPE_CHOICES, default='REGULAR',
        verbose_name="弁当種別"
    )
    is_regular = models.BooleanField(default=False, verbose_name="定期注文")
    REGULAR_TYPE_CHOICES = [
        ('WEEKDAY', '平日毎日（月〜金）'),
        ('CUSTOM', '曜日指定'),
    ]
    regular_type = models.CharField(
        max_length=10, choices=REGULAR_TYPE_CHOICES, default='WEEKDAY', blank=True,
        verbose_name="定期スケジュール"
    )
    schedule_mon = models.BooleanField(default=False, verbose_name="月")
    schedule_tue = models.BooleanField(default=False, verbose_name="火")
    schedule_wed = models.BooleanField(default=False, verbose_name="水")
    schedule_thu = models.BooleanField(default=False, verbose_name="木")
    schedule_fri = models.BooleanField(default=False, verbose_name="金")
    schedule_sat = models.BooleanField(default=False, verbose_name="土")
    schedule_sun = models.BooleanField(default=False, verbose_name="日")
    is_active = models.BooleanField(default=True, verbose_name="有効")
    include_in_total = models.BooleanField(default=True, verbose_name="全体注文数に含める")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['customer_type', 'company_name', 'department', 'name']
        verbose_name = "顧客"
        verbose_name_plural = "顧客"

    def __str__(self):
        if self.customer_type == 'B2B' and self.company_name:
            parts = self.company_name
            if self.department:
                parts = f"{parts} {self.department}"
            if self.contact_person:
                parts = f"{parts}（{self.contact_person}）"
            return parts
        return self.name or ''

    def display_name(self):
        if self.customer_type == 'B2B' and self.company_name:
            return self.company_name
        return self.name or ''


class Order(models.Model):
    # 請求パターンの軸は「月末締め（定期）」か「即時発行（都度）」の2択。顧客の invoice_timing と一致。
    # スポットまとめ（複数受注を1枚に束ねる）は v2 の請求書発行時の操作で実現するため、受注フラグは持たない。
    BILLING_PATTERN_CHOICES = [
        ('MONTH_END', '月末締め'),
        ('IMMEDIATE', '即時発行'),
    ]

    order_number = models.CharField(max_length=20, unique=True, verbose_name="受注番号")
    customer = models.ForeignKey(
        Customer, on_delete=models.PROTECT, related_name='orders',
        verbose_name="顧客"
    )
    order_date = models.DateField(verbose_name="受注日")
    delivery_date = models.DateField(db_index=True, verbose_name="納品日")
    notes = models.TextField(blank=True, verbose_name="備考")
    receipt_memo = models.CharField(max_length=100, default='お弁当代', verbose_name="但し書き")
    billing_pattern = models.CharField(
        max_length=10, choices=BILLING_PATTERN_CHOICES, default='MONTH_END',
        verbose_name="請求パターン"
    )
    pdf_printed_at = models.DateTimeField(null=True, blank=True, verbose_name="PDF出力日時")
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
        null=True, blank=True, related_name='created_orders',
        verbose_name="作成者"
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-delivery_date', '-created_at']
        verbose_name = "受注"
        verbose_name_plural = "受注"
        constraints = [
            models.UniqueConstraint(
                fields=['customer', 'delivery_date'],
                name='unique_customer_delivery_date'
            )
        ]

    def __str__(self):
        return f"{self.order_number} - {self.customer}"

    @property
    def total(self):
        bento = sum(item.subtotal for item in self.items.all())
        extra = sum(ei.subtotal for ei in self.extra_items.all())
        return bento + extra

    @property
    def bento_total(self):
        return sum(item.subtotal for item in self.items.all())

    @property
    def total_quantity(self):
        return sum(item.quantity for item in self.items.all())

    # --- 受注調整（返品・取消・返金・値引き）による相殺。元注文は不変・別レコードで相殺 ---
    @property
    def adjustments_total(self):
        """有効な調整の合計（税込）。"""
        return sum(a.amount for a in self.adjustments.all())

    @property
    def credit_total(self):
        """うち請求相殺（CREDIT）ぶん。将来の請求書で控除行になる。"""
        return sum(a.amount for a in self.adjustments.all()
                   if a.settlement == OrderAdjustment.SETTLEMENT_CREDIT)

    @property
    def cash_refund_total(self):
        """うち現金・振込返金（CASH）ぶん。請求書には載せない。"""
        return sum(a.amount for a in self.adjustments.all()
                   if a.settlement == OrderAdjustment.SETTLEMENT_CASH)

    @property
    def net_total(self):
        """純額 ＝ 受注額 − 調整額（将来の請求対象額）。"""
        return self.total - self.adjustments_total

    def save(self, *args, **kwargs):
        if not self.order_number:
            self.order_number = self._generate_order_number(self.delivery_date)
        super().save(*args, **kwargs)

    @staticmethod
    def _generate_order_number(delivery_date):
        date_str = delivery_date.strftime('%Y%m%d')
        last = Order.objects.filter(
            order_number__startswith=f'ORD-{date_str}'
        ).order_by('-order_number').first()
        if last:
            seq = int(last.order_number.split('-')[-1]) + 1
        else:
            seq = 1
        return f"ORD-{date_str}-{seq:04d}"


class OrderItem(models.Model):
    order = models.ForeignKey(
        Order, on_delete=models.CASCADE, related_name='items',
        verbose_name="受注"
    )
    product = models.ForeignKey(
        'sales.Product', on_delete=models.PROTECT,
        null=True, blank=True, verbose_name="商品"
    )
    product_name = models.CharField(max_length=255, verbose_name="商品名")
    quantity = models.PositiveIntegerField(default=0, verbose_name="数量（合計）")
    quantity_large = models.PositiveIntegerField(default=0, verbose_name="大盛り数")
    quantity_regular = models.PositiveIntegerField(default=0, verbose_name="普通盛り数")
    quantity_small = models.PositiveIntegerField(default=0, verbose_name="小盛り数")
    unit_price = models.DecimalField(
        max_digits=10, decimal_places=0, verbose_name="単価"
    )
    subtotal = models.DecimalField(
        max_digits=10, decimal_places=0, verbose_name="小計"
    )

    class Meta:
        verbose_name = "受注明細"
        verbose_name_plural = "受注明細"

    def __str__(self):
        return f"{self.product_name} x {self.quantity}"

    def save(self, *args, **kwargs):
        self.quantity = self.quantity_large + self.quantity_regular + self.quantity_small
        if not self.product_name and self.product:
            self.product_name = self.product.name
        super().save(*args, **kwargs)


class ExtraProduct(models.Model):
    name = models.CharField(max_length=200, verbose_name="商品名")
    unit_price = models.DecimalField(max_digits=10, decimal_places=0, verbose_name="単価")
    is_active = models.BooleanField(default=True, verbose_name="有効")
    sort_order = models.PositiveIntegerField(default=0, verbose_name="表示順")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['sort_order', 'name']
        verbose_name = "追加商品"
        verbose_name_plural = "追加商品"

    def __str__(self):
        return self.name


class OrderExtraItem(models.Model):
    order = models.ForeignKey(
        Order, on_delete=models.CASCADE, related_name='extra_items',
        verbose_name="受注"
    )
    extra_product = models.ForeignKey(
        ExtraProduct, on_delete=models.PROTECT,
        null=True, blank=True, verbose_name="追加商品マスタ"
    )
    product_name = models.CharField(max_length=200, verbose_name="商品名")
    unit_price = models.DecimalField(max_digits=10, decimal_places=0, verbose_name="単価")
    quantity = models.PositiveIntegerField(default=1, verbose_name="数量")
    subtotal = models.DecimalField(max_digits=10, decimal_places=0, default=0, verbose_name="小計")

    class Meta:
        verbose_name = "追加商品明細"
        verbose_name_plural = "追加商品明細"

    def __str__(self):
        return f"{self.product_name} x {self.quantity}"

    def save(self, *args, **kwargs):
        self.subtotal = self.unit_price * self.quantity
        super().save(*args, **kwargs)


class OrderAdjustment(models.Model):
    """受注に対する調整（返品・取消・返金・値引き）。元注文は書き換えず、別レコードで相殺する。

    仕様: 受注調整_要件定義.md
    - 1イベント（返品/取消/クレーム1件）＝ 1レコード。1受注に複数積める。
    - 金額はすべて税込（受注明細と同基準）。
    - settlement で「請求相殺（将来の請求書に控除行として載る）」と「現金・振込返金（載せない）」を区別。
    """
    KIND_RETURN = 'RETURN'
    KIND_CANCEL = 'CANCEL'
    KIND_DISCOUNT = 'DISCOUNT'
    KIND_OTHER = 'OTHER'
    KIND_CHOICES = [
        (KIND_RETURN, '返品'),
        (KIND_CANCEL, '取消'),
        (KIND_DISCOUNT, '値引き'),
        (KIND_OTHER, 'その他'),
    ]
    SETTLEMENT_CREDIT = 'CREDIT'
    SETTLEMENT_CASH = 'CASH'
    SETTLEMENT_CHOICES = [
        (SETTLEMENT_CREDIT, '請求相殺'),
        (SETTLEMENT_CASH, '現金・振込返金'),
    ]

    order = models.ForeignKey(
        Order, on_delete=models.PROTECT, related_name='adjustments', verbose_name="受注"
    )
    kind = models.CharField(max_length=10, choices=KIND_CHOICES, verbose_name="種別")
    settlement = models.CharField(max_length=10, choices=SETTLEMENT_CHOICES, verbose_name="返金方法")
    amount = models.DecimalField(max_digits=10, decimal_places=0, verbose_name="調整額（税込）")
    reason = models.TextField(verbose_name="理由")
    occurred_on = models.DateField(default=timezone.localdate, db_index=True, verbose_name="発生日")
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='created_order_adjustments', verbose_name="登録者"
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-occurred_on', '-created_at']
        verbose_name = "受注調整"
        verbose_name_plural = "受注調整"

    def __str__(self):
        return f"{self.order.order_number} {self.get_kind_display()} ¥{self.amount}"

    @property
    def is_credit(self):
        return self.settlement == self.SETTLEMENT_CREDIT

    def recalc_amount_from_lines(self):
        """明細行があれば合計で amount を上書きして返す（自由額のときは行が無いので据え置き）。"""
        if self.pk and self.lines.exists():
            self.amount = sum(line.subtotal for line in self.lines.all())
        return self.amount


class OrderAdjustmentLine(models.Model):
    """受注調整の内訳（明細単位／まるごと取消のとき）。自由額のときは行を持たない。"""
    adjustment = models.ForeignKey(
        OrderAdjustment, on_delete=models.CASCADE, related_name='lines', verbose_name="受注調整"
    )
    order_item = models.ForeignKey(
        OrderItem, on_delete=models.SET_NULL, null=True, blank=True, verbose_name="対象明細"
    )
    order_extra_item = models.ForeignKey(
        OrderExtraItem, on_delete=models.SET_NULL, null=True, blank=True, verbose_name="対象追加明細"
    )
    label = models.CharField(max_length=255, verbose_name="商品名")
    quantity = models.PositiveIntegerField(default=0, verbose_name="数量")
    unit_price = models.DecimalField(max_digits=10, decimal_places=0, verbose_name="単価（税込）")
    subtotal = models.DecimalField(max_digits=10, decimal_places=0, default=0, verbose_name="小計")

    class Meta:
        verbose_name = "受注調整明細"
        verbose_name_plural = "受注調整明細"

    def __str__(self):
        return f"{self.label} x {self.quantity}"

    def save(self, *args, **kwargs):
        self.subtotal = self.unit_price * self.quantity
        super().save(*args, **kwargs)


class OrderSettings(models.Model):
    tax_rate = models.DecimalField(
        max_digits=5, decimal_places=2, default=8,
        verbose_name="消費税率（%）"
    )
    company_name = models.CharField(
        max_length=200, default="有限会社ランチサービスネットワーク",
        verbose_name="会社名"
    )
    postal_code = models.CharField(
        max_length=10, default="〒224-0021", verbose_name="郵便番号"
    )
    address = models.CharField(
        max_length=300, default="横浜市都筑区北山田1-1-18", verbose_name="住所"
    )
    tel = models.CharField(
        max_length=20, default="045-593-6656", verbose_name="電話番号"
    )
    fax = models.CharField(
        max_length=20, default="045-592-8424", verbose_name="FAX番号"
    )
    invoice_number = models.CharField(
        max_length=20, default="T4020002059715", verbose_name="登録番号"
    )

    class Meta:
        verbose_name = "受注設定"
        verbose_name_plural = "受注設定"

    def save(self, *args, **kwargs):
        self.pk = 1
        super().save(*args, **kwargs)

    @classmethod
    def load(cls):
        obj, _ = cls.objects.get_or_create(pk=1)
        return obj

    def __str__(self):
        return "受注設定"


class DeliveryCompletion(models.Model):
    customer = models.ForeignKey(Customer, on_delete=models.CASCADE, verbose_name="顧客")
    delivery_date = models.DateField(verbose_name="配達日", db_index=True)
    delivery_bin = models.ForeignKey(DeliveryBin, on_delete=models.CASCADE, verbose_name="配達便")
    completed_at = models.DateTimeField(auto_now_add=True, verbose_name="完了日時")

    class Meta:
        unique_together = [('customer', 'delivery_date')]
        verbose_name = "配達完了"
        verbose_name_plural = "配達完了"

    def __str__(self):
        return f"{self.customer} - {self.delivery_date}"


class OrderUserMenuPermission(models.Model):
    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='order_menu_permission'
    )
    can_view_dashboard = models.BooleanField(default=True, verbose_name="受注ダッシュボード")
    can_view_delivery_list = models.BooleanField(default=True, verbose_name="配達リスト")
    can_view_new_order = models.BooleanField(default=True, verbose_name="新規受注")
    can_view_customers = models.BooleanField(default=True, verbose_name="顧客一覧")
    can_view_settings = models.BooleanField(default=True, verbose_name="設定")
    can_view_csv_export = models.BooleanField(default=True, verbose_name="CSVエクスポート")
    can_view_adjustments = models.BooleanField(default=True, verbose_name="受注調整")

    class Meta:
        verbose_name = "受注メニュー表示設定"
        verbose_name_plural = "受注メニュー表示設定"

    def __str__(self):
        return f"OrderMenuPermission({self.user})"
