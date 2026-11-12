"""販売フィー計算書（#206）。売り場の会社に払う販売フィーを日計表から計算し、発行した記録を残す。

仕様: .company/engineering/harness/specs/lunchnetsale-販売フィー計算書-要件定義.md・画面設計.md
受注の請求書（orders.Invoice）とはお金の向き・元データ・相手が違うので別に持つ。
"""
import datetime

from django.conf import settings
from django.db import models
from django.db.models import Q

from sales.models import SalesLocation


def month_start(d):
    return d.replace(day=1)


class FeeDestination(models.Model):
    """計算書の宛先。販売場所に1対1でつける（販売場所のテーブルには列を足さない）。
    「出す」をOFFにしても宛名・率は残す（また出すときに入れ直さなくてよいように）。"""
    location = models.OneToOneField(SalesLocation, on_delete=models.CASCADE, related_name="fee_destination")
    enabled = models.BooleanField(default=False, verbose_name="計算書を出す")
    addressee = models.CharField(max_length=100, blank=True, default="", verbose_name="宛名")  # 「御中」は含めない
    include_service = models.BooleanField(default=True, verbose_name="サービス販売を売上に反映する")
    updated_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
                                   related_name="+")
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "計算書の宛先"
        verbose_name_plural = "計算書の宛先"

    def __str__(self):
        return f"{self.location.name} → {self.addressee}"

    def rate_on(self, month):
        """対象月の1日時点の手数料率（なければ None）。"""
        rate = self.rates.filter(start_month__lte=month_start(month)).order_by("-start_month").first()
        return rate.rate if rate else None

    def next_rate(self, today):
        return self.rates.filter(start_month__gt=month_start(today)).order_by("start_month").first()


class FeeRate(models.Model):
    """手数料率。開始月（月の1日）から適用する。月の途中で率が変わる計算は起こさない。
    開始前のものだけ取消できる。適用中・終了は直さない（間違えたら新しい開始月で登録し直す）。"""
    destination = models.ForeignKey(FeeDestination, on_delete=models.CASCADE, related_name="rates")
    start_month = models.DateField(verbose_name="開始月")
    rate = models.DecimalField(max_digits=4, decimal_places=1, verbose_name="手数料率（%）")
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
                                   related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-start_month"]
        constraints = [
            models.UniqueConstraint(fields=["destination", "start_month"], name="uniq_feerate_destination_month"),
        ]

    def __str__(self):
        return f"{self.start_month:%Y/%m}から {self.rate}%"


class FeeStatement(models.Model):
    """発行した計算書。発行時点の数字・宛名・発行元・担当をスナップショットで持つ。
    あとで日計表や設定を直しても発行済みは変わらない。直すときは取消→再発行。"""
    STATUS_ISSUED = "issued"
    STATUS_VOID = "void"
    # 支払済みは今は持たない。足すときは選択肢を増やすだけで済むよう文字列で持つ
    STATUS_CHOICES = [(STATUS_ISSUED, "発行済み"), (STATUS_VOID, "取消")]

    number = models.CharField(max_length=20, unique=True, verbose_name="計算書番号")
    month = models.DateField(verbose_name="対象月")  # 月の1日
    location = models.ForeignKey(SalesLocation, on_delete=models.PROTECT, related_name="fee_statements")
    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default=STATUS_ISSUED, db_index=True)
    issue_date = models.DateField(verbose_name="発行日")
    pay_date = models.DateField(verbose_name="支払日")

    # --- スナップショット ---
    location_name = models.CharField(max_length=255)
    addressee = models.CharField(max_length=100)
    rate = models.DecimalField(max_digits=4, decimal_places=1)
    include_service = models.BooleanField()
    total_sales = models.BigIntegerField()      # 当月総売上（税込）
    tax_excluded = models.BigIntegerField()     # 税抜金額
    tax_amount = models.BigIntegerField()       # 消費税額（8%）
    fee_excluded = models.BigIntegerField()     # 税抜手数料
    fee_tax = models.BigIntegerField()          # 手数料消費税額（10%）
    pay_amount = models.BigIntegerField()       # 当月お支払金額（税込）
    detail = models.JSONField(default=dict)     # 日別明細・列・内訳（fees.calc.MonthData.as_dict）
    issuer = models.JSONField(default=dict)     # 発行元（会社名・住所・登録番号・社印）
    staff_name = models.CharField(max_length=100, blank=True)

    issued_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
                                  related_name="+")
    issued_at = models.DateTimeField(auto_now_add=True)
    voided_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
                                  related_name="+")
    voided_at = models.DateTimeField(null=True, blank=True)
    void_reason = models.CharField(max_length=200, blank=True)
    # 再発行のとき、取消した計算書
    replaces = models.ForeignKey("self", on_delete=models.SET_NULL, null=True, blank=True, related_name="reissues")

    class Meta:
        ordering = ["-month", "location__no", "number"]
        constraints = [
            # 同じ月・同じ売り場に発行済みは1件だけ（二度押し・同時発行の二重発行をDBで止める）
            models.UniqueConstraint(fields=["month", "location"], condition=Q(status="issued"),
                                    name="uniq_feestatement_issued_month_location"),
        ]

    def __str__(self):
        return f"{self.number} {self.location_name}"

    @property
    def is_void(self):
        return self.status == self.STATUS_VOID

    @property
    def month_label(self):
        return f"{self.month.year}年{self.month.month}月分"

    @staticmethod
    def next_number(month):
        """FEE-YYYYMM-連番4桁。YYYYMM は対象月。取消した番号も数えるので使い回さない。"""
        prefix = f"FEE-{month:%Y%m}-"
        last = (FeeStatement.objects.filter(number__startswith=prefix)
                .order_by("-number").values_list("number", flat=True).first())
        seq = int(last.rsplit("-", 1)[1]) + 1 if last else 1
        return f"{prefix}{seq:04d}"


def default_pay_date(month):
    """支払日の既定：対象月の翌月10日（見本 10/30発行→11/10支払）。"""
    m = month_start(month)
    nxt = (m + datetime.timedelta(days=32)).replace(day=1)
    return nxt.replace(day=10)
