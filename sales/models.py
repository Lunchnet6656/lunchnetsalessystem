# sales/models.py
import secrets

from django.db import models
from django.contrib.auth.models import AbstractUser
from django.contrib.auth.models import User
from django.utils.timezone import now
from django.conf import settings


def _generate_qr_token():
    # 16バイト=128bitの URL-safe トークン（22文字）。拠点別QRコードに埋め込む固定値。
    return secrets.token_urlsafe(16)


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

class CarpoolRoute(models.Model):
    name = models.CharField(max_length=100, verbose_name="ルート名")
    departure_time = models.TimeField(verbose_name="出発時間")
    display_order = models.IntegerField(default=0, verbose_name="表示順")

    class Meta:
        ordering = ['display_order', 'name']
        verbose_name = "同乗ルート"
        verbose_name_plural = "同乗ルート"

    def __str__(self):
        return f"{self.name} ({self.departure_time:%H:%M})"


class SalesLocation(models.Model):
    no = models.IntegerField(default=0)
    name = models.CharField(max_length=255)
    type = models.CharField(max_length=100)
    price_type = models.CharField(max_length=100)
    service_name = models.CharField(max_length=100)
    service_price = models.DecimalField(max_digits=10, decimal_places=0, default=0)
    service_style = models.CharField(max_length=100, default="なし")
    direct_return = models.IntegerField(default=0)
    accepts_digital_payment = models.BooleanField(default=False, verbose_name="電子決済対応")
    expects_cash = models.BooleanField(
        default=True,
        verbose_name="現金入力あり",
        help_text="OFF の拠点（社員食堂・配達など現金を扱わない売り場）は、日計表の現金欄をグレーアウトし、未入力チェックの対象外にする。",
    )
    requires_drive = models.BooleanField(default=False, verbose_name="運転必須")
    priority = models.CharField(max_length=1, choices=[("S","S"),("A","A"),("B","B")], default="A", verbose_name="優先度")
    excluded_from_shift = models.BooleanField(default=False, verbose_name="シフト対象外")
    excluded_from_public_status = models.BooleanField(
        default=False,
        verbose_name="出店状況ページ非表示",
        help_text="お客様向け公開ページ（status.lunchnetsalessystem.com）に出さない拠点。配達など、社内シフトには残すが対外的に出店ではないもの。",
    )
    carpool_route = models.ForeignKey(
        'CarpoolRoute', on_delete=models.SET_NULL,
        null=True, blank=True, related_name='locations',
        verbose_name="同乗ルート",
    )

    # --- 出店状況ページ「本日の上書き」（QRコード由来）---
    # generate_status_json は today_override_date == today の場合のみ today_override を採用する。
    # 8:00 の Scheduler 実行時に publish_status_json が stale な override を全クリアする。
    TODAY_OVERRIDE_CHOICES = [
        ("", "なし"),
        ("sold_out", "完売"),
        ("closed", "お休み"),
    ]
    today_override = models.CharField(
        max_length=10, choices=TODAY_OVERRIDE_CHOICES, default="", blank=True,
        verbose_name="本日の上書き",
        help_text="QRコードから設定される今日のステータス上書き。空＝通常表示。",
    )
    today_override_date = models.DateField(
        null=True, blank=True,
        verbose_name="本日の上書き設定日",
        help_text="today_override を設定したJST日付。今日と一致する場合のみ有効。",
    )
    qr_sold_out_token = models.CharField(
        max_length=32, unique=True, db_index=True, default=_generate_qr_token,
        verbose_name="完売QRトークン",
    )
    qr_closed_token = models.CharField(
        max_length=32, unique=True, db_index=True, default=_generate_qr_token,
        verbose_name="急休みQRトークン",
    )
    qr_enabled = models.BooleanField(
        default=False,
        verbose_name="QR有効",
        help_text="OFF のときは QR スキャンしても上書きを受け付けない（パイロット運用用のキルスイッチ）。",
    )
    last_qr_publish_at = models.DateTimeField(
        null=True, blank=True,
        verbose_name="最終QR起因publish時刻",
        help_text="QR起因で Cloudflare publish した最終時刻。60秒スロットルの判定に使う。",
    )

    # --- 予約システム（個人セルフ取り置き）---
    # 仕様: .company/engineering/harness/specs/w001-予約システム-要件定義.md
    reservation_enabled = models.BooleanField(
        default=False,
        verbose_name="予約受付",
        help_text="ON の拠点だけセルフ予約を受け付ける。テスト店舗だけ ON にするキルスイッチ。",
    )
    qr_reserve_token = models.CharField(
        max_length=32, unique=True, db_index=True, default=_generate_qr_token,
        verbose_name="予約QRトークン",
    )
    default_product_cap = models.IntegerField(
        default=10,
        verbose_name="1メニューあたりの予約上限/日",
        help_text="1メニューを1日に何個まで予約で確保できるか。既定10。",
    )

    # --- スタンプカード（来店計測）---
    # 仕様: .company/engineering/harness/specs/lunchnetsale-スタンプカード来店計測-MVP.md
    # 既存のQRトークンの仕組み（_generate_qr_token）と同じパターンでスタンプ用トークンを持つ。
    stamp_enabled = models.BooleanField(
        default=False,
        verbose_name="スタンプ受付",
        help_text="ON の店舗だけスタンプを受け付ける。パイロット店舗だけ ON にするキルスイッチ。",
    )
    qr_stamp_token = models.CharField(
        max_length=32, unique=True, db_index=True, default=_generate_qr_token,
        verbose_name="スタンプQRトークン",
    )
    stamp_open_time = models.TimeField(
        null=True, blank=True,
        verbose_name="スタンプ出店開始時刻",
        help_text="空＝全店共通の既定（11:00）。本店など製造中にも客が来る店舗は個別に早めに設定。",
    )
    stamp_close_time = models.TimeField(
        null=True, blank=True,
        verbose_name="スタンプ出店終了時刻",
        help_text="空＝全店共通の既定（13:30）。",
    )

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
    # 値段の種類。入っていれば価格表から日付で値段を引く（週の途中の値上げに対応するため）。
    # 空の古いメニューは price_A/B/C をそのまま使う。
    rank = models.ForeignKey(
        'PriceRank', on_delete=models.PROTECT, null=True, blank=True,
        related_name='products', verbose_name="値段の種類",
    )

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
    
