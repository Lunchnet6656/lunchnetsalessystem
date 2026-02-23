from django.db import models
from django.conf import settings


class UserProfile(models.Model):
    WORK_PATTERN_CHOICES = [
        ('FULL', 'フルタイム'),
        ('PART', 'パートタイム'),
        ('HELPER', '助っ人'),
    ]

    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='profile',
    )
    can_drive = models.BooleanField(default=False, verbose_name="運転可能")
    work_pattern = models.CharField(
        max_length=6, choices=WORK_PATTERN_CHOICES,
        default='FULL', verbose_name="勤務形態",
    )
    uses_app = models.BooleanField(default=True, verbose_name="アプリ利用")
    min_shifts_per_week = models.PositiveIntegerField(default=0, verbose_name="最低出勤日数/週")
    max_shifts_per_week = models.PositiveIntegerField(default=5, verbose_name="最大出勤日数/週")
    fixed_weekdays = models.CharField(
        max_length=50, blank=True, default='',
        verbose_name="固定出勤曜日",
        help_text="カンマ区切り: 0=月,1=火,...,6=日",
    )
    default_location = models.ForeignKey(
        'sales.SalesLocation', on_delete=models.SET_NULL,
        null=True, blank=True, related_name='default_users',
        verbose_name="デフォルト売り場",
    )
    line_user_id = models.CharField(max_length=50, blank=True, default='', verbose_name='LINE User ID')
    notify_via_line = models.BooleanField(default=False, verbose_name='LINE通知')
    notify_via_email = models.BooleanField(default=True, verbose_name='メール通知')
    notification_email = models.EmailField(blank=True, default='', verbose_name='通知先メール')

    class Meta:
        verbose_name = "ユーザープロフィール"
        verbose_name_plural = "ユーザープロフィール"

    def __str__(self):
        return f"{self.user.get_full_name() or self.user.username} プロフィール"


class CompanyHoliday(models.Model):
    date = models.DateField(unique=True, verbose_name="日付")
    name = models.CharField(max_length=100, verbose_name="名称")

    class Meta:
        ordering = ['-date']
        verbose_name = "会社休日"
        verbose_name_plural = "会社休日"

    def __str__(self):
        return f"{self.date} {self.name}"


class SchedulePeriod(models.Model):
    STATUS_CHOICES = [
        ('OPEN', '募集中'),
        ('REVIEW', '確認中'),
        ('FIXED', '確定'),
        ('PUBLISHED', '公開済'),
    ]

    start_date = models.DateField(verbose_name="開始日")
    end_date = models.DateField(verbose_name="終了日")
    submission_open_at = models.DateTimeField(verbose_name="提出受付開始")
    submission_close_at = models.DateTimeField(verbose_name="提出締切")
    status = models.CharField(
        max_length=10, choices=STATUS_CHOICES,
        default='OPEN', verbose_name="ステータス",
    )
    shared_notes = models.TextField(blank=True, verbose_name="共有事項")

    class Meta:
        ordering = ['-start_date']
        verbose_name = "募集期間"
        verbose_name_plural = "募集期間"

    def __str__(self):
        return f"{self.start_date} ~ {self.end_date} ({self.get_status_display()})"


class AvailabilitySubmission(models.Model):
    STATUS_CHOICES = [
        ('DRAFT', '下書き'),
        ('SUBMITTED', '提出済'),
        ('APPROVED', '承認済'),
        ('RETURNED', '差戻'),
    ]

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='availability_submissions',
        verbose_name="ユーザー",
    )
    period = models.ForeignKey(
        SchedulePeriod,
        on_delete=models.CASCADE,
        related_name='submissions',
        verbose_name="期間",
    )
    status = models.CharField(
        max_length=10, choices=STATUS_CHOICES,
        default='DRAFT', verbose_name="ステータス",
    )
    submitted_at = models.DateTimeField(null=True, blank=True, verbose_name="提出日時")
    remarks = models.TextField(blank=True, default='', verbose_name="備考")
    admin_note = models.TextField(blank=True, default='', verbose_name="管理者メモ")
    submitted_by_admin = models.BooleanField(default=False, verbose_name="管理者代理提出")
    is_late_submission = models.BooleanField(default=False, verbose_name="締切後提出")

    class Meta:
        unique_together = ('user', 'period')
        verbose_name = "シフト希望提出"
        verbose_name_plural = "シフト希望提出"

    def __str__(self):
        return f"{self.user} - {self.period} ({self.get_status_display()})"


