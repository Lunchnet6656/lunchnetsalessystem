from sales.models import ReportMessage
from django.db.models import Q


def returned_shift_count(request):
    """差し戻され再提出が必要なシフト提出の件数を全ページに渡す（お知らせバナー用）。"""
    if not request.user.is_authenticated:
        return {'returned_shift_count': 0}
    from shifts.models import AvailabilitySubmission
    count = AvailabilitySubmission.objects.filter(
        user=request.user,
        status='RETURNED',
        period__status__in=['OPEN', 'REVIEW'],
    ).count()
    return {'returned_shift_count': count}


def unread_message_count(request):
    if not request.user.is_authenticated:
        return {'unread_admin_replies': 0, 'unread_employee_replies': 0}
    try:
        is_admin = request.user.menu_permission.can_view_daily_report_list
    except Exception:
        is_admin = False

    if is_admin:
        unread_qs = ReportMessage.objects.filter(
            admin_user=request.user, sender_role='employee', is_read=False
        )
        count = unread_qs.count()
        first_msg = unread_qs.select_related('report').order_by('-created_at').first()
        first_report_pk = first_msg.report_id if first_msg else None
        return {
            'unread_admin_replies': 0,
            'unread_employee_replies': count,
            'unread_employee_reply_report_pk': first_report_pk,
        }

    full_name = f"{request.user.last_name} {request.user.first_name}".strip()
    count = ReportMessage.objects.filter(
        Q(report__submitted_by=request.user) |
        Q(report__submitted_by__isnull=True, report__person_in_charge=full_name),
        sender_role='admin',
        is_read=False
    ).count()
    return {'unread_admin_replies': count, 'unread_employee_replies': 0}


def price_master_status(request):
    """価格・メニュー設定の権限がある人にだけ、要確認のメニュー数と未確認の週を全ページに渡す（サイドバーとTOP用）。"""
    user = request.user
    try:
        allowed = user.is_authenticated and user.menu_permission.can_view_price_master
    except Exception:
        allowed = False
    if not allowed:
        return {}
    from django.utils import timezone
    from sales.menu_week import week_alerts
    from sales.models import MenuProfile
    return {
        "pm_needs_check_count": MenuProfile.objects.filter(confirmed=False).count(),
        "pm_week_alerts": week_alerts(timezone.localdate()),
    }
