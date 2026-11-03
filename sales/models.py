# sales/models.py
import datetime
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
    WEATHER_AREA_CHOICES = [("tokyo", "東京"), ("yokohama", "横浜みなと"), ("shinyokohama", "新横浜")]
    weather_area = models.CharField(
        max_length=20, choices=WEATHER_AREA_CHOICES, default="tokyo", verbose_name="天気の地点",
        help_text="食数予測の雨の補正に使う天気予報の地点。横浜は都心と予報が違う日が多い。",
    )
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
    area = models.CharField(max_length=20, default="tokyo")  # sales.weather.WEATHER_AREAS のキー
    lunch_precip = models.FloatField()        # 昼11〜13時の予想降水量(mm)
    lunch_snow = models.FloatField(default=0)  # 同・降雪(cm)
    lunch_temp = models.FloatField(null=True, blank=True)
    lunch_feels = models.FloatField(null=True, blank=True)  # 体感温度

    class Meta:
        ordering = ["-fetched_at"]

    def __str__(self):
        return f"{self.target_date} 昼{self.lunch_precip}mm（{self.fetched_at:%m/%d %H:%M}取得）"


class QuantityDraft(models.Model):
    """D-2の夜に入れた振分表36行目（店ごとの数）の下書き。予測と並べて比べるためだけに使い、
    持参数（ItemQuantity）や19:30の未受信通知には使わない。同じ日付・店は最新で上書き。"""
    target_date = models.DateField(db_index=True)
    location = models.CharField(max_length=100)   # 店名（販売場所No.は振り直されるため名前で持つ）
    quantity = models.IntegerField()
    recorded_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["target_date", "location"], name="uniq_quantity_draft_date_location"),
        ]

    def __str__(self):
        return f"{self.target_date} {self.location} {self.quantity}（下書き）"


class ForecastSnapshot(models.Model):
    """食数予測の画面に出ていた数字の記録。画面は開くたびに計算し直すので、
    「決めた時点の予測」を残しておかないと後から本当の答え合わせができない。
    D-2の夜（20時）とD-1（15時）に、次の3営業日ぶんを店ごとに残す。"""
    made_at = models.DateTimeField(auto_now_add=True)
    target_date = models.DateField(db_index=True)
    location = models.CharField(max_length=100)    # 店名（販売場所No.は振り直されるため名前で持つ）
    pred = models.IntegerField()                   # 予測食数（雨の補正込み・画面の大きい数字）
    base = models.IntegerField()                   # 雨がない場合
    recent = models.IntegerField()                 # 直近5営業日の需要
    weekday_pct = models.IntegerField(default=0)
    trend_pct = models.IntegerField(null=True, blank=True)
    rain_level = models.CharField(max_length=10, blank=True)
    rain_pct = models.IntegerField(default=0)
    draft = models.IntegerField(null=True, blank=True)        # その時点の下書き
    comment_num = models.IntegerField(null=True, blank=True)  # その時点の現場コメントの数字

    class Meta:
        ordering = ["-made_at"]
        indexes = [models.Index(fields=["target_date", "location"])]

    def __str__(self):
        return f"{self.target_date} {self.location} 予測{self.pred}（{self.made_at:%m/%d %H:%M}）"


class StaffQuantityPlan(models.Model):
    """現場が日計表に書いた「明日以降の食数」。同じ店・同じ日は一番新しい記録を使う（古いものも残す）。"""
    target_date = models.DateField(db_index=True)
    location = models.CharField(max_length=100)           # 店名（販売場所No.は振り直されるため名前で持つ）
    qty_min = models.IntegerField(null=True, blank=True)
    qty_max = models.IntegerField(null=True, blank=True)  # 幅のときだけ。1つの数なら空
    kept = models.IntegerField(null=True, blank=True)     # 取置き
    rain_min = models.IntegerField(null=True, blank=True)
    rain_max = models.IntegerField(null=True, blank=True)
    report = models.ForeignKey("DailyReport", null=True, blank=True, on_delete=models.SET_NULL, related_name="quantity_plans")
    written_on = models.DateField()                       # 何日の日計表で書かれたか
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at", "-id"]
        indexes = [models.Index(fields=["target_date", "location"])]

    def __str__(self):
        return f"{self.target_date} {self.location} {self.qty_label}（{self.written_on}記入）"

    @staticmethod
    def _range(lo, hi):
        if lo is None and hi is None:
            return ""
        if hi is None or hi == lo:
            return f"{lo if lo is not None else hi}"
        return f"{lo}〜{hi}"

    @property
    def qty_label(self):
        return self._range(self.qty_min, self.qty_max)

    @property
    def rain_label(self):
        return self._range(self.rain_min, self.rain_max)

    @property
    def qty_mid(self):
        vals = [v for v in (self.qty_min, self.qty_max) if v is not None]
        return sum(vals) / len(vals) if vals else None


