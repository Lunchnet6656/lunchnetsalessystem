"""勤怠管理アプリ（w002）のデータモデル。

スプリント1の範囲は、店舗・スタッフ・時給・打刻・給与設定のマスタと、
食堂スタッフのスマホ打刻まで。実働時間の集計や給与計算はスプリント2以降で扱う。

要件定義: .company/engineering/harness/specs/w002-勤怠管理給与計算-要件定義.md
スプリント契約: .company/engineering/harness/specs/w002-スプリント1-勤怠打刻とマスタ.md
"""
import uuid
from datetime import time
from decimal import Decimal

from django.conf import settings
from django.db import models
from django.db.models import Q
from django.utils import timezone

# 食堂事業／販売事業の2区分。値が2つで固定のため、モデルにせず choices で持つ。
BUSINESS_UNIT_CHOICES = [
    ("cafeteria", "食堂事業"),
    ("sales", "販売事業"),
]

# 帳簿上の会社（法人格）。所在は同じだが、給与計算・支払いは会社単位で分ける。
# 業務区分（business_unit）とは独立：販売事業部の中に LN（販売員）と
# LF（弁当製造員）の2社のスタッフが混在する。
#   LSN：(有)ランチサービスネットワーク … 母体（社長・社員）／食堂事業
#   LN ：(合)ランチネット             … 販売会社／販売事業
#   LF ：(合)ランチファクトリー         … 弁当製造会社／販売事業
# choices の第1要素はDB値、コード上の参照に使う。マスタモデルは作らない（3社固定）。
COMPANY_LSN = "LSN"
COMPANY_LN = "LN"
COMPANY_LF = "LF"
COMPANY_CHOICES = [
    (COMPANY_LSN, "(有)ランチサービスネットワーク"),
    (COMPANY_LN, "(合)ランチネット"),
    (COMPANY_LF, "(合)ランチファクトリー"),
]
COMPANY_SHORT_LABELS = {
    COMPANY_LSN: "LSN",
    COMPANY_LN: "LN",
    COMPANY_LF: "LF",
}


class TimeStampedModel(models.Model):
    """作成・更新時刻を全モデル共通で持たせる抽象基底。"""

    created_at = models.DateTimeField("作成日時", auto_now_add=True)
    updated_at = models.DateTimeField("更新日時", auto_now=True)

    class Meta:
        abstract = True


class Store(TimeStampedModel):
    """勤務先の店舗。スプリント1の対象は食堂事業の1店舗。"""

    business_unit = models.CharField("事業区分", max_length=16, choices=BUSINESS_UNIT_CHOICES)
    name = models.CharField("店舗名", max_length=100)
    address = models.CharField("所在地", max_length=255, blank=True, default="")
    is_active = models.BooleanField("有効", default=True)

    class Meta:
        verbose_name = "店舗"
        verbose_name_plural = "店舗"
        ordering = ["business_unit", "name"]

    def __str__(self):
        return f"{self.get_business_unit_display()}／{self.name}"


