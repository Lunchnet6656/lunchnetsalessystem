from django.contrib import admin
from .models import (
    Customer, Order, OrderItem, OrderSettings, PaymentMethod,
    Invoice, InvoiceLine,
)


@admin.register(PaymentMethod)
class PaymentMethodAdmin(admin.ModelAdmin):
    list_display = ['name', 'sort_order', 'is_active']
    list_filter = ['is_active']
    list_editable = ['sort_order', 'is_active']


@admin.register(Customer)
class CustomerAdmin(admin.ModelAdmin):
    list_display = ['name', 'customer_type', 'company_name', 'phone', 'price_type', 'payment_method', 'is_active']
    list_filter = ['customer_type', 'price_type', 'payment_method', 'is_active']
    search_fields = ['name', 'company_name', 'phone', 'contact_person']


class OrderItemInline(admin.TabularInline):
    model = OrderItem
    extra = 1


@admin.register(Order)
class OrderAdmin(admin.ModelAdmin):
    list_display = ['order_number', 'customer', 'order_date', 'delivery_date', 'created_by']
    list_filter = ['delivery_date', 'order_date']
    search_fields = ['order_number', 'customer__name', 'customer__company_name']
    inlines = [OrderItemInline]


@admin.register(OrderItem)
class OrderItemAdmin(admin.ModelAdmin):
    list_display = ['order', 'product_name', 'quantity', 'unit_price', 'subtotal']


@admin.register(OrderSettings)
class OrderSettingsAdmin(admin.ModelAdmin):
    list_display = ['__str__', 'tax_rate', 'company_name']


class InvoiceLineInline(admin.TabularInline):
    model = InvoiceLine
    extra = 0
    fields = ['sort_order', 'line_date', 'source_type', 'label',
              'quantity', 'quantity_large', 'unit_price', 'amount']


@admin.register(Invoice)
class InvoiceAdmin(admin.ModelAdmin):
    list_display = ['invoice_number', 'customer', 'pattern', 'issue_date',
                    'period_start', 'period_end', 'net_amount', 'status']
    list_display_links = ['customer']
    list_editable = ['invoice_number', 'status']
    list_filter = ['status', 'pattern', 'issue_date']
    search_fields = ['invoice_number', 'customer__name', 'customer__company_name']
    date_hierarchy = 'issue_date'
    ordering = ['-issue_date', '-created_at']
    raw_id_fields = ['customer', 'issued_by']
    readonly_fields = ['created_at', 'updated_at']
    inlines = [InvoiceLineInline]
    fieldsets = (
        (None, {
            'fields': ['invoice_number', 'customer', 'status', 'pattern']
        }),
        ('日付・期間', {
            'fields': ['issue_date', 'due_date', 'period_start', 'period_end']
        }),
        ('金額（発行時スナップショット）', {
            'fields': ['tax_rate', 'charge_total', 'credit_total',
                       'net_amount', 'tax_amount']
        }),
        ('発行情報', {
            'fields': ['registration_number', 'bank_info', 'notes',
                       'void_reason', 'issued_by', 'created_at', 'updated_at']
        }),
    )
