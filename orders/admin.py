from django.contrib import admin
from .models import Customer, Order, OrderItem, OrderSettings, PaymentMethod


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