class StoreEvent(models.Model):
    """現場が書いた「明日以降の予定」。その日の食数予測に目印を出す。"""
    target_date = models.DateField(db_index=True)
    location = models.CharField(max_length=100)
    note = models.CharField(max_length=200)
    report = models.ForeignKey("DailyReport", null=True, blank=True, on_delete=models.SET_NULL, related_name="store_events")
    written_on = models.DateField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["target_date", "created_at"]

    def __str__(self):
        return f"{self.target_date} {self.location} {self.note}"


class DailyReportQuestionSetting(models.Model):
    """日計表で「いつもと違った日」に聞く基準と選択肢（設定画面で変える・1行だけ）。"""
    low_one_in = models.PositiveSmallIntegerField(default=10)   # その店で「N回に1回」しかない少なさで聞く
    early_before = models.TimeField(default=datetime.time(12, 20))            # この時刻より前に完売したら聞く
    choices = models.JSONField(default=dict, blank=True)        # {"indoor_low": [...], ...}。空なら既定の選択肢
    updated_at = models.DateTimeField(auto_now=True)

    @classmethod
    def load(cls):
        obj, _ = cls.objects.get_or_create(pk=1)
        return obj


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
    # --- 売場の様子（選択式・2026-10）。すべて空可：過去の日計表・締め・給与の処理に影響させない ---
    WEATHER_CHOICES = [("sunny", "晴れ"), ("cloudy", "くもり"), ("light_rain", "小雨"),
                       ("rain", "雨"), ("heavy_rain", "大雨"), ("snow", "雪")]
    FEELS_CHOICES = [("very_cold", "とても寒い"), ("cold", "寒い"), ("cool", "涼しい"),
                     ("comfortable", "ちょうどいい"), ("hot", "暑い"), ("very_hot", "とても暑い")]
    UNUSUAL_CHOICES = [("", "ふだんどおり"), ("low", "いつもより売れなかった"), ("early", "早く売り切れた")]
    weather_during_sales = models.CharField(max_length=20, choices=WEATHER_CHOICES, blank=True, default="")
    feels = models.CharField(max_length=20, choices=FEELS_CHOICES, blank=True, default="")
    lost_customers = models.IntegerField(null=True, blank=True)       # 完売後に買えなかったお客さん（だいたい）
    unusual_kind = models.CharField(max_length=10, choices=UNUSUAL_CHOICES, blank=True, default="")
    unusual_reasons = models.JSONField(default=list, blank=True)       # 選んだ「気づいたこと」（文字のまま残す）
    unusual_note = models.CharField(max_length=200, blank=True, default="")
    usual_sales = models.IntegerField(null=True, blank=True)           # 入力時点のその店のふだんの販売数
    staff_is_regular = models.BooleanField(null=True, blank=True)       # いつもの担当か（シフトから判定）
    auto_lunch_precip = models.FloatField(null=True, blank=True)       # 11〜13時の雨量(mm)・アプリが自動記録
    auto_lunch_temp = models.FloatField(null=True, blank=True)
    auto_lunch_feels = models.FloatField(null=True, blank=True)        # 体感温度（気温＋湿度＋風）
    auto_lunch_wind = models.FloatField(null=True, blank=True)         # 風速(m/s)
    auto_lunch_snow = models.FloatField(null=True, blank=True)         # 降雪量(cm)
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
    can_view_dashboard = models.BooleanField(default=False, verbose_name="実績分析")
    can_view_quest = models.BooleanField(default=False, verbose_name="ランチクエスト")
    can_view_attendance = models.BooleanField(default=False, verbose_name="勤怠アプリ")
    can_view_stamp = models.BooleanField(default=False, verbose_name="公式LINE（スタンプ）")
    # 価格表・割引設定・メニューデータベース。お金の設定なので、本部の担当者にだけ個別にONにする（staffでも自動ではONにしない）
    can_view_price_master = models.BooleanField(default=False, verbose_name="価格・メニュー設定")
    direct_return = models.BooleanField(default=False, verbose_name="直行直帰")
    shin_yokohama = models.BooleanField(default=False, verbose_name="新横浜")

    def __str__(self):
        return f"MenuPermission({self.user})"


