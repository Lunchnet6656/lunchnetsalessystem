from django.urls import path

from . import views

app_name = 'shifts'

urlpatterns = [
    # スタッフ
    path('period/select/', views.select_period, name='select_period'),
    path('period/current/submit/', views.submit_availability, name='submit_availability'),
    path('period/<int:period_id>/submit/', views.submit_availability, name='submit_availability_for_period'),
    path('mine/', views.my_submissions, name='my_submissions'),
    path('schedule/current/', views.view_schedule, name='view_schedule'),
    path('rules/', views.shift_rules, name='shift_rules'),

    # 管理者
    path('admin/company-holidays/', views.admin_company_holidays, name='admin_company_holidays'),
    path('admin/periods/', views.admin_periods, name='admin_periods'),
    path('admin/dashboard/', views.admin_dashboard, name='admin_dashboard'),
    path('admin/assign/<str:target_date>/', views.admin_daily_assignment, name='admin_daily_assignment'),

    path('admin/submissions/<int:period_id>/', views.admin_review_submissions, name='admin_review_submissions'),
    path('admin/periods/<int:period_id>/export-csv/', views.admin_export_submissions_csv, name='admin_export_submissions_csv'),

    # 管理者 代理提出
    path('admin/proxy-submit/<int:user_id>/', views.submit_availability, name='proxy_submit'),
    path('admin/proxy-submit/<int:user_id>/period/<int:period_id>/', views.submit_availability, name='proxy_submit_for_period'),

    # 管理者 プロフィール管理
    path('admin/profiles/', views.admin_user_profiles, name='admin_user_profiles'),
    path('admin/profiles/<int:user_id>/', views.admin_edit_profile, name='admin_edit_profile'),

    # 管理者 期間別割当グリッド
    path('admin/period-assign/<int:period_id>/', views.admin_period_assignment, name='admin_period_assignment'),
    path('admin/api/assignment/', views.api_assignment_update, name='api_assignment_update'),
    path('admin/api/autofill/<int:period_id>/', views.api_autofill, name='api_autofill'),
    path('admin/api/shared-notes/<int:period_id>/', views.api_save_shared_notes, name='api_save_shared_notes'),

    # 管理者 外部スタッフ管理
    path('admin/external-staff/', views.admin_external_staff, name='admin_external_staff'),
    path('admin/external-availability/<int:period_id>/', views.admin_external_availability, name='admin_external_availability'),

    # 管理者 しきい値設定
    path('admin/shift-settings/', views.admin_shift_settings, name='admin_shift_settings'),

    # 管理者 通知テンプレート設定
    path('admin/notification-settings/', views.admin_notification_settings, name='admin_notification_settings'),

    # 管理者 販売場所設定
    path('admin/location-settings/', views.admin_location_settings, name='admin_location_settings'),

    # 管理者 同乗ルート管理
    path('admin/carpool-routes/', views.admin_carpool_routes, name='admin_carpool_routes'),

    # ユーザー設定（LINE連携・通知設定）
    path('settings/', views.user_settings, name='user_settings'),

    # LINE Webhook
    path('line-webhook/', views.line_webhook, name='line_webhook'),
]