class Staff(TimeStampedModel):
    """時給スタッフ。ログインユーザー（auth.User）と1対1で紐付く。

    食堂スタッフはこのアプリ用にアカウントを新規発行し、販売事業スタッフは
    既存 lunchnetsale アカウントを流用する想定（要件定義 F8）。
    """

    EMPLOYMENT_HOURLY = "hourly"
    EMPLOYMENT_TYPE_CHOICES = [
        (EMPLOYMENT_HOURLY, "時給"),
    ]

    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="attendance_staff",
        verbose_name="ログインユーザー",
    )
    GENDER_MALE = "male"
    GENDER_FEMALE = "female"
    GENDER_OTHER = "other"
    GENDER_UNSPECIFIED = ""
    GENDER_CHOICES = [
        (GENDER_UNSPECIFIED, "未設定"),
        (GENDER_MALE, "男性"),
        (GENDER_FEMALE, "女性"),
        (GENDER_OTHER, "その他"),
    ]

    display_name = models.CharField("氏名", max_length=100)
    business_unit = models.CharField("事業区分", max_length=16, choices=BUSINESS_UNIT_CHOICES)
    # 帳簿上の会社（法人格）。給与計算・支払いを会社単位で分ける。
    # 既定は LSN（社員・食堂が最も多い）。データ移行で事業区分から自動割当する。
    # LN と LF はどちらも「販売事業部」に属する（LFは弁当製造、LNは販売の担当）。
    company = models.CharField(
        "会社", max_length=8, choices=COMPANY_CHOICES, default=COMPANY_LSN,
        help_text=(
            "LSN=(有)ランチサービスネットワーク（社員／食堂事業）／"
            "LN=(合)ランチネット（販売事業の販売担当）／"
            "LF=(合)ランチファクトリー（販売事業の弁当製造担当）"
        ),
    )
    store = models.ForeignKey(
        Store,
        on_delete=models.PROTECT,
        related_name="staff_members",
        verbose_name="所属店舗",
    )
    employment_type = models.CharField(
        "雇用形態",
        max_length=16,
        choices=EMPLOYMENT_TYPE_CHOICES,
        default=EMPLOYMENT_HOURLY,
    )
    hired_on = models.DateField("入社日", null=True, blank=True)
    is_active = models.BooleanField("在籍", default=True)
    # QR打刻用の個人トークン。スタッフごとに一意で、QRに埋め込んで配布する。
    # 不正利用された場合は再発行（=値を差し替え）するとQRが無効になる。
    punch_token = models.UUIDField(
        "打刻トークン",
        default=uuid.uuid4,
        editable=False,
        unique=True,
    )
    # 労働者名簿（労基法107条）。実務上の運用で本人確認・社労士手続きに使う。
    birthday = models.DateField("生年月日", null=True, blank=True)
    gender = models.CharField(
        "性別", max_length=8, choices=GENDER_CHOICES, blank=True, default="",
    )
    address = models.CharField("住所", max_length=255, blank=True, default="")
    phone = models.CharField("連絡先", max_length=32, blank=True, default="")
    job_description = models.CharField(
        "業務の種類", max_length=100, blank=True, default="",
        help_text="名簿に書く業務の種類。例：「お弁当製造」「ホール販売」「配達運転手」",
    )
    retired_on = models.DateField("退職年月日", null=True, blank=True)
    retire_reason = models.CharField(
        "退職事由", max_length=200, blank=True, default="",
    )
    # 有給休暇の今期付与日数。10月〜9月の有給年度内で消化される日数。
    # 労基法39条で勤続年数により10〜20日が法定だが、運用上は社労士の指示で
    # 個別に設定する想定（v1は手動入力、v2で勤続年数から自動付与を検討）。
    paid_leave_granted_days = models.PositiveSmallIntegerField(
        "有給付与日数（今期）", default=10,
        help_text="10月〜9月の有給年度における付与日数。労基法39条の法定値を参考に設定",
    )

    class Meta:
        verbose_name = "スタッフ"
        verbose_name_plural = "スタッフ"
        ordering = ["business_unit", "display_name"]

    def __str__(self):
        return self.display_name


class HourlyWage(TimeStampedModel):
    """スタッフの時給。適用開始日つきの履歴で持ち、時給改定のさかのぼり事故を防ぐ。

    スプリント1では登録のみ。給与計算での参照はスプリント3で実装する。
    """

    staff = models.ForeignKey(
        Staff,
        on_delete=models.CASCADE,
        related_name="hourly_wages",
        verbose_name="スタッフ",
    )
    amount = models.PositiveIntegerField("時給（円）")
    effective_from = models.DateField("適用開始日")

    class Meta:
        verbose_name = "時給"
        verbose_name_plural = "時給"
        ordering = ["staff", "-effective_from"]
        constraints = [
            models.UniqueConstraint(
                fields=["staff", "effective_from"],
                name="uniq_staff_wage_effective_from",
            )
        ]

    def __str__(self):
        return f"{self.staff.display_name} {self.amount}円（{self.effective_from}〜）"


