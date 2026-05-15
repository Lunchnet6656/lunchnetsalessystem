from django.contrib import admin, messages
from django.http import HttpResponse

from .models import SalesLocation, Product, ItemQuantity, DailyReport, DailyReportEntry, CustomUser, OthersItem, ShiftRequest, Holiday, ReportMessage
from .models import _generate_qr_token
from django.contrib.auth.admin import UserAdmin

class CustomUserAdmin(UserAdmin):
    fieldsets = UserAdmin.fieldsets + (
        (None, {'fields': ('full_name',)}),
    )

@admin.action(description="選択した拠点のQR PDF を生成")
def generate_qr_pdf_action(modeladmin, request, queryset):
    """選択した拠点（no順）の QR PDF をダウンロードレスポンスとして返す。

    1拠点あたり2ページ（完売QR・急休みQR）。0件選択時はメッセージで通知。
    """
    locs = list(queryset.order_by('no'))
    if not locs:
        modeladmin.message_user(
            request, "拠点が選択されていません。", level=messages.WARNING,
        )
        return

    from .qr_pdf import build_qr_pdf  # 重い import を遅延

    def _build_url(token, kind):
        # admin にログイン中のリクエストから絶対URLを構築（dev/prod 自動切替）
        return request.build_absolute_uri(f"/q/{kind}/{token}/")

    pdf_bytes = build_qr_pdf(locs, _build_url)

    response = HttpResponse(pdf_bytes, content_type="application/pdf")
    response["Content-Disposition"] = (
        f'attachment; filename="lunchnet-qr-{len(locs)}locations.pdf"'
    )
    return response


@admin.action(description="選択した拠点のQRトークンを再発行（既存QRは無効化）")
def regenerate_qr_tokens_action(modeladmin, request, queryset):
    """流出時の緊急対応用。実行すると古い物理QRは即無効化（404）になる。

    再印刷＋現場差し替えが必要なので、警告メッセージを目立たせる。
    """
    count = 0
    for loc in queryset:
        loc.qr_sold_out_token = _generate_qr_token()
        loc.qr_closed_token = _generate_qr_token()
        loc.save(update_fields=['qr_sold_out_token', 'qr_closed_token'])
        count += 1

    if count == 0:
        modeladmin.message_user(
            request, "拠点が選択されていません。", level=messages.WARNING,
        )
        return

    modeladmin.message_user(
        request,
        f"{count}拠点のQRトークンを再発行しました。"
        f"既存の物理QRは即時無効化されました。再印刷＋現場差し替えが必要です。",
        level=messages.WARNING,
    )


class SalesLocationAdmin(admin.ModelAdmin):
    list_display = (
        'no', 'name', 'type', 'price_type', 'service_name', 'service_price',
        'service_style', 'direct_return',
        'excluded_from_shift', 'excluded_from_public_status',
        'qr_enabled', 'today_override', 'today_override_date',
    )
    list_filter = (
        'excluded_from_shift', 'excluded_from_public_status',
        'qr_enabled', 'today_override',
    )
    list_editable = ('excluded_from_public_status', 'qr_enabled', 'today_override')
    search_fields = ('no', 'name', 'direct_return')
    readonly_fields = ('qr_sold_out_token', 'qr_closed_token', 'last_qr_publish_at')
    actions = [generate_qr_pdf_action, regenerate_qr_tokens_action]


class ProductAdmin(admin.ModelAdmin):
    list_display = ('no', 'week', 'name', 'price_A','price_B','price_C', 'container_type')
    search_fields = ('no','name', 'week')

class ItemQuantityAdmin(admin.ModelAdmin):
    list_display = ('target_date', 'target_week', 'product', 'sales_location', 'quantity')
    search_fields = ('target_week', 'product', 'sales_location')
    list_filter = ('target_week', 'target_date', 'sales_location')

class DailyReportAdmin(admin.ModelAdmin):
    list_display = ('date', 'location', 'location_no', 'person_in_charge', 'total_quantity', 'total_sales_quantity', 'total_remaining', 'total_revenue', 'no_rice_quantity', 'extra_rice_quantity', 'coupon_type_600', 'coupon_type_700', 'discount_50', 'discount_100', 'total_discount', 'paypay', 'digital_payment', 'cash', 'sales_difference')
    list_filter = ('date', 'location', 'person_in_charge', 'weather', 'temp')
    search_fields = ('date', 'location__name', 'person_in_charge', 'comments')

class DailyReportEntryAdmin(admin.ModelAdmin):
    list_display = ('report', 'product', 'quantity', 'sales_quantity', 'remaining_number', 'total_sales', 'sold_out', 'popular', 'unpopular')
    list_filter = ('report', 'product', 'sold_out', 'popular', 'unpopular')
    search_fields = ('report__date', 'product__name', 'report__location__name')

class OthersItemAdmin(admin.ModelAdmin):
    list_display = ('no', 'name', 'price')
    list_filter = ('no', 'name', 'price')

class ShiftRequestAdmin(admin.ModelAdmin):
    list_display = ('user', 'last_name', 'first_name', 'is_off','date')
    list_filter = ('user', 'last_name', 'first_name', 'is_off','date')

class HolidayAdmin(admin.ModelAdmin):
    list_display = ('date', 'description')


class ReportMessageInline(admin.TabularInline):
    model = ReportMessage
    fk_name = 'report'
    extra = 0
    readonly_fields = ('admin_user', 'field_target', 'message_type', 'body', 'emoji',
                       'sender_role', 'parent', 'is_read', 'created_at')
    can_delete = False


class ReportMessageAdmin(admin.ModelAdmin):
    list_display = ('report', 'admin_user', 'field_target', 'message_type', 'sender_role',
                    'is_read', 'created_at', 'body_preview')
    list_filter = ('message_type', 'sender_role', 'is_read', 'field_target')
    search_fields = ('body', 'report__location', 'admin_user__last_name', 'admin_user__first_name')
    readonly_fields = ('created_at',)
    raw_id_fields = ('report', 'admin_user', 'sender', 'parent')

    def body_preview(self, obj):
        if obj.message_type == 'reaction':
            return obj.emoji
        return (obj.body or '')[:50]
    body_preview.short_description = '内容'


# 管理画面にモデルを登録
admin.site.register(ItemQuantity, ItemQuantityAdmin)
admin.site.register(SalesLocation, SalesLocationAdmin)
admin.site.register(Product, ProductAdmin)
admin.site.register(DailyReport, DailyReportAdmin)
admin.site.register(DailyReportEntry, DailyReportEntryAdmin)
admin.site.register(CustomUser)
admin.site.register(OthersItem, OthersItemAdmin)
admin.site.register(ShiftRequest, ShiftRequestAdmin)
admin.site.register(Holiday, HolidayAdmin)
admin.site.register(ReportMessage, ReportMessageAdmin)