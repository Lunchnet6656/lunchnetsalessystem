"""lunchnetsale URL Configuration

The `urlpatterns` list routes URLs to views. For more information please see:
    https://docs.djangoproject.com/en/4.0/topics/http/urls/
Examples:
Function views
    1. Add an import:  from my_app import views
    2. Add a URL to urlpatterns:  path('', views.home, name='home')
Class-based views
    1. Add an import:  from other_app.views import Home
    2. Add a URL to urlpatterns:  path('', Home.as_view(), name='home')
Including another URLconf
    1. Import the include() function: from django.urls import include, path
    2. Add a URL to urlpatterns:  path('blog/', include('blog.urls'))
"""
# lunchnetsale/urls.py
from django.contrib import admin
from django.urls import path
from . import views
from django.contrib.auth import views as auth_views

urlpatterns = [
    path('admin/', admin.site.urls),
    path('login/', views.login_view, name='login'),
    path('logout/', auth_views.LogoutView.as_view(), name='logout'),
    path('dashboard/', views.dashboard_view, name='dashboard'),
    path('upload/', views.upload_view, name='upload'),

    path('daily_report/', views.daily_report_view, name='daily_report'), #日計表のURL
    path('submission_complete/', views.submission_complete_view, name='submission_complete'),  # 完了ページのURL

    path('daily_report_list/', views.daily_report_list, name='daily_report_list'),
    path('daily_report_list/<str:date>/', views.daily_report_detail, name='daily_report_detail'),  # 日付をキーにした詳細ページ
    path('daily_report_list/<int:pk>/edit/', views.daily_report_edit, name='daily_report_edit'),
    path('daily_report_list/<int:pk>/delete/', views.daily_report_delete, name='daily_report_delete'),

    path('daily_report_detail_rol/', views.daily_report_detail_rol, name='daily_report_detail_rol'), #一般用日計表送信確認
    path('daily_report_detail_rol/<int:pk>/edit/', views.daily_report_edit_rol, name='daily_report_edit_rol'),#一般用日計表明細確認

    path('products/', views.product_list_view, name='product_list'),
    path('products/<str:week>/', views.product_detail_view, name='product_detail'),
    path('products/delete/<str:week>/', views.delete_product_view, name='delete_product'),

    path('location_list', views.location_list_view, name='location_list'),

    path('item_quantity_list/', views.item_quantity_list_view, name='item_quantity_list'),
    path('item_quantity_list/<str:date>/', views.item_quantity_detail_view, name='item_quantity_detail'),
    path('item_quantity_list/update/<int:pk>/', views.item_quantity_update_view, name='item_quantity_update'),

    path('performance_data/', views.performance_data_view, name='performance_data'),
    path('performance_data/<int:year>/<int:month>/<int:day>/', views.menu_sales_performance_view, name='menu_sales_performance'),

    path('performance_by_location/', views.performance_by_location_view, name='performance_by_location'),
    path('download_csv/', views.download_csv, name='download_csv'),
    path('download_csv_allreport/', views.download_csv_allreport, name='download_csv_allreport'),
    path('location/<int:location_id>/performance/<int:search_year>/<int:search_month>/', views.location_performance_view, name='location_performance'),

    path('menu-history/', views.menu_history_view, name='menu_history'),

]
