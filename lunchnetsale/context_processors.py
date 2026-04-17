from sales.models import ReportMessage
from django.db.models import Q


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
