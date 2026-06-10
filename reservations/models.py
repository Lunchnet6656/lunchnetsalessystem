"""個人客セルフ予約（取り置き）モデル。

仕様: .company/engineering/harness/specs/w001-予約システム-要件定義.md

- LineMember        … LINE紐付けの個人会員（LIFFで取得した userId をキーに本人識別）
- Reservation       … 取り置き予約（受取拠点・受取日・状態・予約番号）
- ReservationItem   … 予約明細（メニュー×個数×単価）

拠点側の設定フィールド（reservation_enabled / qr_reserve_token / default_product_cap）は
sales.SalesLocation 側に持つ（既存QRトークンの仕組みに合わせる）。
"""
import secrets

from django.db import models

from sales.models import Product, SalesLocation


def _generate_reservation_number() -> str:
    """控え・受取時の照合に使う予約番号（読み上げ可能な8桁HEX大文字）。"""
    return secrets.token_hex(4).upper()


class LineMember(models.Model):
    """LINE紐付けの個人会員。LIFFで取得した userId をキーに本人を識別する。

    法人顧客（orders.Customer）とは別物。MVPは最小項目（名前＋userId）。
    """
    line_user_id = models.CharField(
        max_length=64, unique=True, db_index=True,
        verbose_name="LINEユーザーID",
        help_text="LIFFで自動取得した userId。本人識別・LINE紐付けのキー。",
    )
    name = models.CharField(
        max_length=100, verbose_name="お名前",
        help_text="受取時の呼び出し用。初回登録。",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "LINE会員"
        verbose_name_plural = "LINE会員"

    def __str__(self):
        return f"{self.name}（{self.line_user_id[:8]}…）"


class Reservation(models.Model):
    STATUS_RECEIVED = "received"
    STATUS_HANDED = "handed"
    STATUS_CANCELLED = "cancelled"
    STATUS_NOSHOW = "noshow"
    STATUS_CHOICES = [
        (STATUS_RECEIVED, "受付済"),
        (STATUS_HANDED, "受渡済"),
        (STATUS_CANCELLED, "キャンセル"),
        (STATUS_NOSHOW, "ノーショー"),
    ]
    # 枠カウント・二重予約判定の対象になる「有効」な状態（受付済/受渡済）。
    ACTIVE_STATUSES = (STATUS_RECEIVED, STATUS_HANDED)

    member = models.ForeignKey(
        LineMember, on_delete=models.PROTECT, related_name="reservations",
        verbose_name="会員",
    )
    sales_location = models.ForeignKey(
        SalesLocation, on_delete=models.PROTECT, related_name="reservations",
        verbose_name="受取拠点",
    )
    pickup_date = models.DateField(db_index=True, verbose_name="受取日")
    status = models.CharField(
        max_length=10, choices=STATUS_CHOICES, default=STATUS_RECEIVED,
        db_index=True, verbose_name="状態",
    )
    reservation_number = models.CharField(
        max_length=12, unique=True, default=_generate_reservation_number,
        verbose_name="予約番号",
    )
    # 前日リマインドの二重送信を防ぐフラグ（S3-B）。送信済なら再送しない。
    reminder_sent = models.BooleanField(default=False, verbose_name="リマインド送信済")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-pickup_date", "-created_at"]
        verbose_name = "予約"
        verbose_name_plural = "予約"
        indexes = [
            models.Index(fields=["sales_location", "pickup_date", "status"]),
        ]
        constraints = [
            # 二重注文防止のDBレベル担保：同一会員×受取日に有効予約は1件まで（競合時の最後の砦）。
            models.UniqueConstraint(
                fields=["member", "pickup_date"],
                condition=models.Q(status__in=("received", "handed")),
                name="uniq_active_reservation_per_member_day",
            ),
        ]

    def __str__(self):
        return f"{self.reservation_number} {self.sales_location.name} {self.pickup_date}"

    @property
    def total_quantity(self):
        return sum(item.quantity for item in self.items.all())

    @property
    def total_amount(self):
        return sum(item.subtotal for item in self.items.all())

    @property
    def is_active(self):
        """枠カウントの対象になる有効な予約か（キャンセル・ノーショーは除外）。"""
        return self.status in self.ACTIVE_STATUSES


class ReservationItem(models.Model):
    reservation = models.ForeignKey(
        Reservation, on_delete=models.CASCADE, related_name="items",
        verbose_name="予約",
    )
    product = models.ForeignKey(
        Product, on_delete=models.PROTECT, null=True, blank=True,
        verbose_name="商品",
    )
    product_name = models.CharField(
        max_length=255, verbose_name="商品名",
        help_text="注文時点の商品名スナップショット。",
    )
    # 盛りは orders.OrderItem に合わせ大盛り/普通/小盛りの3区分。quantity は合計（save で算出）。
    quantity = models.PositiveIntegerField(default=0, verbose_name="個数（合計）")
    quantity_large = models.PositiveIntegerField(default=0, verbose_name="大盛り数")
    quantity_regular = models.PositiveIntegerField(default=0, verbose_name="普通盛り数")
    quantity_small = models.PositiveIntegerField(default=0, verbose_name="小盛り数")
    unit_price = models.DecimalField(
        max_digits=10, decimal_places=0, default=0, verbose_name="単価（基本・盛り共通）",
    )
    large_surcharge = models.DecimalField(
        max_digits=10, decimal_places=0, default=0,
        verbose_name="大盛り割増", help_text="大盛り1個あたりの追加額（既定50・大盛りごはんProduct由来）。",
    )

    class Meta:
        verbose_name = "予約明細"
        verbose_name_plural = "予約明細"

    def save(self, *args, **kwargs):
        self.quantity = self.quantity_large + self.quantity_regular + self.quantity_small
        if not self.product_name and self.product:
            self.product_name = self.product.name
        super().save(*args, **kwargs)

    @property
    def subtotal(self):
        # 大盛りのみ割増。普通・小盛りは基本単価。
        return self.unit_price * self.quantity + self.large_surcharge * self.quantity_large

    def __str__(self):
        return f"{self.product_name} × {self.quantity}"
