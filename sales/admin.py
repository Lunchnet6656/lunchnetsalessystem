from django.contrib import admin

from .models import SalesLocation, Product, ItemQuantity, DailyReport, DailyReportEntry, CustomUser, OthersItem
from django.contrib.auth.admin import UserAdmin

class CustomUserAdmin(UserAdmin):
    fieldsets = UserAdmin.fieldsets + (
        (None, {'fields': ('full_name',)}),
    )

class SalesLocationAdmin(admin.ModelAdmin):
    list_display = ('no', 'name', 'type', 'price_type', 'service_name', 'service_price', 'direct_return')
    search_fields = ('no','name', 'direct_return')


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

# 管理画面にモデルを登録
admin.site.register(ItemQuantity, ItemQuantityAdmin)
admin.site.register(SalesLocation, SalesLocationAdmin)
admin.site.register(Product, ProductAdmin)
admin.site.register(DailyReport, DailyReportAdmin)
admin.site.register(DailyReportEntry, DailyReportEntryAdmin)
admin.site.register(CustomUser)
admin.site.register(OthersItem, OthersItemAdmin)