class AvailabilityDay(models.Model):
    AVAILABILITY_CHOICES = [
        ('WORK', '出勤'),
        ('OFF', '休み'),
    ]
    ABSENCE_CATEGORY_CHOICES = [
        ('PERSONAL', '私用休み'),
        ('SICK', '収入調整'),
        ('SUBSTITUTE', '代理休み'),
        ('OTHER', '冠婚葬祭・療養入院'),
    ]

    submission = models.ForeignKey(
        AvailabilitySubmission,
        on_delete=models.CASCADE,
        related_name='days',
        verbose_name="提出",
    )
    date = models.DateField(verbose_name="日付")
    availability = models.CharField(
        max_length=4, choices=AVAILABILITY_CHOICES,
        default='WORK', verbose_name="出勤/休み",
    )
    absence_category = models.CharField(
        max_length=10, choices=ABSENCE_CATEGORY_CHOICES,
        blank=True, default='', verbose_name="休みカテゴリ",
    )
    substitute_user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name='substitute_days',
        verbose_name="代替ユーザー",
    )
    comment = models.CharField(max_length=200, blank=True, default='', verbose_name="コメント")

    class Meta:
        unique_together = ('submission', 'date')
        ordering = ['date']
        verbose_name = "日別シフト希望"
        verbose_name_plural = "日別シフト希望"

    def __str__(self):
        return f"{self.date} - {self.get_availability_display()}"


class ExternalStaff(models.Model):
    name = models.CharField(max_length=100, verbose_name="名前")
    can_drive = models.BooleanField(default=False, verbose_name="運転可能")
    is_active = models.BooleanField(default=True, verbose_name="有効")
    default_location = models.ForeignKey(
        'sales.SalesLocation', on_delete=models.SET_NULL,
        null=True, blank=True, related_name='default_external_staff',
        verbose_name="デフォルト売り場",
    )

    class Meta:
        verbose_name = "外部スタッフ"
        verbose_name_plural = "外部スタッフ"

    def __str__(self):
        return self.name


class ExternalAvailabilityDay(models.Model):
    external_staff = models.ForeignKey(
        ExternalStaff, on_delete=models.CASCADE,
        related_name='availability_days', verbose_name="外部スタッフ",
    )
    period = models.ForeignKey(
        SchedulePeriod, on_delete=models.CASCADE,
        related_name='external_availability_days', verbose_name="期間",
    )
    date = models.DateField(verbose_name="日付")
    is_available = models.BooleanField(default=True, verbose_name="出勤可能")

    class Meta:
        unique_together = ('external_staff', 'period', 'date')
        ordering = ['date']
        verbose_name = "外部スタッフ出勤可能日"
        verbose_name_plural = "外部スタッフ出勤可能日"

    def __str__(self):
        return f"{self.external_staff.name} - {self.date} ({'可' if self.is_available else '不可'})"


