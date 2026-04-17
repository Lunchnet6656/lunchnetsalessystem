from django.contrib import admin
from .models import (
    QuestProfile, MissionTemplate, DailyMission,
    Achievement, UserAchievement,
)


@admin.register(QuestProfile)
class QuestProfileAdmin(admin.ModelAdmin):
    list_display = ('user', 'level', 'title', 'xp', 'total_xp', 'current_streak')
    list_filter = ('level',)
    search_fields = ('user__username', 'user__first_name')


@admin.register(MissionTemplate)
class MissionTemplateAdmin(admin.ModelAdmin):
    list_display = ('mission_type', 'name_template', 'base_xp', 'is_active')
    list_filter = ('is_active', 'mission_type')


@admin.register(DailyMission)
class DailyMissionAdmin(admin.ModelAdmin):
    list_display = ('user', 'date', 'title', 'status', 'xp_reward', 'actual_value')
    list_filter = ('status', 'date')
    search_fields = ('user__username', 'title')
    date_hierarchy = 'date'


@admin.register(Achievement)
class AchievementAdmin(admin.ModelAdmin):
    list_display = ('code', 'name', 'category', 'xp_reward', 'threshold', 'sort_order')
    list_filter = ('category',)


@admin.register(UserAchievement)
class UserAchievementAdmin(admin.ModelAdmin):
    list_display = ('user', 'achievement', 'earned_at')
    list_filter = ('achievement',)
    search_fields = ('user__username',)
