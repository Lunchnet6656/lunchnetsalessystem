"""予約モデルの admin 登録（開発・デバッグ用の最小登録）。

運用画面（予約一覧・集計・拠点ON/OFF・枠設定・QR発行）はアプリ内画面で別途作る（S4）。
admin はデータ確認用に最小限だけ置く。
"""
from django.contrib import admin

from .models import LineMember, Reservation, ReservationItem


class ReservationItemInline(admin.TabularInline):
    model = ReservationItem
    extra = 0


@admin.register(LineMember)
class LineMemberAdmin(admin.ModelAdmin):
    list_display = ("name", "line_user_id", "created_at")
    search_fields = ("name", "line_user_id")


@admin.register(Reservation)
class ReservationAdmin(admin.ModelAdmin):
    list_display = (
        "reservation_number", "sales_location", "pickup_date",
        "member", "status", "created_at",
    )
    list_filter = ("status", "pickup_date", "sales_location")
    search_fields = ("reservation_number", "member__name")
    inlines = [ReservationItemInline]