class TimeRecord(TimeStampedModel):
    """打刻1件。出勤または退勤。

    休憩は打刻しない（労働時間に応じてスプリント2の集計で自動控除する方針）。
    取り消しは論理削除（is_deleted）とし、打刻履歴は残す。
    """

    KIND_CLOCK_IN = "clock_in"
    KIND_CLOCK_OUT = "clock_out"
    KIND_CHOICES = [
        (KIND_CLOCK_IN, "出勤"),
        (KIND_CLOCK_OUT, "退勤"),
    ]

    SOURCE_APP = "app"
    SOURCE_MANUAL = "manual"
    SOURCE_CHOICES = [
        (SOURCE_APP, "スマホ打刻"),
        (SOURCE_MANUAL, "管理者まとめ入力"),
    ]

    staff = models.ForeignKey(
        Staff,
        on_delete=models.CASCADE,
        related_name="time_records",
        verbose_name="スタッフ",
    )
    kind = models.CharField("種別", max_length=16, choices=KIND_CHOICES)
    recorded_at = models.DateTimeField("打刻日時")
    # 集計の単位となる勤務日。日付をまたぐ勤務でも出勤日に揃えるため独立して持つ。
    work_date = models.DateField("勤務日")
    source = models.CharField(
        "入力経路", max_length=16, choices=SOURCE_CHOICES, default=SOURCE_APP
    )
    is_corrected = models.BooleanField("修正済み", default=False)
    is_deleted = models.BooleanField("取り消し済み", default=False)
    note = models.CharField("備考", max_length=255, blank=True, default="")

    class Meta:
        verbose_name = "打刻"
        verbose_name_plural = "打刻"
        ordering = ["-recorded_at"]
        indexes = [
            models.Index(fields=["staff", "work_date"]),
        ]
        constraints = [
            # 同一スタッフ・同一勤務日に有効な出勤/退勤は各1件まで。
            # 二度押しが競合してもDB側で弾く（is_deleted の取り消し分は対象外）。
            models.UniqueConstraint(
                fields=["staff", "work_date", "kind"],
                condition=Q(is_deleted=False),
                name="uniq_active_punch_per_day_kind",
            )
        ]

    def __str__(self):
        local = timezone.localtime(self.recorded_at)
        return f"{self.staff.display_name} {self.get_kind_display()} {local:%Y-%m-%d %H:%M:%S}"


class PayrollSetting(TimeStampedModel):
    """給与計算の設定。組織で1レコードのみ運用する。

    スプリント1ではモデルとadmin登録のみ。割増・端数・休憩控除の各値を
    計算で参照するのはスプリント2以降。初期値は労働基準法の法定値に合わせる。
    """

    closing_day = models.PositiveSmallIntegerField("締め日", default=15)
    overtime_rate = models.DecimalField(
        "時間外割増率", max_digits=4, decimal_places=2, default=Decimal("0.25")
    )
    over60h_rate = models.DecimalField(
        "月60時間超の時間外割増率", max_digits=4, decimal_places=2, default=Decimal("0.50")
    )
    night_rate = models.DecimalField(
        "深夜割増率", max_digits=4, decimal_places=2, default=Decimal("0.25")
    )
    holiday_rate = models.DecimalField(
        "休日割増率", max_digits=4, decimal_places=2, default=Decimal("0.35")
    )
    night_start = models.TimeField("深夜帯 開始", default=time(22, 0))
    night_end = models.TimeField("深夜帯 終了", default=time(5, 0))
    # 休憩の自動控除は拘束時間（退勤−出勤）のしきい値で2段階に判定する。
    # 初期値は法定準拠（拘束6h超→45分／8h超→60分）。値はここで変更できる。
    break1_threshold_minutes = models.PositiveSmallIntegerField(
        "休憩控除 しきい値1（拘束・分超）", default=360
    )
    break1_deduct_minutes = models.PositiveSmallIntegerField(
        "休憩控除 控除分数1（分）", default=45
    )
    break2_threshold_minutes = models.PositiveSmallIntegerField(
        "休憩控除 しきい値2（拘束・分超）", default=480
    )
    break2_deduct_minutes = models.PositiveSmallIntegerField(
        "休憩控除 控除分数2（分）", default=60
    )
    rounding_rule = models.CharField(
        "端数処理", max_length=64, default="1分単位（丸めなし）"
    )
    # 地域別最低賃金（円）。初期値は東京（2025-10〜）。所在地確定後に設定で変更。
    min_wage = models.PositiveIntegerField("最低賃金（円）", default=1226)
    # 支給額の丸め単位（円）。1＝円単位（丸めなし）。
    wage_rounding_unit = models.PositiveSmallIntegerField(
        "賃金の丸め単位（円）", default=1
    )
    # 雇用保険料率（労働者負担）。既定は2025年度の一般の事業＝5.5/1000。
    employment_insurance_rate = models.DecimalField(
        "雇用保険料率（労働者負担）", max_digits=6, decimal_places=4, default=Decimal("0.0055")
    )
    # 販売事業部の手当単価（会社一律）。スタッフごとに変える場合は将来 StaffPayrollProfile に逃がす。
    peddling_allowance_yen = models.PositiveIntegerField(
        "行商手当（円/回）", default=1000,
        help_text="販売事業部の行商1回あたりの手当額",
    )
    box_wash_allowance_yen = models.PositiveIntegerField(
        "箱洗い手当（円/箱）", default=200,
        help_text="販売事業部の箱洗い1箱あたりの手当額",
    )
    driver_allowance_yen = models.PositiveIntegerField(
        "ドライバー手当（円/回）", default=1500,
        help_text="配達ドライバー1回あたりの手当額。プロフィールで「ドライバー」に該当する人のみ加算される",
    )

    class Meta:
        verbose_name = "給与設定"
        verbose_name_plural = "給与設定"

    def __str__(self):
        return f"給与設定（{self.closing_day}日締め）"

    def save(self, *args, **kwargs):
        # 設定は組織で1件のみ。常に pk=1 へ固定し、複数レコードの発生を防ぐ。
        self.pk = 1
        super().save(*args, **kwargs)

    def deduct_break_minutes(self, span_minutes):
        """拘束時間（分）から自動控除する休憩分数を返す。長いしきい値を優先。"""
        if span_minutes > self.break2_threshold_minutes:
            return self.break2_deduct_minutes
        if span_minutes > self.break1_threshold_minutes:
            return self.break1_deduct_minutes
        return 0

    @classmethod
    def current(cls):
        """組織で1件の給与設定を返す。無ければ既定値で作成する。"""
        return cls.objects.get_or_create(pk=1)[0]