class UserChangeLog(models.Model):
    """ユーザー管理での変更の記録。誰かが勝手に管理者になったり権限を広げたりしても気づけるように。"""
    ACTION_CHOICES = [
        ("create", "新規作成"),
        ("profile", "基本情報"),
        ("role", "区分"),
        ("permission", "権限"),
        ("assignment", "担当"),
        ("deactivate", "無効化"),
        ("activate", "有効に戻す"),
        ("password", "パスワード設定"),
        ("unlock", "ロック解除"),
    ]
    target = models.ForeignKey(User, on_delete=models.CASCADE, related_name="change_logs", verbose_name="対象")
    changed_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name="+",
                                   verbose_name="変更した人")
    action = models.CharField(max_length=20, choices=ACTION_CHOICES, verbose_name="操作")
    detail = models.CharField(max_length=255, blank=True, default="", verbose_name="内容")
    changed_at = models.DateTimeField(auto_now_add=True, verbose_name="日時")

    class Meta:
        ordering = ["-changed_at", "-id"]
        verbose_name = "ユーザー変更履歴"
        verbose_name_plural = "ユーザー変更履歴"

    def __str__(self):
        return f"{self.target} {self.get_action_display()} {self.detail}"


# ===== 直行直帰の勤務時間 =====
# 仕様: .company/engineering/harness/specs/lunchnetsale-直行直帰-勤務時間計算-要件定義.md §4

class DirectReturnWorkTime(models.Model):
    """売り場ごとの就業時間（開始日つき）。勤務日に効くのは、開始日がその日以前で一番新しい行。"""
    location = models.ForeignKey(
        SalesLocation, on_delete=models.CASCADE, related_name='direct_return_work_times', verbose_name="売り場"
    )
    start_time = models.TimeField(verbose_name="始業")
    end_time = models.TimeField(verbose_name="終業")
    effective_from = models.DateField(verbose_name="開始日")
    note = models.CharField(max_length=255, blank=True, default="", verbose_name="備考")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "直行直帰 就業時間"
        verbose_name_plural = "直行直帰 就業時間"
        ordering = ['location', '-effective_from']
        constraints = [
            models.UniqueConstraint(fields=['location', 'effective_from'], name='uniq_dr_work_time_per_day'),
        ]

    def __str__(self):
        return f"{self.location.name} {self.effective_from}から {self.start_time:%H:%M}〜{self.end_time:%H:%M}"


class DirectReturnExtraAttendance(models.Model):
    """日計表を送っていない出勤日（研修など）を管理者が足したもの。1人1日1件。"""
    REASON_TRAINING = 'training'
    REASON_NOT_SENT = 'not_sent'
    REASON_OTHER = 'other'
    REASON_CHOICES = [
        (REASON_TRAINING, '研修'),
        (REASON_NOT_SENT, '送信忘れ'),
        (REASON_OTHER, 'その他'),
    ]

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='direct_return_extras', verbose_name="スタッフ"
    )
    date = models.DateField(verbose_name="日付")
    location = models.ForeignKey(
        SalesLocation, on_delete=models.PROTECT, related_name='direct_return_extras', verbose_name="売り場"
    )
    reason = models.CharField(max_length=20, choices=REASON_CHOICES, default=REASON_TRAINING, verbose_name="区分")
    note = models.CharField(max_length=255, blank=True, default="", verbose_name="備考")
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='+', verbose_name="入力した管理者"
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "直行直帰 出勤追加"
        verbose_name_plural = "直行直帰 出勤追加"
        constraints = [
            models.UniqueConstraint(fields=['user', 'date'], name='uniq_dr_extra_per_user_day'),
        ]

    def __str__(self):
        return f"{self.user} {self.date} {self.location.name}（{self.get_reason_display()}）"