class ShiftAssignment(models.Model):
    STATUS_CHOICES = [
        ('DRAFT', '仮'),
        ('CONFIRMED', '確定'),
    ]
    SPECIAL_TYPE_CHOICES = [
        ('REST', '休み'),
        ('CANCEL', '中止'),
    ]

    date = models.DateField(verbose_name="日付")
    sales_location = models.ForeignKey(
        'sales.SalesLocation',
        on_delete=models.CASCADE,
        related_name='shift_assignments',
        verbose_name="売り場",
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        null=True, blank=True,
        related_name='shift_assignments',
        verbose_name="担当者",
    )
    external_staff = models.ForeignKey(
        ExternalStaff,
        on_delete=models.CASCADE,
        null=True, blank=True,
        related_name='shift_assignments',
        verbose_name="外部スタッフ",
    )
    special_type = models.CharField(
        max_length=10, choices=SPECIAL_TYPE_CHOICES,
        blank=True, default='', verbose_name="特殊種別",
    )
    status = models.CharField(
        max_length=10, choices=STATUS_CHOICES,
        default='DRAFT', verbose_name="ステータス",
    )
    admin_note = models.CharField(max_length=200, blank=True, default='', verbose_name="管理者メモ")

    class Meta:
        unique_together = ('date', 'sales_location')
        ordering = ['date', 'sales_location']
        verbose_name = "シフト割当"
        verbose_name_plural = "シフト割当"
        constraints = [
            models.CheckConstraint(
                check=(
                    models.Q(user__isnull=False, external_staff__isnull=True, special_type='')
                    | models.Q(user__isnull=True, external_staff__isnull=False, special_type='')
                    | models.Q(user__isnull=True, external_staff__isnull=True, special_type__in=['REST', 'CANCEL'])
                ),
                name='assignment_user_or_external_staff',
            ),
        ]

    def __str__(self):
        if self.special_type:
            return f"{self.date} {self.sales_location} → {self.get_special_type_display()}"
        assignee = self.user or self.external_staff
        return f"{self.date} {self.sales_location} → {assignee}"

    @property
    def assignee_name(self):
        if self.special_type:
            return self.get_special_type_display()
        if self.user:
            full_name = f"{self.user.last_name} {self.user.first_name}".strip()
            return full_name or self.user.username
        if self.external_staff:
            return self.external_staff.name
        return ""

    @property
    def is_external(self):
        return self.external_staff is not None


class ShiftNotification(models.Model):
    NOTIFICATION_TYPE_CHOICES = [
        ('OPEN', '募集開始'),
        ('REMINDER', '締切リマインド'),
        ('MANUAL', '手動通知'),
    ]

    period = models.ForeignKey(SchedulePeriod, on_delete=models.CASCADE, related_name='notifications', verbose_name="期間")
    notification_type = models.CharField(max_length=20, choices=NOTIFICATION_TYPE_CHOICES, verbose_name="通知種別")
    title = models.CharField(max_length=200, verbose_name="タイトル")
    body = models.TextField(verbose_name="本文")
    created_at = models.DateTimeField(auto_now_add=True, verbose_name="送信日時")
    sent_line_count = models.IntegerField(default=0, verbose_name="LINE送信数")
    sent_email_count = models.IntegerField(default=0, verbose_name="メール送信数")

    class Meta:
        ordering = ['-created_at']
        verbose_name = "シフト通知"
        verbose_name_plural = "シフト通知"

    def __str__(self):
        return f"{self.created_at:%Y-%m-%d %H:%M} [{self.get_notification_type_display()}] {self.title}"


class ShiftSettings(models.Model):
    ok_threshold = models.PositiveIntegerField(default=2, verbose_name="OK上限（緑）")
    warning_threshold = models.PositiveIntegerField(default=4, verbose_name="注意上限（黄）")
    danger_threshold = models.PositiveIntegerField(default=6, verbose_name="危険上限（赤）")

    class Meta:
        verbose_name = "シフト設定"
        verbose_name_plural = "シフト設定"

    def save(self, *args, **kwargs):
        self.pk = 1
        super().save(*args, **kwargs)

    @classmethod
    def load(cls):
        obj, _ = cls.objects.get_or_create(pk=1)
        return obj

    def __str__(self):
        return f"しきい値設定 (OK≤{self.ok_threshold}, 注意≤{self.warning_threshold}, 危険>{self.danger_threshold})"