class PayrollPeriod(TimeStampedModel):
    """締め期間（○月度）。締めるとその期間の打刻・修正・入力をロックする。

    期間は閲覧・締め操作のときに必要に応じて作成される。期間レコードが無い
    ＝まだ一度も締めていない＝ロックされていない、と扱う。
    """

    period_start = models.DateField("期間開始日")
    # 締め日。期間を一意に識別するキーにする。
    period_end = models.DateField("期間終了日（締め日）", unique=True)
    closing_day = models.PositiveSmallIntegerField("締め日（スナップショット）")
    # その月のガソリン単価（円/L）。給与計算画面で運用者が手動で入れる。
    # 0 のままだと通勤ガソリン代は計算されない（=非アクティブ）。
    gasoline_yen_per_liter = models.PositiveIntegerField(
        "今月のガソリン単価（円/L）", default=0,
        help_text="月末にスタンド価格を確認して入力。0だとガソリン代計算しない",
    )
    is_closed = models.BooleanField("締め済み", default=False)
    closed_at = models.DateTimeField("締め日時", null=True, blank=True)
    closed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="closed_payroll_periods",
        verbose_name="締め実行者",
    )

    class Meta:
        verbose_name = "締め期間"
        verbose_name_plural = "締め期間"
        ordering = ["-period_end"]

    def __str__(self):
        mark = "締め済み" if self.is_closed else "未締め"
        return f"{self.period_end.year}年{self.period_end.month}月度（{mark}）"

    def covers(self, work_date):
        """指定の勤務日がこの期間に含まれるか。"""
        return self.period_start <= work_date <= self.period_end


class TimeRecordEdit(TimeStampedModel):
    """打刻の修正履歴。管理者が打刻を作成・更新・削除するたびに1件残す（追記専用）。

    「誰が・いつ・どの打刻を・どう変えたか」を保持し、勤怠の改ざんを追えるようにする。
    """

    ACTION_CREATED = "created"
    ACTION_UPDATED = "updated"
    ACTION_DELETED = "deleted"
    ACTION_CHOICES = [
        (ACTION_CREATED, "新規入力"),
        (ACTION_UPDATED, "時刻修正"),
        (ACTION_DELETED, "取り消し"),
    ]

    time_record = models.ForeignKey(
        TimeRecord,
        on_delete=models.CASCADE,
        related_name="edits",
        verbose_name="対象の打刻",
    )
    editor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="time_record_edits",
        verbose_name="修正者",
    )
    action = models.CharField("操作", max_length=16, choices=ACTION_CHOICES)
    before_value = models.CharField("修正前", max_length=32, blank=True, default="—")
    after_value = models.CharField("修正後", max_length=32, blank=True, default="—")
    reason = models.CharField("理由", max_length=255, blank=True, default="")

    class Meta:
        verbose_name = "打刻の修正履歴"
        verbose_name_plural = "打刻の修正履歴"
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.get_action_display()} {self.before_value}→{self.after_value}"