class ItemQuantityUpload(models.Model):
    """持参数の受信記録。19:30の未受信通知と、画面の「最終受信」表示に使う。"""
    SOURCE_CHOICES = [("api", "振分表の確定ボタン"), ("screen", "画面アップロード")]

    target_date = models.DateField(null=True, blank=True)
    received_at = models.DateTimeField(auto_now_add=True)
    source = models.CharField(max_length=10, choices=SOURCE_CHOICES)
    ok = models.BooleanField(default=False)
    saved_count = models.IntegerField(default=0)
    errors = models.JSONField(default=list, blank=True)

    class Meta:
        ordering = ["-received_at", "-id"]

    def __str__(self):
        return f"{self.target_date} {self.get_source_display()} {'OK' if self.ok else 'NG'}"


class WeatherForecastSnapshot(models.Model):
    """決めた時点の昼の天気予報。過去の予報は後から取れないので、検証のために毎日残す。"""
    fetched_at = models.DateTimeField(auto_now_add=True)
    target_date = models.DateField(db_index=True)
    lunch_precip = models.FloatField()        # 昼11〜13時の予想降水量(mm)
    lunch_snow = models.FloatField(default=0)  # 同・降雪(cm)
    lunch_temp = models.FloatField(null=True, blank=True)
    lunch_feels = models.FloatField(null=True, blank=True)  # 体感温度

    class Meta:
        ordering = ["-fetched_at"]

    def __str__(self):
        return f"{self.target_date} 昼{self.lunch_precip}mm（{self.fetched_at:%m/%d %H:%M}取得）"


