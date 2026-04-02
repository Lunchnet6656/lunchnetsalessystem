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
    order_number = models.CharField(max_length=20, unique=True, verbose_name="受注番号")
    customer = models.ForeignKey(
        Customer, on_delete=models.PROTECT, related_name='orders',
        verbose_name="顧客"
    )
    order_date = models.DateField(verbose_name="受注日")
    delivery_date = models.DateField(db_index=True, verbose_name="納品日")
    notes = models.TextField(blank=True, verbose_name="備考")
    receipt_memo = models.CharField(max_length=100, default='お弁当代', verbose_name="但し書き")
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

    class Meta:
        verbose_name = "受注メニュー表示設定"
        verbose_name_plural = "受注メニュー表示設定"

    def __str__(self):
        return f"OrderMenuPermission({self.user})"