class DirectReturnDayAdjustment(models.Model):
    """日別の早出・遅刻・早退・残業（時間・0.25刻み）と備考。1人1日1件。"""
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='direct_return_adjustments', verbose_name="スタッフ"
    )
    date = models.DateField(verbose_name="日付")
    early_hours = models.DecimalField(max_digits=4, decimal_places=2, default=0, verbose_name="早出（時間）")
    late_hours = models.DecimalField(max_digits=4, decimal_places=2, default=0, verbose_name="遅刻（時間）")
    leave_early_hours = models.DecimalField(max_digits=4, decimal_places=2, default=0, verbose_name="早退（時間）")
    overtime_hours = models.DecimalField(max_digits=4, decimal_places=2, default=0, verbose_name="残業（時間）")
    note = models.CharField(max_length=255, blank=True, default="", verbose_name="備考")
    updated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='+', verbose_name="入力した管理者"
    )
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "直行直帰 日別の調整"
        verbose_name_plural = "直行直帰 日別の調整"
        constraints = [
            models.UniqueConstraint(fields=['user', 'date'], name='uniq_dr_adjustment_per_user_day'),
        ]

    def __str__(self):
        return f"{self.user} {self.date}"


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

class PricePattern(models.Model):
    """価格パターン（価格表の列）。本部が画面で追加・終了できるようにDBで持つ（2026-10-13 価格の一元化）。
    販売所・顧客・価格表のマスは code で持つので、code は作ったあと変えない。"""
    code = models.CharField(max_length=10, unique=True)
    name = models.CharField(max_length=30, unique=True, verbose_name="名前")
    sort_order = models.IntegerField(default=0, verbose_name="並び順")
    active = models.BooleanField(default=True, verbose_name="使用中")

    class Meta:
        ordering = ["sort_order", "id"]
        verbose_name = "価格パターン"
        verbose_name_plural = "価格パターン"

    def __str__(self):
        return self.name


class PriceRank(models.Model):
    """値段の種類（★特選・通常・お手頃・大盛り）。値段そのものは PriceTableCell が持つ。"""
    ROLE_CHOICES = [("star", "特選"), ("normal", "通常"), ("large", "大盛り")]

    name = models.CharField(max_length=50, unique=True, verbose_name="値段の種類")
    # 大盛りは弁当ではない。クーポン・サービスの欄を作るときに除くため。
    is_bento = models.BooleanField(default=True, verbose_name="弁当")
    sort_order = models.IntegerField(default=0, verbose_name="並び順")
    active = models.BooleanField(default=True, verbose_name="使用中")
    # 名前を変えても、種類のない古いメニューの判定（★→特選など）と新メニューの初期値が壊れないよう役割で引く。
    role = models.CharField(max_length=10, choices=ROLE_CHOICES, blank=True, default="")

    class Meta:
        ordering = ["sort_order", "id"]
        verbose_name = "値段の種類"
        verbose_name_plural = "値段の種類"

    def __str__(self):
        return self.name


class PriceTable(models.Model):
    """価格表の版。ある日の値段は「その日に始まっている一番新しい版」で決まる。
    版は丸ごと1枚で持つ（本部の人が「10/1からの価格表」を1枚で見られるように）。"""
    valid_from = models.DateField(verbose_name="開始日")
    # 空＝販売所用（列＝価格パターン）。顧客あり＝その顧客だけの価格表（列は1つ、マスの pattern は ""）。
    customer = models.ForeignKey(
        "orders.Customer", on_delete=models.PROTECT, null=True, blank=True,
        related_name="price_tables", verbose_name="顧客",
    )
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
        constraints = [
            models.UniqueConstraint(fields=["valid_from"], condition=models.Q(customer__isnull=True),
                                    name="uniq_pricetable_shared_valid_from"),
            models.UniqueConstraint(fields=["customer", "valid_from"], condition=models.Q(customer__isnull=False),
                                    name="uniq_pricetable_customer_valid_from"),
        ]

    def __str__(self):
        who = f"{self.customer} " if self.customer_id else ""
        return f"{who}{self.valid_from:%Y-%m-%d}〜 {self.note}"


class PriceTableCell(models.Model):
    table = models.ForeignKey(PriceTable, on_delete=models.CASCADE, related_name="cells")
    rank = models.ForeignKey(PriceRank, on_delete=models.PROTECT, related_name="cells")
    pattern = models.CharField(max_length=10, blank=True)   # PricePattern.code。顧客別価格表は ""
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
    KIND_CHOICES = [("price_table", "価格表"), ("discount", "割引設定"),
                    ("customer_price", "顧客別の価格"), ("price_master", "値段の種類・価格パターン")]

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


# ===== メニューデータベース（S4） =====
# 元のメニュー表（Excel）には値段も容器も書かれていないので、メニュー名ごとに「値段の種類」と「容器」を覚える。
CONTAINER_CHOICES = ["赤容器", "黒容器", "一体型", "ご飯容器"]