class OthersItem(models.Model):
    no = models.IntegerField(default=0)
    name =  models.CharField(max_length=255)
    price =  models.DecimalField(max_digits=10, decimal_places=0, default=0)

    def __str__(self):
        return f"{self.no} - {self.name} - {self.price}"


# 日計表のメインモデル
class DailyReport(models.Model):
    date = models.DateField()  # 日付
    location = models.CharField(max_length=100) #models.ForeignKey(SalesLocation, on_delete=models.CASCADE)  # 販売場所
    location_no = models.IntegerField(default=0)#販売場所NO locationモデルのNOを取得
    person_in_charge = models.CharField(max_length=100, null=True, blank=True)  # null と blank が許可されているか
    weather = models.CharField(max_length=50, blank=True)  # 天気
    temp = models.CharField(max_length=50, blank=True)  # 体感気温
    total_quantity = models.DecimalField(max_digits=10, decimal_places=0, default=0)  # 持参数合計
    sales_price_quantity_1 = models.DecimalField(max_digits=10, decimal_places=0, default=0) # 単価別販売数1
    sales_price_quantity_2 = models.DecimalField(max_digits=10, decimal_places=0, default=0) # 単価別販売数2
    sales_price_quantity_3 = models.DecimalField(max_digits=10, decimal_places=0, default=0) # 単価別販売数3
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
    coupon_type_600 = models.DecimalField(max_digits=10, decimal_places=0, default=0) # クーポン650（名前は旧600のまま・実単価650）
    coupon_type_700 = models.DecimalField(max_digits=10, decimal_places=0, default=0) # クーポン700
    coupon_type_750 = models.DecimalField(max_digits=10, decimal_places=0, default=0) # クーポン750（2026年10月〜）
    discount_50 = models.DecimalField(max_digits=10, decimal_places=0, default=0) # 割引・返金50
    discount_100 = models.DecimalField(max_digits=10, decimal_places=0, default=0) # 割引・返金100
    service_name = models.CharField(max_length=100, default="") # サービス名
    service_price = models.DecimalField(max_digits=10, decimal_places=0, default=0) # サービス価格
    service_type_600 = models.DecimalField(max_digits=10, decimal_places=0, default=0)
    service_type_700 = models.DecimalField(max_digits=10, decimal_places=0, default=0)
    service_type_750 = models.DecimalField(max_digits=10, decimal_places=0, default=0)  # サービス750（2026年10月〜）
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
    submitted_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name='submitted_reports'
    )
    updated_at = models.DateTimeField(auto_now=True) # 自動で更新日時を記録

    class Meta:
        # 同日・同売場は1件のみ。再送信が速すぎて起きる競合（同時POST）による
        # 重複行をDBレベルで防ぐ。制約があることで update_or_create が
        # 「作成失敗→既存を更新」に自動フォールバックし、根本的に競合安全になる。
        constraints = [
            models.UniqueConstraint(
                fields=['date', 'location'],
                name='uniq_dailyreport_date_location',
            ),
        ]

    def __str__(self):
        return f"{self.date} - {self.location}"

# 商品ごとの日計表エントリモデル
class DailyReportEntry(models.Model):
    report = models.ForeignKey(DailyReport, related_name='entries', on_delete=models.CASCADE)  # 日計表への外部キー
    product_no = models.IntegerField(default=0)
    product = models.CharField(max_length=255)  ##ForeignKey(Product, on_delete=models.CASCADE)  # 商品への外部キー
    quantity = models.IntegerField()  # 持参数
    sales_quantity = models.IntegerField()  # 販売数
    remaining_number = models.IntegerField()  # 残数
    total_sales = models.DecimalField(max_digits=10, decimal_places=0)  # 売上
    # 保存した時点の販売単価。あとで値上げしても過去の日計表の金額が変わらないように残す。
    # 空＝移行前の古い記録（backfill_pricing で埋める）。
    unit_price = models.DecimalField(max_digits=10, decimal_places=0, null=True, blank=True)
    sold_out = models.BooleanField(default=False)  # 完売かどうか
    popular = models.BooleanField(default=False)  # 人気商品かどうか
    unpopular = models.BooleanField(default=False)  # 不人気商品かどうか

    class Meta:
        ordering = ['product_no']
        unique_together = [('report', 'product_no')]

    def __str__(self):
        return f"{self.report.date}"

