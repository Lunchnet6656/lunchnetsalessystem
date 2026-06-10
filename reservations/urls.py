from django.urls import path

from . import manage_views, views

app_name = "reservations"

urlpatterns = [
    # 控えページ（予約番号）。token より先に置いてマッチ衝突を避ける。
    path("complete/<str:reservation_number>/", views.reserve_complete, name="reserve_complete"),
    path("complete/<str:reservation_number>/done/", views.reserve_self_handover, name="reserve_self_handover"),
    path("complete/<str:reservation_number>/cancel/", views.reserve_cancel, name="reserve_cancel"),
    # 予約履歴（本人のLINEで自分の予約一覧）。token より先に置く。
    path("mine/", views.my_reservations, name="my_reservations"),
    # 初回会員登録（お名前設定）＋登録判定API。token より先に置く。
    path("register/", views.register_member, name="register_member"),
    path("whoami/", views.reserve_whoami, name="reserve_whoami"),
    # 社内向け管理・受取画面（S4）。すべて token ルートより先に置く。
    path("manage/", manage_views.manage_list, name="manage_list"),
    path("manage/pickup/", manage_views.manage_pickup, name="manage_pickup"),
    path("manage/pickup/update/", manage_views.manage_pickup_update, name="manage_pickup_update"),
    path("manage/locations/", manage_views.manage_locations, name="manage_locations"),
    # 予約ページ（拠点QRトークン）
    path("<str:token>/", views.reserve_page, name="reserve_page"),
    path("<str:token>/submit/", views.reserve_submit, name="reserve_submit"),
]
