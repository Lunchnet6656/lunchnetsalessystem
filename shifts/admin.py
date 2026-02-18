from django.contrib import admin

from .models import (
    AvailabilityDay,
    AvailabilitySubmission,
    CompanyHoliday,
    SchedulePeriod,
    ShiftAssignment,
    ShiftSettings,
    UserProfile,
)


@admin.register(UserProfile)
class UserProfileAdmin(admin.ModelAdmin):
    list_display = ('user', 'can_drive', 'work_pattern', 'uses_app')
    list_filter = ('can_drive', 'work_pattern')


@admin.register(CompanyHoliday)
class CompanyHolidayAdmin(admin.ModelAdmin):
    list_display = ('date', 'name')


@admin.register(SchedulePeriod)
class SchedulePeriodAdmin(admin.ModelAdmin):
    list_display = ('start_date', 'end_date', 'status', 'submission_open_at', 'submission_close_at')
    list_filter = ('status',)


class AvailabilityDayInline(admin.TabularInline):
    model = AvailabilityDay
    extra = 0


@admin.register(AvailabilitySubmission)
class AvailabilitySubmissionAdmin(admin.ModelAdmin):
    list_display = ('user', 'period', 'status', 'submitted_at', 'submitted_by_admin')
    list_filter = ('status', 'submitted_by_admin')
    inlines = [AvailabilityDayInline]


@admin.register(AvailabilityDay)
class AvailabilityDayAdmin(admin.ModelAdmin):
    list_display = ('submission', 'date', 'availability', 'absence_category')
    list_filter = ('availability',)


@admin.register(ShiftAssignment)
class ShiftAssignmentAdmin(admin.ModelAdmin):
    list_display = ('date', 'sales_location', 'user', 'status')
    list_filter = ('status', 'date')


@admin.register(ShiftSettings)
class ShiftSettingsAdmin(admin.ModelAdmin):
    list_display = ('ok_threshold', 'warning_threshold', 'danger_threshold')
