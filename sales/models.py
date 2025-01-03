# sales/models.py
from django.db import models
from django.contrib.auth.models import AbstractUser

class CustomUser(AbstractUser):
    full_name = models.CharField(max_length=100, blank=True, null=True)  # 氏名のフィールド
    role = models.CharField(max_length=50)  # 役割によってリンク内容を変更

    # related_name を追加して衝突を回避
    groups = models.ManyToManyField(
        'auth.Group',
        related_name='customuser_groups',  # 変更されたリバースアクセス名
        blank=True,
        help_text='The groups this user belongs to.'
    )

    user_permissions = models.ManyToManyField(
        'auth.Permission',
        related_name='customuser_permissions',  # 変更されたリバースアクセス名
        blank=True,
        help_text='Specific permissions for this user.'
    )

class SalesLocation(models.Model):
    no = models.IntegerField(default=0)
    name = models.CharField(max_length=255)
    type = models.CharField(max_length=100)
    price_type = models.CharField(max_length=100)
    service_name = models.CharField(max_length=100)
    service_price = models.DecimalField(max_digits=10, decimal_places=0, default=0)
    direct_return = models.IntegerField(default=0)

    def __str__(self):
        return f"{self.name}"

class Product(models.Model):
    no = models.IntegerField(default=0)
    week = models.CharField(max_length=10)  # 対象週（yyyymmdd形式）
    name = models.CharField(max_length=255)
    price_A = models.DecimalField(max_digits=10, decimal_places=0, default=0)
    price_B = models.DecimalField(max_digits=10, decimal_places=0, default=0)
    price_C = models.DecimalField(max_digits=10, decimal_places=0, default=0)
    container_type = models.CharField(max_length=255, default='黒容器')  # デフォルト値を追加

    def __str__(self):
        return f"{self.name}"


class ItemQuantity(models.Model):
    target_date =  models.CharField(max_length=10, default='00000000')  # 対象日付（yyyymmdd形式）
    target_week =  models.CharField(max_length=10, default='00000000')  # 対象週（yyyymmdd形式）
    product = models.ForeignKey(Product, on_delete=models.CASCADE)
    sales_location = models.ForeignKey(SalesLocation, on_delete=models.CASCADE)
    quantity = models.IntegerField()

    def __str__(self):
        return f"{self.target_date} - {self.sales_location} - {self.product} - {self.quantity}"

# 日計表のメインモデル
class DailyReport(models.Model):
    date = models.DateField()  # 日付
    location = models.CharField(max_length=100) #models.ForeignKey(SalesLocation, on_delete=models.CASCADE)  # 販売場所
    location_no = models.IntegerField(default=0)#販売場所NO locationモデルのNOを取得
    person_in_charge = models.CharField(max_length=100, null=True, blank=True)  # null と blank が許可されているか
    weather = models.CharField(max_length=50, blank=True)  # 天気
    temp = models.CharField(max_length=50, blank=True)  # 体感気温
    total_quantity = models.DecimalField(max_digits=10, decimal_places=0, default=0)  # 持参数合計
    total_sales_quantity = models.DecimalField(max_digits=10, decimal_places=0, default=0)  # 販売数合計
    total_remaining = models.DecimalField(max_digits=10, decimal_places=0, default=0)  # 残数合計
    others_sales_1 = models.CharField(max_length=100, default="", blank=True)
    others_price1 = models.DecimalField(max_digits=10, decimal_places=0, default=0)
    others_sales_quantity1 = models.DecimalField(max_digits=10, decimal_places=0, default=0)
    others_sales_2 = models.CharField(max_length=100, default="", blank=True)
    others_price2 = models.DecimalField(max_digits=10, decimal_places=0, default=0)
    others_sales_quantity2 = models.DecimalField(max_digits=10, decimal_places=0, default=0)
    total_others_sales = models.DecimalField(max_digits=10, decimal_places=0, default=0)
    total_revenue = models.DecimalField(max_digits=10, decimal_places=0)  # 総売上
    no_rice_quantity = models.DecimalField(max_digits=10, decimal_places=0, default=0) # ご飯なし
    extra_rice_quantity = models.DecimalField(max_digits=10, decimal_places=0, default=0) # ご飯追加
    coupon_type_600 = models.DecimalField(max_digits=10, decimal_places=0, default=0) # クーポン600
    coupon_type_700 = models.DecimalField(max_digits=10, decimal_places=0, default=0) # クーポン700
    discount_50 = models.DecimalField(max_digits=10, decimal_places=0, default=0) # 割引・返金50
    discount_100 = models.DecimalField(max_digits=10, decimal_places=0, default=0) # 割引・返金100
    service_name = models.CharField(max_length=100, default="") # サービス名
    service_price = models.DecimalField(max_digits=10, decimal_places=0, default=0) # サービス価格
    service_type_600 = models.DecimalField(max_digits=10, decimal_places=0, default=0)
    service_type_700 = models.DecimalField(max_digits=10, decimal_places=0, default=0)
    service_type_100 = models.DecimalField(max_digits=10, decimal_places=0, default=0)
    total_discount = models.DecimalField(max_digits=10, decimal_places=0, default=0)  # 割引合計
    paypay = models.DecimalField(max_digits=10, decimal_places=0, default=0)  # PayPayの売上
    digital_payment = models.DecimalField(max_digits=10, decimal_places=0, default=0)  # 電子決済の売上
    cash = models.DecimalField(max_digits=10, decimal_places=0, default=0)  # 現金の売上
    sales_difference = models.DecimalField(max_digits=10, decimal_places=0, default=0)  # 差額
    departure_time = models.TimeField(null=True, blank=True)  # 出発時間
    arrival_time = models.TimeField(null=True, blank=True)  # 到着時間
    opening_time = models.TimeField(null=True, blank=True)  # 開店時間
    sold_out_time = models.TimeField(null=True, blank=True)  # 完売時間
    closing_time = models.TimeField(null=True, blank=True)  # 閉店時間
    gasolin = models.DecimalField(max_digits=10, decimal_places=0, default=0) # ガソリン代
    highway = models.DecimalField(max_digits=10, decimal_places=0, default=0) # 高速代
    parking = models.DecimalField(max_digits=10, decimal_places=0, default=0) # 駐車場代
    part = models.DecimalField(max_digits=10, decimal_places=0, default=0) # パート代
    others = models.DecimalField(max_digits=10, decimal_places=0, default=0) # その他
    comments = models.TextField(blank=True)  # コメント
    food_count_setting = models.TextField(blank=True)  # 明日の食数設定
    confirmed = models.BooleanField(default=False)  # 確認済みかどうか
    updated_at = models.DateTimeField(auto_now=True) # 自動で更新日時を記録

    def __str__(self):
        return f"{self.date} - {self.location}"

# 商品ごとの日計表エントリモデル
class DailyReportEntry(models.Model):
    report = models.ForeignKey(DailyReport, related_name='entries', on_delete=models.CASCADE)  # 日計表への外部キー
    product = models.CharField(max_length=255)  ##ForeignKey(Product, on_delete=models.CASCADE)  # 商品への外部キー
    quantity = models.IntegerField()  # 持参数
    sales_quantity = models.IntegerField()  # 販売数
    remaining_number = models.IntegerField()  # 残数
    total_sales = models.DecimalField(max_digits=10, decimal_places=0)  # 売上
    sold_out = models.BooleanField(default=False)  # 完売かどうか
    popular = models.BooleanField(default=False)  # 人気商品かどうか
    unpopular = models.BooleanField(default=False)  # 不人気商品かどうか

    def __str__(self):
        return f"{self.report.date}"