class Holiday(models.Model):
    date = models.DateField(unique=True)
    description = models.CharField(max_length=100, blank=True, null=True)

    def __str__(self):
        return f"{self.date} ({self.description})"

class ShiftRequest(models.Model):
    user = models.ForeignKey(User, on_delete=models.CASCADE)  # ユーザー情報
    last_name = models.CharField(max_length=50, blank=True)  # 姓
    first_name = models.CharField(max_length=50, blank=True)  # 名
    date = models.DateField()  # シフト希望日
    is_off = models.BooleanField(default=False)  # チェックボックス：Trueならお休み希望
    submitted_at = models.DateTimeField(auto_now_add=True)  # 送信日時
    comment = models.TextField(blank=True)  # 伝言事項フィールド（追加）

    def save(self, *args, **kwargs):
        """ ユーザーの名前を保存時に自動でセット """
        if self.user:
            self.last_name = self.user.last_name
            self.first_name = self.user.first_name
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.last_name} {self.first_name} - {self.date} ({'休み' if self.is_off else '出勤'})"
    
    class Meta:
        unique_together = ('user', 'date')  # 同じユーザーが同じ日付のシフトを複数回送信できない


class UserMenuPermission(models.Model):
    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='menu_permission'
    )
    can_view_upload = models.BooleanField(default=False, verbose_name="データアップロード")
    can_view_register = models.BooleanField(default=False, verbose_name="新規ユーザー登録")
    can_view_mypage = models.BooleanField(default=False, verbose_name="マイページ")
    can_view_user_list = models.BooleanField(default=False, verbose_name="ユーザー管理")
    can_view_location_list = models.BooleanField(default=False, verbose_name="販売場所データ確認")
    can_view_product_list = models.BooleanField(default=False, verbose_name="メニューデータ確認")
    can_view_item_quantity = models.BooleanField(default=False, verbose_name="持参数データ確認")
    can_view_others_item = models.BooleanField(default=False, verbose_name="その他の項目設定")
    can_view_direct_return_attendance = models.BooleanField(default=False, verbose_name="直行直帰出勤記録")
    can_view_daily_report_list = models.BooleanField(default=False, verbose_name="日計表 送信結果（管理者）")
    can_view_performance_data = models.BooleanField(default=False, verbose_name="日別実績集計データ")
    can_view_performance_by_location = models.BooleanField(default=False, verbose_name="販売場所別実績（管理者）")
    can_view_menu_history = models.BooleanField(default=False, verbose_name="メニュー別販売履歴")
    can_view_daily_report_rol = models.BooleanField(default=True, verbose_name="日計表 送信結果（一般）")
    can_view_performance_by_location_rol = models.BooleanField(default=True, verbose_name="販売場所別実績データ（一般）")
    can_view_daily_report_form = models.BooleanField(default=True, verbose_name="日計表入力フォーム")
    can_view_shift_app = models.BooleanField(default=True, verbose_name="シフトアプリ")
    can_view_orders = models.BooleanField(default=False, verbose_name="受注管理")
    can_view_dashboard = models.BooleanField(default=False, verbose_name="売上ダッシュボード")
    can_view_quest = models.BooleanField(default=False, verbose_name="ランチクエスト")
    can_view_attendance = models.BooleanField(default=False, verbose_name="勤怠アプリ")
    can_view_stamp = models.BooleanField(default=False, verbose_name="公式LINE（スタンプ）")
    # 価格表・割引設定・メニュー辞書。お金の設定なので、本部の担当者にだけ個別にONにする（staffでも自動ではONにしない）
    can_view_price_master = models.BooleanField(default=False, verbose_name="価格・メニュー設定")
    direct_return = models.BooleanField(default=False, verbose_name="直行直帰")
    shin_yokohama = models.BooleanField(default=False, verbose_name="新横浜")

    def __str__(self):
        return f"MenuPermission({self.user})"


