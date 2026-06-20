from django.urls import path

from . import views

app_name = "attendance"

urlpatterns = [
    path("", views.punch_page, name="punch_page"),
    path("mypage/", views.mypage, name="mypage"),
    path("mypage/state/", views.mypage_state, name="mypage_state"),
    path("mypage/detail/", views.my_detail, name="my_detail"),
    path("mypage/payslips/", views.my_payslips, name="my_payslips"),
    path("punch/", views.punch, name="punch"),
    path("punch/undo/", views.undo_punch, name="undo_punch"),
    # QR打刻：共有端末スキャナー（ログイン不要）
    path("punch/scan/", views.punch_scanner, name="punch_scanner"),
    path("punch/kiosk-api/", views.punch_kiosk_api, name="punch_kiosk_api"),
    # QR打刻：トークンURLの個人打刻ページ（ログイン不要）
    path(
        "punch/<uuid:token>/",
        views.punch_by_token,
        name="punch_by_token",
    ),
    # 個人ページから自分の給与明細PDFをダウンロード（ログイン不要・トークン認証）
    path(
        "punch/<uuid:token>/payslip-pdf/",
        views.punch_payslip_pdf,
        name="punch_payslip_pdf",
    ),
    # 管理者：勤怠集計と管理（スプリント2）
    path("manage/", views.manage_dashboard, name="manage_dashboard"),
    path("manage/period/", views.manage_period, name="manage_period"),
    path(
        "manage/staff/<int:staff_id>/",
        views.manage_staff_detail,
        name="manage_staff_detail",
    ),
    path(
        "manage/staff/<int:staff_id>/save/",
        views.manage_save_day,
        name="manage_save_day",
    ),
    path(
        "manage/staff/<int:staff_id>/delete-day/",
        views.manage_delete_day,
        name="manage_delete_day",
    ),
    # 給与計算（スプリント3）
    path("manage/payroll/", views.manage_payroll, name="manage_payroll"),
    path(
        "manage/payroll/confirm/",
        views.manage_payroll_confirm,
        name="manage_payroll_confirm",
    ),
    # 当月のガソリン単価を保存
    path(
        "manage/payroll/save-gasoline/",
        views.manage_save_gasoline,
        name="manage_save_gasoline",
    ),
    # 賃金台帳（法定帳票・労基法108条）
    path(
        "manage/wage-book/<int:staff_id>/",
        views.manage_wage_book,
        name="manage_wage_book",
    ),
    path(
        "manage/wage-book/<int:staff_id>/csv/",
        views.manage_wage_book_csv,
        name="manage_wage_book_csv",
    ),
    # スタッフ新規追加（admin 脱却）
    path(
        "manage/staff/new/",
        views.manage_staff_new,
        name="manage_staff_new",
    ),
    # 既存ユーザーから勤怠スタッフを後付け
    path(
        "manage/staff/from-user/",
        views.manage_staff_from_user,
        name="manage_staff_from_user",
    ),
    # 労働者名簿（法定帳票・労基法107条）
    path(
        "manage/staff-roster/",
        views.manage_staff_roster,
        name="manage_staff_roster",
    ),
    path(
        "manage/staff-roster/csv/",
        views.manage_staff_roster_csv,
        name="manage_staff_roster_csv",
    ),
    path(
        "manage/staff-roster/<int:staff_id>/edit/",
        views.manage_staff_roster_edit,
        name="manage_staff_roster_edit",
    ),
    # 給与明細PDF（スプリント4）
    path(
        "manage/payroll/bulk-pdf/",
        views.manage_payslip_bulk_pdf,
        name="manage_payslip_bulk_pdf",
    ),
    path(
        "manage/payroll/<int:staff_id>/pdf/",
        views.manage_payslip_pdf,
        name="manage_payslip_pdf",
    ),
    path(
        "manage/payroll/<int:staff_id>/",
        views.manage_payslip_detail,
        name="manage_payslip_detail",
    ),
    # 給与明細の勤怠インライン編集（動的プレビュー＋保存／取り消し）
    path(
        "manage/payroll/<int:staff_id>/preview/",
        views.manage_payslip_preview,
        name="manage_payslip_preview",
    ),
    path(
        "manage/payroll/<int:staff_id>/save-hours/",
        views.manage_payslip_save_hours,
        name="manage_payslip_save_hours",
    ),
    path(
        "manage/payroll/<int:staff_id>/clear-hours/",
        views.manage_payslip_clear_hours,
        name="manage_payslip_clear_hours",
    ),
    path(
        "manage/payroll/<int:staff_id>/save-adjustments/",
        views.manage_payslip_save_adjustments,
        name="manage_payslip_save_adjustments",
    ),
    path(
        "manage/staff/<int:staff_id>/profile/",
        views.manage_staff_profile,
        name="manage_staff_profile",
    ),
    path(
        "manage/staff-settings/",
        views.manage_staff_settings,
        name="manage_staff_settings",
    ),
    path("manage/settings/", views.manage_settings, name="manage_settings"),
    # 打刻QRの一括印刷（A4で全スタッフ分）
    path(
        "manage/punch-qrs/",
        views.manage_punch_qrs,
        name="manage_punch_qrs",
    ),
    path(
        "manage/punch-qrs/<int:staff_id>/rotate/",
        views.manage_rotate_punch_token,
        name="manage_rotate_punch_token",
    ),
    path(
        "manage/min-wage-update/",
        views.manage_min_wage_update,
        name="manage_min_wage_update",
    ),
]