class Payslip(TimeStampedModel):
    """確定した給与計算結果の凍結スナップショット（スプリント3）。

    給与計算画面で確定したときに、1スタッフ1期間につき1件作成する。
    打刻が後から変わっても、確定済みの金額は動かさない（再計算は取消→再実行）。
    PDF発行（スプリント4）はこのレコードを参照する。
    """

    payroll_period = models.ForeignKey(
        "PayrollPeriod",
        on_delete=models.CASCADE,
        related_name="payslips",
        verbose_name="締め期間",
    )
    staff = models.ForeignKey(
        Staff,
        on_delete=models.PROTECT,
        related_name="payslips",
        verbose_name="スタッフ",
    )
    work_days = models.PositiveSmallIntegerField("勤務日数", default=0)
    work_minutes = models.PositiveIntegerField("実働（分）", default=0)
    # 通常時間（分）。基本賃金の対象。時間外・深夜・休日は含まない（v2方式）。
    normal_minutes = models.PositiveIntegerField("通常時間（分）", default=0)
    # 控除した休憩（分）。打刻ベースは自動控除分の合計、手動入力は入力した月合計。
    break_minutes = models.PositiveIntegerField("休憩（分）", default=0)
    overtime_minutes = models.PositiveIntegerField("時間外（分）", default=0)
    over60h_minutes = models.PositiveIntegerField("うち月60時間超（分）", default=0)
    night_minutes = models.PositiveIntegerField("深夜（分）", default=0)
    holiday_minutes = models.PositiveIntegerField("休日（分）", default=0)
    hourly_wage = models.PositiveIntegerField("時給（円）", default=0)
    base_wage_yen = models.PositiveIntegerField("基本賃金（円）", default=0)
    # 休憩控除（支給欄の表示用）。基本賃金（拘束分）＝base_wage_yen＋break_deduction_yen。
    break_deduction_yen = models.PositiveIntegerField("休憩控除（円）", default=0)
    overtime_premium_yen = models.PositiveIntegerField("時間外割増（円）", default=0)
    night_premium_yen = models.PositiveIntegerField("深夜割増（円）", default=0)
    holiday_premium_yen = models.PositiveIntegerField("休日割増（円）", default=0)
    total_yen = models.PositiveIntegerField("賃金計（基本＋割増）（円）", default=0)
    # Phase 2：手当・控除・差引支給額
    commute_allowance_yen = models.PositiveIntegerField("通勤手当（円）", default=0)
    other_allowance_yen = models.PositiveIntegerField("その他手当（円）", default=0)
    # 販売事業部向けの手当スナップショット（食堂は通常 0）
    peddling_allowance_yen = models.PositiveIntegerField("行商手当（円）", default=0)
    box_wash_allowance_yen = models.PositiveIntegerField("箱洗い手当（円）", default=0)
    paid_leave_yen = models.PositiveIntegerField("有給手当（円）", default=0)
    driver_allowance_yen = models.PositiveIntegerField("ドライバー手当（円）", default=0)
    # 通勤ガソリン代（非課税扱い）
    gasoline_yen = models.PositiveIntegerField("通勤ガソリン代（円）", default=0)
    gross_yen = models.PositiveIntegerField("総支給額（円）", default=0)
    health_insurance_yen = models.PositiveIntegerField("健康保険料（円）", default=0)
    nursing_insurance_yen = models.PositiveIntegerField("介護保険料（円）", default=0)
    pension_yen = models.PositiveIntegerField("厚生年金保険料（円）", default=0)
    employment_insurance_yen = models.PositiveIntegerField("雇用保険料（円）", default=0)
    income_tax_yen = models.PositiveIntegerField("所得税（円）", default=0)
    resident_tax_yen = models.PositiveIntegerField("住民税（円）", default=0)
    other_deduction_yen = models.PositiveIntegerField("その他控除（円）", default=0)
    total_deduction_yen = models.PositiveIntegerField("控除合計（円）", default=0)
    # 控除が総支給額を上回るとマイナスになり得る（要対応のサイン）ため符号付き。
    net_pay_yen = models.IntegerField("差引支給額（円）", default=0)
    confirmed_at = models.DateTimeField("確定日時")
    confirmed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="confirmed_payslips",
        verbose_name="確定者",
    )

    class Meta:
        verbose_name = "給与明細"
        verbose_name_plural = "給与明細"
        ordering = ["-payroll_period__period_end", "staff"]
        constraints = [
            models.UniqueConstraint(
                fields=["payroll_period", "staff"],
                name="uniq_payslip_per_period_staff",
            )
        ]

    def __str__(self):
        return f"{self.staff.display_name} {self.payroll_period} 差引{self.net_pay_yen:,}円"


