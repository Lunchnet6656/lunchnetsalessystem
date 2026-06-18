from django.contrib import admin

from .models import (
    HourlyWage,
    PayrollPeriod,
    PayrollSetting,
    Payslip,
    Staff,
    Store,
    TimeRecord,
    TimeRecordEdit,
)


class HourlyWageInline(admin.TabularInline):
    model = HourlyWage
    extra = 1


@admin.register(Store)
class StoreAdmin(admin.ModelAdmin):
    list_display = ("name", "business_unit", "is_active")
    list_filter = ("business_unit", "is_active")
    search_fields = ("name",)


@admin.register(Staff)
class StaffAdmin(admin.ModelAdmin):
    list_display = (
        "display_name",
        "business_unit",
        "store",
        "employment_type",
        "is_active",
    )
    list_filter = ("business_unit", "is_active", "store")
    search_fields = ("display_name", "user__username")
    autocomplete_fields = ("user",)
    inlines = (HourlyWageInline,)


@admin.register(HourlyWage)
class HourlyWageAdmin(admin.ModelAdmin):
    list_display = ("staff", "amount", "effective_from")
    list_filter = ("effective_from",)
    search_fields = ("staff__display_name",)


@admin.register(TimeRecord)
class TimeRecordAdmin(admin.ModelAdmin):
    list_display = (
        "staff",
        "kind",
        "recorded_at",
        "work_date",
        "source",
        "is_corrected",
        "is_deleted",
    )
    list_filter = ("kind", "source", "is_corrected", "is_deleted", "work_date")
    search_fields = ("staff__display_name",)
    date_hierarchy = "work_date"


@admin.register(PayrollSetting)
class PayrollSettingAdmin(admin.ModelAdmin):
    list_display = (
        "__str__",
        "closing_day",
        "overtime_rate",
        "night_rate",
        "min_wage",
        "rounding_rule",
    )


@admin.register(Payslip)
class PayslipAdmin(admin.ModelAdmin):
    """給与明細は確定スナップショット。admin からの新規作成・編集はさせない
    （確定・取消は給与計算画面から行う）。"""

    list_display = (
        "payroll_period",
        "staff",
        "work_days",
        "hourly_wage",
        "base_wage_yen",
        "total_yen",
        "confirmed_at",
        "confirmed_by",
    )
    list_filter = ("payroll_period", "staff__business_unit")
    search_fields = ("staff__display_name",)

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False


@admin.register(PayrollPeriod)
class PayrollPeriodAdmin(admin.ModelAdmin):
    list_display = (
        "__str__",
        "period_start",
        "period_end",
        "is_closed",
        "closed_at",
        "closed_by",
    )
    list_filter = ("is_closed",)
    date_hierarchy = "period_end"


@admin.register(TimeRecordEdit)
class TimeRecordEditAdmin(admin.ModelAdmin):
    """打刻の修正履歴は閲覧専用。改ざん追跡のため admin から編集・追加させない。"""

    list_display = (
        "created_at",
        "time_record",
        "action",
        "before_value",
        "after_value",
        "editor",
        "reason",
    )
    list_filter = ("action", "created_at")
    search_fields = ("time_record__staff__display_name", "reason")
    date_hierarchy = "created_at"

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False