class CustomStamp(models.Model):
    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='custom_stamps'
    )
    name = models.CharField(max_length=50, verbose_name='スタンプ名')
    image_data = models.TextField(verbose_name='画像データ(base64 data URI)')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['created_at']
        verbose_name = 'カスタムスタンプ'
        verbose_name_plural = 'カスタムスタンプ'

    def __str__(self):
        return f"{self.owner} - {self.name}"


class ReportMessage(models.Model):
    FIELD_CHOICES = [
        ('comments', 'コメント'),
        ('food_count_setting', '明日の食数設定'),
    ]
    TYPE_CHOICES = [
        ('text', 'テキスト'),
        ('reaction', 'リアクション'),
        ('stamp', 'スタンプ'),
    ]
    EMOJI_CHOICES = ['👍', '❤️', '😊', '👏', '🎉']

    report = models.ForeignKey(
        'DailyReport', on_delete=models.CASCADE, related_name='messages'
    )
    admin_user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE,
        related_name='admin_messages'
    )
    sender = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE,
        related_name='sent_report_messages'
    )
    sender_role = models.CharField(
        max_length=10, choices=[('admin', '管理者'), ('employee', 'スタッフ')]
    )
    field_target = models.CharField(
        max_length=30, choices=FIELD_CHOICES, default='comments'
    )
    message_type = models.CharField(
        max_length=10, choices=TYPE_CHOICES, default='text'
    )
    body = models.TextField(blank=True)
    emoji = models.CharField(max_length=10, blank=True)
    stamp = models.ForeignKey(
        'CustomStamp', on_delete=models.SET_NULL, null=True, blank=True,
        related_name='messages'
    )
    parent = models.ForeignKey(
        'self', on_delete=models.CASCADE, null=True, blank=True,
        related_name='replies'
    )
    is_read = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['created_at']
        verbose_name = 'レポートメッセージ'
        verbose_name_plural = 'レポートメッセージ'

    def __str__(self):
        return f"[{self.sender_role}] {self.report} / {self.field_target} ({self.created_at:%Y-%m-%d %H:%M})"


# ===== 価格と割引のマスタ =====
# 仕様: .company/engineering/harness/specs/lunchnetsale-価格と割引のマスタ化-要件定義.md
# 値上げのたびにコードやExcelを直さずに済むよう、値段と割引の金額は日付つきでDBに持つ。

# 販売所の価格パターン。D価格を足すときはここと SalesLocation の運用だけを直せば済むよう1か所に置く。
PRICE_PATTERNS = ("A", "B", "C")


class PriceRank(models.Model):
    """値段の種類（★特選・通常・お手頃・大盛り）。値段そのものは PriceTableCell が持つ。"""
    name = models.CharField(max_length=50, unique=True, verbose_name="値段の種類")
    # 大盛りは弁当ではない。クーポン・サービスの欄を作るときに除くため。
    is_bento = models.BooleanField(default=True, verbose_name="弁当")
    sort_order = models.IntegerField(default=0, verbose_name="並び順")

    class Meta:
        ordering = ["sort_order", "id"]
        verbose_name = "値段の種類"
        verbose_name_plural = "値段の種類"

    def __str__(self):
        return self.name