class StaffPayrollProfile(TimeStampedModel):
    """スタッフごとの給与プロフィール（Phase 2）。手当・控除の登録値を持つ。

    社会保険料・住民税は社労士／自治体から渡る月額をそのまま登録する（ハイブリッド
    方式・契約書 決定1）。雇用保険料と所得税はこの値を使って計算で求める。
    """

    staff = models.OneToOneField(
        Staff,
        on_delete=models.CASCADE,
        related_name="payroll_profile",
        verbose_name="スタッフ",
    )
    commute_allowance = models.PositiveIntegerField(
        "通勤手当（月額・円）", default=0,
        help_text="非課税扱い（暫定）。所得税の課税対象から除く",
    )
    other_allowance = models.PositiveIntegerField("その他手当（月額・円）", default=0)
    other_allowance_name = models.CharField(
        "その他手当の名称", max_length=32, blank=True, default="その他手当"
    )
    health_insurance = models.PositiveIntegerField("健康保険料（月額・円）", default=0)
    nursing_insurance = models.PositiveIntegerField("介護保険料（月額・円）", default=0)
    pension_insurance = models.PositiveIntegerField("厚生年金保険料（月額・円）", default=0)
    resident_tax = models.PositiveIntegerField("住民税（月額・円）", default=0)
    other_deduction = models.PositiveIntegerField("その他控除（月額・円）", default=0)
    other_deduction_name = models.CharField(
        "その他控除の名称", max_length=32, blank=True, default="その他控除"
    )
    dependents_count = models.PositiveSmallIntegerField(
        "扶養親族等の数", default=0,
        help_text="所得税計算用。源泉控除対象配偶者＋源泉控除対象親族の数",
    )
    employment_insurance_enrolled = models.BooleanField(
        "雇用保険に加入", default=True
    )
    # 有給手当の計算に使う「1日あたりの所定労働時間（分）」。販売事業部での
    # 有給支給額 = 有給日数 × 所定分 × 時給/60 で算出する。
    # 既定は8時間 = 480分（一般的なフルタイム想定）。
    scheduled_minutes_per_day = models.PositiveSmallIntegerField(
        "1日の所定労働時間（分）", default=480,
        help_text="有給1日あたりの支払い時間として使う。例：8時間勤務なら480分",
    )
    # 配達ドライバーかどうか。True のスタッフだけ給与明細に「ドライバー回数」
    # 入力欄が出る。False の人にはドライバー手当の概念自体表示しない。
    is_driver = models.BooleanField(
        "配達ドライバー", default=False,
        help_text="チェックを付けると給与計算でドライバー手当の入力欄が出る",
    )
    # 通勤手段。ガソリン代の自動計算に使う（car/motorcycle のときだけ計算）。
    COMMUTE_METHOD_CHOICES = [
        ("transit", "公共交通機関"),
        ("car", "車"),
        ("motorcycle", "バイク"),
        ("walk", "徒歩・自転車"),
    ]
    commute_method = models.CharField(
        "通勤手段", max_length=16, choices=COMMUTE_METHOD_CHOICES, default="transit",
    )
    commute_distance_km = models.DecimalField(
        "片道通勤距離（km）", max_digits=5, decimal_places=1, default=Decimal("0"),
        help_text="ガソリン代計算に使う。自宅↔職場の片道距離",
    )
    fuel_efficiency_kml = models.DecimalField(
        "燃費（km/L）", max_digits=4, decimal_places=1, default=Decimal("15"),
        help_text="ガソリン代計算に使う。一般車は15km/L目安",
    )

    class Meta:
        verbose_name = "給与プロフィール"
        verbose_name_plural = "給与プロフィール"

    def __str__(self):
        return f"{self.staff.display_name} の給与プロフィール"