class MenuProfile(models.Model):
    """メニューデータベース（メニュー名で1行）。1回直せば、次にこの名前が出た週から自動で決まる。"""
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
        verbose_name = "メニューデータベース"
        verbose_name_plural = "メニューデータベース"

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


# 評価ごとに決めるときの条件に使える指標。(名前, 単位, 小数の桁, 上限)。販売数・持参数・残数・売上金額は期間の合計
RULE_METRICS = {
    "avg_sales": ("1日平均", "食", 1, 999.9),
    "waste_rate": ("廃棄率", "%", 1, 100),
    "sales": ("販売数", "食", 0, 99999),
    "quantity": ("持参数", "食", 0, 99999),
    "remaining": ("残数", "食", 0, 99999),
    "revenue": ("売上金額", "円", 0, 9999999),
    "days": ("稼働日数", "日", 0, 366),
}
# メニューには売上金額がない。稼働日数は提供日数と呼ぶ
RULE_METRIC_KEYS = {
    "location": ["avg_sales", "waste_rate", "sales", "quantity", "remaining", "revenue", "days"],
    "menu": ["avg_sales", "waste_rate", "sales", "quantity", "remaining", "days"],
}
RULE_OPS = {"gte": "以上", "gt": "超", "lte": "以下", "lt": "未満"}
# 評価のバッジの色。値は Bootstrap の色名（bg-*-subtle 等）
RULE_COLOR_CHOICES = [("success", "緑"), ("info", "水色"), ("primary", "青"), ("warning", "黄"), ("danger", "赤"), ("secondary", "グレー")]
RULE_MAX = 8
RULE_COND_MAX = 3
RULE_LABEL_MAX = 8
OTHER_RULE_LABEL = "様子見"  # どの評価にも当てはまらないもの。名前は固定
# 4つ固定だったころの評価の色（保存済みの条件を読み替えるとき・初期値に使う）
LEGACY_RULE_COLORS = {
    "優良": "success", "人気": "success", "持参を減らす": "warning", "作りすぎ": "warning",
    "テコ入れ": "info", "増やせる": "info", "撤退検討": "danger", "不人気": "danger",
}


def metric_name(metric, target):
    if metric == "days" and target == "menu":
        return "提供日数"
    return RULE_METRICS[metric][0]


def normalize_rule(rule):
    """評価ごとの条件を {"label", "color", "conds": [{"metric", "op", "value"}]} にそろえる。
    4つ固定だったころの形（avg_op/waste_op）で保存された条件もこの形に読み替える。"""
    if "conds" in rule:
        return rule
    conds = []
    if rule.get("avg_op"):
        conds.append({"metric": "avg_sales", "op": rule["avg_op"], "value": float(rule["avg_value"])})
    if rule.get("waste_op"):
        conds.append({"metric": "waste_rate", "op": rule["waste_op"], "value": float(rule["waste_value"])})
    return {"label": rule["label"], "color": rule.get("color") or LEGACY_RULE_COLORS.get(rule["label"], "secondary"), "conds": conds}


def cond_text(cond, target="location"):
    unit, digits = RULE_METRICS[cond["metric"]][1], RULE_METRICS[cond["metric"]][2]
    return f"{metric_name(cond['metric'], target)}{float(cond['value']):,.{digits}f}{unit}{RULE_OPS[cond['op']]}"


def rule_text(rule, target="location"):
    """評価ごとの条件を1行に。例「優良 1日平均25.0食以上・廃棄率10.0%以下」"""
    rule = normalize_rule(rule)
    return f"{rule['label']} " + "・".join(cond_text(c, target) for c in rule["conds"])