class PriceTable(models.Model):
    """価格表の版。ある日の値段は「その日に始まっている一番新しい版」で決まる。
    版は丸ごと1枚で持つ（本部の人が「10/1からの価格表」を1枚で見られるように）。"""
    valid_from = models.DateField(unique=True, verbose_name="開始日")
    note = models.CharField(max_length=100, blank=True, verbose_name="メモ")
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name="+",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-valid_from"]
        verbose_name = "価格表"
        verbose_name_plural = "価格表"

    def __str__(self):
        return f"{self.valid_from:%Y-%m-%d}〜 {self.note}"


class PriceTableCell(models.Model):
    table = models.ForeignKey(PriceTable, on_delete=models.CASCADE, related_name="cells")
    rank = models.ForeignKey(PriceRank, on_delete=models.PROTECT, related_name="cells")
    pattern = models.CharField(max_length=10, choices=[(p, f"価格{p}") for p in PRICE_PATTERNS])
    price = models.PositiveIntegerField()

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["table", "rank", "pattern"], name="uniq_pricecell_table_rank_pattern"),
        ]

    def __str__(self):
        return f"{self.table.valid_from:%Y-%m-%d} {self.rank} {self.pattern}={self.price}"


class DiscountItem(models.Model):
    """日計表の割引項目のうち、弁当の値段から決まらないもの（ご飯・割引返金）。
    金額は書き換えず、値上げは「前日で終了＋新しい項目を開始」で表す（いつからいくらかを残すため）。"""
    GROUP_CHOICES = [("rice", "ご飯"), ("refund", "割引・返金")]
    DIRECTION_CHOICES = [("minus", "引く"), ("plus", "足す")]

    group = models.CharField(max_length=10, choices=GROUP_CHOICES)
    label = models.CharField(max_length=50, verbose_name="表示名")
    direction = models.CharField(max_length=5, choices=DIRECTION_CHOICES)
    amount = models.PositiveIntegerField(verbose_name="金額")
    sort_order = models.IntegerField(default=0)
    valid_from = models.DateField(verbose_name="開始日")
    valid_to = models.DateField(null=True, blank=True, verbose_name="終了日")
    # 移行期間に旧カラム（no_rice_quantity など）へも同じ数を書くための対応表。新しく足した項目は空。
    legacy_field = models.CharField(max_length=50, blank=True)
    csv_label = models.CharField(max_length=50, blank=True)
    updated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name="+",
    )
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["group", "sort_order", "valid_from"]
        verbose_name = "割引項目"
        verbose_name_plural = "割引項目"

    def __str__(self):
        return f"{self.get_group_display()} {self.label}（{self.valid_from:%Y-%m-%d}〜）"

    @property
    def unit_amount(self):
        return -self.amount if self.direction == "minus" else self.amount


class DailyReportDiscountLine(models.Model):
    """日計表1件の割引明細。保存した時点の名前と単価を残し、あとの値上げで過去の金額が変わらないようにする。"""
    GROUP_CHOICES = [
        ("rice", "ご飯"), ("refund", "割引・返金"), ("coupon", "クーポン"), ("service", "サービス販売"),
    ]

    report = models.ForeignKey(DailyReport, on_delete=models.CASCADE, related_name="discount_lines")
    group = models.CharField(max_length=10, choices=GROUP_CHOICES)
    # クーポン・サービスは弁当の値段から作るので項目を持たない（空）。
    item = models.ForeignKey(DiscountItem, on_delete=models.PROTECT, null=True, blank=True, related_name="lines")
    label = models.CharField(max_length=50)
    base_price = models.IntegerField(default=0)  # クーポン・サービスの元になった弁当の値段
    unit_amount = models.IntegerField()           # 1個あたりの割引額（符号付き）
    quantity = models.PositiveIntegerField(default=0)
    amount = models.IntegerField(default=0)

    class Meta:
        ordering = ["report", "group", "-base_price", "id"]
        constraints = [
            models.UniqueConstraint(fields=["report", "group", "label"], name="uniq_discountline_report_group_label"),
        ]

    def __str__(self):
        return f"{self.report} {self.label} ×{self.quantity}"


