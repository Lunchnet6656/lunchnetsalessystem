from django.urls import path

from . import dev_views, manage_views, views

app_name = "stamps"

urlpatterns = [
    # --- 管理画面（運営用・シェル＋スタンプモジュール）。token ルートより先に置く。 ---
    path("manage/", manage_views.dashboard, name="manage_dashboard"),
    path("manage/visits/", manage_views.visits, name="manage_visits"),
    path("manage/analytics/", manage_views.analytics, name="manage_analytics"),
    path("manage/friends/", manage_views.friends, name="manage_friends"),
    path("manage/tag-settings/", manage_views.tag_settings, name="manage_tag_settings"),
    path("manage/richmenu/", manage_views.richmenu, name="manage_richmenu"),
    path("manage/richmenu/image", manage_views.richmenu_image, name="manage_richmenu_image"),
    path("manage/locations/", manage_views.locations, name="manage_locations"),
    path("manage/locations/qr.pdf", manage_views.location_qr_pdf, name="manage_location_qr_pdf"),
    path("manage/locations/<int:location_id>/pop/", manage_views.location_pop, name="manage_location_pop"),
    path("manage/locations/<int:location_id>/pop-stand/", manage_views.location_stand_pop, name="manage_location_stand_pop"),
    path("manage/rewards/", manage_views.rewards, name="manage_rewards"),

    # --- 開発確認用デモ（本番は404）。token ルートより先に置く。 ---
    path("demo/coupon/", dev_views.demo_coupon, name="demo_coupon"),
    path("demo/", dev_views.stamp_demo, name="stamp_demo"),

    # --- お客様向け（LINE内導線：マイカード）。token ルートより先に置く。 ---
    path("me/", views.my_card, name="my_card"),

    # --- お客様向け（クーポン詳細・使用）。token ルートより先に置く。 ---
    path("reward/<int:reward_id>/", views.coupon_detail, name="coupon_detail"),
    path("reward/<int:reward_id>/use/", views.reward_use, name="reward_use"),

    # --- お客様向け（店舗別QRトークン）。最後に置く＝管理/特典ルートと衝突させない。 ---
    path("<str:token>/", views.stamp_page, name="stamp_page"),
    path("<str:token>/scan/", views.stamp_scan, name="stamp_scan"),
]
