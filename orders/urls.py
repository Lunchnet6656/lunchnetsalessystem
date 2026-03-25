from django.urls import path
from . import views

app_name = 'orders'

urlpatterns = [
    # Orders
    path('list/', views.order_list, name='order_list'),
    path('create/', views.order_create, name='order_create'),
    path('create/<int:customer_id>/', views.order_create, name='order_create_for_customer'),
    path('<int:pk>/', views.order_detail, name='order_detail'),
    path('<int:pk>/edit/', views.order_edit, name='order_edit'),
    path('<int:pk>/delete/', views.order_delete, name='order_delete'),
    path('<int:pk>/pdf/', views.order_pdf, name='order_pdf'),
    path('<int:pk>/print/', views.order_print_preview, name='order_print'),
    path('<int:pk>/receipt/print/', views.receipt_print_preview, name='receipt_print'),
    path('<int:pk>/receipt/pdf/', views.receipt_pdf, name='receipt_pdf'),

    # Regular order dashboard
    path('regular/', views.regular_order_dashboard, name='regular_dashboard'),

    # Delivery list
    path('delivery-list/', views.delivery_list, name='delivery_list'),
    path('delivery-list/<int:pk>/', views.delivery_list_by_bin, name='delivery_list_by_bin'),

    # Customers
    path('customers/', views.customer_list, name='customer_list'),
    path('customers/create/', views.customer_create, name='customer_create'),
    path('customers/<int:pk>/', views.customer_detail, name='customer_detail'),
    path('customers/<int:pk>/edit/', views.customer_edit, name='customer_edit'),
    path('customers/<int:pk>/delete/', views.customer_delete, name='customer_delete'),

    # Payment Methods
    path('payment-methods/', views.payment_method_list, name='payment_method_list'),
    path('payment-methods/create/', views.payment_method_create, name='payment_method_create'),
    path('payment-methods/<int:pk>/edit/', views.payment_method_edit, name='payment_method_edit'),
    path('payment-methods/<int:pk>/delete/', views.payment_method_delete, name='payment_method_delete'),

    # Settings
    path('settings/', views.order_settings, name='order_settings'),

    # Extra Products
    path('extra-products/create/', views.extra_product_create, name='extra_product_create'),
    path('extra-products/<int:pk>/edit/', views.extra_product_edit, name='extra_product_edit'),
    path('extra-products/<int:pk>/delete/', views.extra_product_delete, name='extra_product_delete'),

    # Delivery Bins
    path('delivery-bins/create/', views.delivery_bin_create, name='delivery_bin_create'),
    path('delivery-bins/<int:pk>/edit/', views.delivery_bin_edit, name='delivery_bin_edit'),
    path('delivery-bins/<int:pk>/delete/', views.delivery_bin_delete, name='delivery_bin_delete'),

    # API
    path('api/customer/<int:pk>/info/', views.api_customer_info, name='api_customer_info'),
    path('api/products/<str:date>/', views.api_products, name='api_products'),
    path('api/check-duplicate/', views.api_check_duplicate, name='api_check_duplicate'),
    path('api/order-totals/<str:date>/', views.api_order_totals, name='api_order_totals'),
    path('api/extra-products/', views.api_extra_products, name='api_extra_products'),
]
