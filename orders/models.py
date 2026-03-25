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


class Customer(models.Model):
    CUSTOMER_TYPE_CHOICES = [
        ('B2B', '法人'),
        ('INDIVIDUAL', '個人'),
    ]
    PRICE_TYPE_CHOICES = [
        ('A', '価格A'),
        ('B', '価格B'),
        ('C', '価格C'),
    ]

    customer_type = models.CharField(
        max_length=10, choices=CUSTOMER_TYPE_CHOICES, default='B2B',
        verbose_name="顧客種別"
    )
    company_name = models.CharField(max_length=200, blank=True, verbose_name="会社名")
    contact_person = models.CharField(max_length=100, blank=True, verbose_name="担当者名")
    name = models.CharField(max_length=100, verbose_name="顧客名")
    postal_code = models.CharField(max_length=10, blank=True, verbose_name="郵便番号")
    address = models.TextField(blank=True, verbose_name="住所")
    phone = models.CharField(max_length=20, blank=True, verbose_name="電話番号")
    fax = models.CharField(max_length=20, blank=True, verbose_name="FAX番号")
    email = models.EmailField(blank=True, verbose_name="メールアドレス")
    price_type = models.CharField(
        max_length=1, choices=PRICE_TYPE_CHOICES, default='A',
        verbose_name="価格タイプ"
    )
    payment_method = models.ForeignKey(
        PaymentMethod, on_delete=models.SET_NULL, null=True, blank=True,
        verbose_name="支払い方法"
    )
    notes = models.TextField(blank=True, verbose_name="備考")
    is_regular = models.BooleanField(default=False, verbose_name="定期注文")
    is_active = models.BooleanField(default=True, verbose_name="有効")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['company_name', 'name']
        verbose_name = "顧客"
        verbose_name_plural = "顧客"

    def __str__(self):
        if self.customer_type == 'B2B' and self.company_name:
            return f"{self.company_name}（{self.contact_person}）"
        return self.name

    def display_name(self):
        if self.customer_type == 'B2B' and self.company_name:
            return self.company_name
        return self.name


class Order(models.Model):
    order_number = models.CharField(max_length=20, unique=True, verbose_name="受注番号")
    customer = models.ForeignKey(
        Customer, on_delete=models.PROTECT, related_name='orders',
        verbose_name="顧客"
    )
    order_date = models.DateField(verbose_name="受注日")
    delivery_date = models.DateField(db_index=True, verbose_name="納品日")
    notes = models.TextField(blank=True, verbose_name="備考")
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
        return sum(item.subtotal for item in self.items.all())

    @property
    def total_quantity(self):
        return sum(item.quantity for item in self.items.all())

    def save(self, *args, **kwargs):
        if not self.order_number:
            self.order_number = self._generate_order_number()
        super().save(*args, **kwargs)

    @staticmethod
    def _generate_order_number():
        today = timezone.now().strftime('%Y%m%d')
        last = Order.objects.filter(
            order_number__startswith=f'ORD-{today}'
        ).order_by('-order_number').first()
        if last:
            seq = int(last.order_number.split('-')[-1]) + 1
        else:
            seq = 1
        return f"ORD-{today}-{seq:04d}"


class OrderItem(models.Model):
    order = models.ForeignKey(
        Order, on_delete=models.CASCADE, related_name='items',
        verbose_name="受注"
    )
    product = models.ForeignKey(
        'sales.Product', on_delete=models.PROTECT,
        verbose_name="商品"
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