class AnalysisBasisSetting(models.Model):
    """実績分析の4象限評価の基準。売り場用・メニュー用で1行ずつ。行が無ければ既定値（中央値・売り場全体・3日）で評価する。"""
    TARGET_CHOICES = [("location", "売り場"), ("menu", "メニュー")]
    AVG_MODE_CHOICES = [("median", "中央値"), ("mean", "平均値"), ("fixed", "固定")]
    WASTE_MODE_CHOICES = [("store", "売り場全体"), ("total", "全体（受注を含む）"), ("fixed", "固定")]
    RULE_MODE_CHOICES = [("lines", "2本の線で分ける"), ("rules", "評価ごとに決める")]
    COLOR_MODE_CHOICES = [("basis", "基準と比べる"), ("bands", "数値で決める")]
    # 色は見分けやすいものに絞る（自由に選ぶと読みにくい色が混ざる）。値は Bootstrap の文字色クラス
    COLOR_CHOICES = [("danger", "赤"), ("orange", "オレンジ"), ("primary", "青"), ("success", "緑"), ("secondary", "グレー")]

    target = models.CharField(max_length=10, choices=TARGET_CHOICES, unique=True, verbose_name="対象")
    rule_mode = models.CharField(max_length=10, choices=RULE_MODE_CHOICES, default="lines", verbose_name="評価の決め方")
    # 評価ごとに決めるときの評価と条件。判定順に [{"label", "color", "conds": [{"metric", "op", "value"}]}]
    # 旧形式 [{"label", "avg_op", "avg_value", "waste_op", "waste_value"}] も normalize_rule で読み替える
    rules = models.JSONField(default=list, blank=True, verbose_name="評価ごとの条件")
    waste_color_mode = models.CharField(max_length=10, choices=COLOR_MODE_CHOICES, default="basis", verbose_name="廃棄率の色の決め方")
    # 数値で決めるときの段。高い順に [{"min": 20.0, "color": "danger", "bold": true}]。いちばん高く当てはまった段の色
    waste_bands = models.JSONField(default=list, blank=True, verbose_name="廃棄率の色の段")
    avg_mode = models.CharField(max_length=10, choices=AVG_MODE_CHOICES, default="median", verbose_name="1日平均の基準")
    avg_fixed = models.DecimalField(max_digits=6, decimal_places=1, null=True, blank=True, verbose_name="1日平均の固定値")
    waste_mode = models.CharField(max_length=10, choices=WASTE_MODE_CHOICES, default="store", verbose_name="廃棄率の基準")
    waste_fixed = models.DecimalField(max_digits=4, decimal_places=1, null=True, blank=True, verbose_name="廃棄率の固定値")
    min_days = models.PositiveSmallIntegerField(default=3, verbose_name="対象外にする日数")
    updated_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name="+", verbose_name="更新した人")
    updated_at = models.DateTimeField(auto_now=True, verbose_name="更新日時")

    class Meta:
        verbose_name = "実績分析の評価基準"
        verbose_name_plural = "実績分析の評価基準"

    def __str__(self):
        return self.get_target_display()

    def color_summary(self):
        if self.waste_color_mode != "bands":
            return "色：基準と比べる"
        names = dict(self.COLOR_CHOICES)
        return "色：" + "・".join(f"{b['min']:.1f}%以上{names[b['color']]}{'太字' if b['bold'] else ''}" for b in self.waste_bands)

    def normalized_rules(self):
        return [normalize_rule(r) for r in self.rules or []]

    def summary(self):
        return f"{self._rule_summary()}／{self.color_summary()}"

    def _rule_summary(self):
        if self.rule_mode == "rules":
            colors = dict(RULE_COLOR_CHOICES)
            return "評価ごと：" + "／".join(f"{rule_text(r, self.target)}（{colors.get(r['color'], r['color'])}）"
                                       for r in self.normalized_rules()) + f"／{self.min_days}日未満は対象外"
        avg = f"固定 {self.avg_fixed:.1f}食" if self.avg_mode == "fixed" else self.get_avg_mode_display()
        waste = f"固定 {self.waste_fixed:.1f}%" if self.waste_mode == "fixed" else self.get_waste_mode_display()
        return f"1日平均 {avg}／廃棄率 {waste}／{self.min_days}日未満は対象外"


class AnalysisBasisLog(models.Model):
    """評価基準の変更履歴。全員の評価が変わる設定なので、いつ誰が何をどう変えたか残す。"""
    target = models.CharField(max_length=10, choices=AnalysisBasisSetting.TARGET_CHOICES, verbose_name="対象")
    # 評価が最大8つ×条件3つになり255字に収まらないので TextField
    before = models.TextField(verbose_name="変更前")
    after = models.TextField(verbose_name="変更後")
    changed_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name="+", verbose_name="変更した人")
    changed_at = models.DateTimeField(auto_now_add=True, verbose_name="日時")

    class Meta:
        ordering = ["-changed_at", "-id"]
        verbose_name = "実績分析の評価基準の変更履歴"
        verbose_name_plural = "実績分析の評価基準の変更履歴"