class PriceChangeLog(models.Model):
    """価格表・割引設定の変更履歴。お金の設定なので、だれが・いつ・何を変えたかを画面で追えるようにする。"""
    KIND_CHOICES = [("price_table", "価格表"), ("discount", "割引設定")]

    kind = models.CharField(max_length=20, choices=KIND_CHOICES)
    summary = models.TextField(verbose_name="内容")  # 確認画面の文章を要約したもの
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name="+",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at", "-id"]
        verbose_name = "価格・割引の変更履歴"
        verbose_name_plural = "価格・割引の変更履歴"

    def __str__(self):
        return f"{self.created_at:%Y-%m-%d %H:%M} {self.get_kind_display()} {self.summary}"


# ===== メニュー辞書（S4） =====
# 元のメニュー表（Excel）には値段も容器も書かれていないので、メニュー名ごとに「値段の種類」と「容器」を覚える。
CONTAINER_CHOICES = ["赤容器", "黒容器", "一体型", "ご飯容器"]


class MenuProfile(models.Model):
    """メニュー辞書（メニュー名で1行）。1回直せば、次にこの名前が出た週から自動で決まる。"""
    name = models.CharField(max_length=255, unique=True, verbose_name="メニュー名")
    rank = models.ForeignKey(PriceRank, on_delete=models.PROTECT, related_name="menu_profiles",
                             verbose_name="値段の種類")
    container = models.CharField(max_length=20, verbose_name="容器")
    # False＝判定ルールで仮に決めただけ（画面では「要確認」）
    confirmed = models.BooleanField(default=False, verbose_name="確認済み")
    first_seen = models.DateField(null=True, blank=True, verbose_name="初めて出た週")
    last_seen = models.DateField(null=True, blank=True, verbose_name="最後に出た週")
    updated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+",
    )
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["name"]
        verbose_name = "メニュー辞書"
        verbose_name_plural = "メニュー辞書"

    def __str__(self):
        return f"{self.name}（{self.rank}・{self.container}）"


class ClassifyRule(models.Model):
    """初めて見るメニューの仮の決め方。上から順に見て、種類と容器をそれぞれ最初に当たったルールで決める。"""
    keyword = models.CharField(max_length=50, verbose_name="メニュー名にこの言葉があれば")
    rank = models.ForeignKey(PriceRank, on_delete=models.PROTECT, null=True, blank=True,
                             related_name="+", verbose_name="値段の種類")  # 空＝種類は決めない
    container = models.CharField(max_length=20, blank=True, verbose_name="容器")  # 空＝容器は決めない
    sort_order = models.IntegerField(default=0)

    class Meta:
        ordering = ["sort_order", "id"]
        verbose_name = "判定ルール"
        verbose_name_plural = "判定ルール"

    def __str__(self):
        return f"{self.keyword} → {self.rank or '—'}・{self.container or '—'}"


class MenuWeekCheck(models.Model):
    """週のメニューの受信と確認の状態。送るたびに「未確認」に戻り、毎週必ず人が確かめる。"""
    week = models.DateField(unique=True, verbose_name="週（水曜）")
    received_at = models.DateTimeField(null=True, blank=True, verbose_name="届いた日時")
    received_count = models.IntegerField(default=0)
    confirmed_at = models.DateTimeField(null=True, blank=True, verbose_name="確認した日時")
    confirmed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+",
    )
    # 確認済みのあとで送り直された（画面で「もう一度確認してください」と出す）
    resent_after_confirm = models.BooleanField(default=False)

    class Meta:
        ordering = ["-week"]
        verbose_name = "週のメニュー確認"
        verbose_name_plural = "週のメニュー確認"

    @property
    def is_confirmed(self):
        return self.confirmed_at is not None

    def __str__(self):
        return f"{self.week:%Y-%m-%d}週 {'確認済み' if self.is_confirmed else '未確認'}"
