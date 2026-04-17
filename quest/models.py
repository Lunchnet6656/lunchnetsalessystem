from django.db import models
from django.conf import settings


class QuestProfile(models.Model):
    """プレイヤープロフィール - RPGステータス"""
    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='quest_profile'
    )
    level = models.PositiveIntegerField(default=1)
    xp = models.PositiveIntegerField(default=0)
    total_xp = models.PositiveIntegerField(default=0)
    title = models.CharField(max_length=50, default='見習い販売員')
    character_image = models.CharField(max_length=100, default='quest/img/titles/lv001_minarai.png')
    current_streak = models.PositiveIntegerField(default=0)
    longest_streak = models.PositiveIntegerField(default=0)
    last_mission_date = models.DateField(null=True, blank=True)
    is_participating = models.BooleanField(default=False, verbose_name='参加中')
    sound_enabled = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = 'クエストプロフィール'
        verbose_name_plural = 'クエストプロフィール'

    def __str__(self):
        return f"Lv.{self.level} {self.title} ({self.user})"


class MissionTemplate(models.Model):
    """ミッション種別定義"""
    MISSION_TYPES = [
        ('sales_count', '販売数達成'),
        ('zero_waste', '廃棄ゼロ'),
        ('sold_out', '完売達成'),
        ('sold_out_time', '早期完売'),
        ('revenue', '売上金額達成'),
    ]
    mission_type = models.CharField(max_length=30, choices=MISSION_TYPES, unique=True)
    name_template = models.CharField(max_length=100)
    description = models.TextField(blank=True)
    base_xp = models.PositiveIntegerField(default=10)
    icon = models.CharField(max_length=50, blank=True)
    is_active = models.BooleanField(default=True)

    class Meta:
        verbose_name = 'ミッションテンプレート'
        verbose_name_plural = 'ミッションテンプレート'

    def __str__(self):
        return f"{self.get_mission_type_display()} ({self.base_xp}XP)"


class DailyMission(models.Model):
    """日次ミッションインスタンス"""
    STATUS_CHOICES = [
        ('pending', '進行中'),
        ('completed', '達成'),
        ('failed', '未達成'),
    ]
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='daily_missions'
    )
    date = models.DateField()
    template = models.ForeignKey(MissionTemplate, on_delete=models.CASCADE)
    title = models.CharField(max_length=200)
    description = models.TextField(blank=True)
    target_value = models.DecimalField(max_digits=10, decimal_places=0, default=0)
    actual_value = models.DecimalField(max_digits=10, decimal_places=0, null=True, blank=True)
    xp_reward = models.PositiveIntegerField(default=10)
    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default='pending')
    completed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        verbose_name = 'デイリーミッション'
        verbose_name_plural = 'デイリーミッション'
        unique_together = ['user', 'date', 'template']
        ordering = ['-date', 'template']

    def __str__(self):
        return f"{self.date} {self.title} [{self.get_status_display()}]"


class Achievement(models.Model):
    """実績バッジ定義"""
    CATEGORY_CHOICES = [
        ('streak', '連続達成'),
        ('cumulative', '累計達成'),
        ('special', '特別'),
    ]
    code = models.CharField(max_length=50, unique=True)
    name = models.CharField(max_length=100)
    description = models.TextField()
    category = models.CharField(max_length=20, choices=CATEGORY_CHOICES)
    icon = models.CharField(max_length=100, blank=True)
    xp_reward = models.PositiveIntegerField(default=50)
    threshold = models.PositiveIntegerField(default=0)
    sort_order = models.PositiveIntegerField(default=0)

    class Meta:
        verbose_name = '実績バッジ'
        verbose_name_plural = '実績バッジ'
        ordering = ['sort_order']

    def __str__(self):
        return f"{self.name} ({self.xp_reward}XP)"


class UserAchievement(models.Model):
    """ユーザー取得実績"""
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='quest_achievements'
    )
    achievement = models.ForeignKey(Achievement, on_delete=models.CASCADE)
    earned_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = 'ユーザー実績'
        verbose_name_plural = 'ユーザー実績'
        unique_together = ['user', 'achievement']

    def __str__(self):
        return f"{self.user} - {self.achievement.name}"


class CelebrationQueue(models.Model):
    """演出キュー: シグナルから演出情報を一時保存し、ビューで取り出す"""
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='celebration_queue'
    )
    # レベル情報
    prev_level = models.PositiveIntegerField(default=1)
    new_level = models.PositiveIntegerField(default=1)
    leveled_up = models.BooleanField(default=False)
    # 称号情報
    prev_title = models.CharField(max_length=50, blank=True)
    new_title = models.CharField(max_length=50, blank=True)
    title_changed = models.BooleanField(default=False)
    # キャラクター画像
    new_character_image = models.CharField(max_length=100, blank=True)
    # XP
    xp_gained = models.PositiveIntegerField(default=0)
    # 達成ミッション（JSON: [{title, xp_reward}]）
    completed_missions_json = models.JSONField(default=list)
    # 新実績（JSON: [{name, xp_reward, icon}]）
    new_achievements_json = models.JSONField(default=list)
    # 既読フラグ
    is_read = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = '演出キュー'
        verbose_name_plural = '演出キュー'
        ordering = ['-created_at']

    def __str__(self):
        return f"{self.user} Lv.{self.prev_level}→{self.new_level} ({self.created_at})"
