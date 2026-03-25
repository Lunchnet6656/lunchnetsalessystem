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

    # Regular order dashboard
    path('regular/', views.regular_order_dashboard, name='regular_dashboard'),

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

    # API
    path('api/customer/<int:pk>/info/', views.api_customer_info, name='api_customer_info'),
    path('api/products/<str:date>/', views.api_products, name='api_products'),
    path('api/check-duplicate/', views.api_check_duplicate, name='api_check_duplicate'),
    path('api/order-totals/<str:date>/', views.api_order_totals, name='api_order_totals'),
]