class ManualWorkHours(TimeStampedModel):
    """勤務時間を月単位で直接入力するレコード（販売事業の v1 用）。

    打刻（TimeRecord）を導入していない販売事業では、紙タイムカードから集計した
    月次の総勤務時間（と時間外・深夜・休日の内訳）を1スタッフ1期間で1件記録する。
    給与計算でこのレコードがあるスタッフは、打刻からの集計を使わずこの値を使う。
    """

    staff = models.ForeignKey(
        Staff,
        on_delete=models.CASCADE,
        related_name="manual_hours",
        verbose_name="スタッフ",
    )
    payroll_period = models.ForeignKey(
        "PayrollPeriod",
        on_delete=models.CASCADE,
        related_name="manual_hours",
        verbose_name="締め期間",
    )
    work_days = models.PositiveSmallIntegerField("勤務日数", default=0)
    # 勤務時間は「拘束時間（休憩込み）」として入力する。実働＝拘束−休憩で求める。
    # 既存レコードは break_minutes=0 のため、実働＝work_minutes で従来どおり計算される。
    work_minutes = models.PositiveIntegerField("勤務時間（分・拘束）", default=0)
    # 今月の休憩合計（分）。勤務時間（拘束）から差し引いて実働を出す（販売事業 v1）。
    # 打刻ベース（食堂）は拘束時間から自動控除するため、こちらの手動入力は使わない。
    break_minutes = models.PositiveIntegerField("休憩時間（分・月合計）", default=0)
    overtime_minutes = models.PositiveIntegerField("時間外時間（分）", default=0)
    night_minutes = models.PositiveIntegerField("深夜時間（分）", default=0)
    holiday_minutes = models.PositiveIntegerField("休日時間（分）", default=0)
    # 販売事業部の手当用（行商・箱洗い・有給・ドライバー）。食堂は通常 0 で影響しない。
    peddling_count = models.PositiveSmallIntegerField("行商回数（回）", default=0)
    box_wash_count = models.PositiveIntegerField("箱洗い数（箱）", default=0)
    paid_leave_days = models.PositiveSmallIntegerField("有給取得日数（日）", default=0)
    driver_count = models.PositiveSmallIntegerField("ドライバー回数（回）", default=0)
    note = models.CharField("備考", max_length=255, blank=True, default="")

    class Meta:
        verbose_name = "勤務時間（手動入力）"
        verbose_name_plural = "勤務時間（手動入力）"
        constraints = [
            models.UniqueConstraint(
                fields=["staff", "payroll_period"],
                name="uniq_manual_hours_per_staff_period",
            )
        ]

    def __str__(self):
        return f"{self.staff.display_name} {self.payroll_period} 勤務{self.work_minutes // 60}h"


class PayslipAdjustment(TimeStampedModel):
    """給与明細の臨時項目。年末調整還付金・慶弔金・遡及精算など、月次給与の
    定常項目に当たらない金額をスタッフ×期間に紐づけて1行ずつ持つ。

    kind=支給(payment) は総支給額に加算され、kind=控除(deduction) は控除合計に
    加算される。額は常に正の整数（還付金などマイナス相当のものも、支給側に
    プラス計上することで手取りに加算される）。
    """

    KIND_PAYMENT = "payment"
    KIND_DEDUCTION = "deduction"
    KIND_CHOICES = [
        (KIND_PAYMENT, "支給（追加）"),
        (KIND_DEDUCTION, "控除（追加）"),
    ]

    staff = models.ForeignKey(
        Staff,
        on_delete=models.CASCADE,
        related_name="payslip_adjustments",
        verbose_name="スタッフ",
    )
    payroll_period = models.ForeignKey(
        "PayrollPeriod",
        on_delete=models.CASCADE,
        related_name="adjustments",
        verbose_name="締め期間",
    )
    name = models.CharField("項目名", max_length=64)
    kind = models.CharField("区分", max_length=16, choices=KIND_CHOICES)
    amount_yen = models.PositiveIntegerField("金額（円）")
    note = models.CharField("備考", max_length=255, blank=True, default="")

    class Meta:
        verbose_name = "臨時項目"
        verbose_name_plural = "臨時項目"
        ordering = ["staff", "payroll_period", "id"]

    def __str__(self):
        sign = "＋" if self.kind == self.KIND_PAYMENT else "−"
        return f"{self.staff.display_name} {self.name} {sign}{self.amount_yen:,}円"
