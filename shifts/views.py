import csv
import datetime
import random
import string
import urllib.parse

from django.contrib import messages
from django.contrib.auth import get_user_model
from django.contrib.admin.views.decorators import staff_member_required
from django.contrib.auth.decorators import login_required
from django.db import models
from django.db.models import Count, Q
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

from sales.models import SalesLocation

from .models import (
    AvailabilityDay,
    AvailabilitySubmission,
    CompanyHoliday,
    SchedulePeriod,
    ShiftAssignment,
    ShiftNotification,
    ShiftSettings,
    UserProfile,
)
from .notifications import notify_manual_reminder, notify_period_open
from .utils import auto_close_expired_periods, can_submit_for_period, generate_date_range, get_holiday_name, is_holiday

User = get_user_model()

WEEKDAY_NAMES = ['月', '火', '水', '木', '金', '土', '日']


# ---------- 期間選択 ----------

@login_required
def select_period(request):
    is_admin = request.user.is_staff
    now = timezone.now()

    # 締切を過ぎたOPEN期間を自動的にREVIEWへ変更
    auto_close_expired_periods()

    if is_admin:
        periods = SchedulePeriod.objects.filter(status__in=['OPEN', 'REVIEW'])
    else:
        # OPEN（受付中）+ REVIEW（締切済み表示用）
        periods = SchedulePeriod.objects.filter(
            status__in=['OPEN', 'REVIEW'],
            submission_open_at__lte=now,
        )

    # 期間が1つだけなら直接提出画面へリダイレクト
    if periods.count() == 1:
        period = periods.first()
        return redirect('shifts:submit_availability_for_period', period_id=period.pk)

    # 各期間の提出状況を取得
    period_data = []
    for period in periods:
        is_closed = period.status == 'REVIEW'

        submission = AvailabilitySubmission.objects.filter(
            user=request.user, period=period,
        ).first()
        if submission and submission.submitted_at:
            if submission.status == 'RETURNED':
                submit_status = 'returned'
                submit_label = '差戻し'
            elif submission.status == 'APPROVED':
                submit_status = 'approved'
                submit_label = '承認済'
            else:
                submit_status = 'submitted'
                submit_label = '提出済'
        else:
            submit_status = 'not_submitted'
            submit_label = '未提出'

        period_data.append({
            'period': period,
            'submit_status': submit_status,
            'submit_label': submit_label,
            'is_closed': is_closed,
        })

    # 直近24時間以内に作成された OPEN 期間があればバナーを表示
    from datetime import timedelta
    new_period_banner = SchedulePeriod.objects.filter(
        status='OPEN',
        submission_open_at__gte=now - timedelta(hours=24),
    ).order_by('-submission_open_at').first()

    context = {
        'period_data': period_data,
        'is_admin': is_admin,
        'new_period_banner': new_period_banner,
    }
    return render(request, 'shifts/select_period.html', context)


# ---------- 6-1: シフト希望提出 (スタッフ & 管理者代理提出) ----------

@login_required
def submit_availability(request, period_id=None, user_id=None):
    is_admin = request.user.is_staff
    target_user = request.user

    # 管理者代理提出
    if user_id is not None:
        if not is_admin:
            messages.error(request, '権限がありません。')
            return redirect('shifts:my_submissions')
        target_user = get_object_or_404(User, pk=user_id)

    # period_id が指定されていない場合は select_period へリダイレクト
    if period_id is None:
        return redirect('shifts:select_period')

    period = get_object_or_404(SchedulePeriod, pk=period_id)

    # 締切を過ぎたOPEN期間を自動的にREVIEWへ変更
    auto_close_expired_periods()
    period.refresh_from_db()

    # 締切済み判定: DB ステータスが REVIEW、または OPEN でも締切時刻を過ぎている場合
    now = timezone.now()
    is_closed = not is_admin and (
        period.status == 'REVIEW'
        or (period.status == 'OPEN' and now > period.submission_close_at)
    )

    if not can_submit_for_period(period, target_user, is_admin) and not is_closed:
        messages.warning(request, 'この期間は提出できません。')
        return redirect('shifts:my_submissions')

    # 締切済みの場合、POSTは拒否
    if is_closed and request.method == 'POST':
        messages.warning(request, '応募は締め切られています。')
        return redirect('shifts:select_period')

    # ユーザープロフィール取得
    profile, _ = UserProfile.objects.get_or_create(user=target_user)
    work_pattern = profile.work_pattern
    fixed_weekday_list = [int(x) for x in profile.fixed_weekdays.split(',') if x.strip().isdigit()]

    # 日付リスト生成
    dates = list(generate_date_range(period.start_date, period.end_date))

    # ヒートマップ用データ
    shift_settings = ShiftSettings.load()
    requires_drive_count = SalesLocation.objects.filter(requires_drive=True, excluded_from_shift=False).count()

    date_info = []
    for d in dates:
        holiday = is_holiday(d)
        holiday_name = get_holiday_name(d) if holiday else ''
        is_fixed = d.weekday() in fixed_weekday_list
        requires_reason = (
            work_pattern == 'FULL'
            or (work_pattern == 'PART' and is_fixed)
        )

        # ヒートマップ: 自分以外のOFF数・ドライバー不足を判定
        off_count = 0
        heatmap_status = ''
        heatmap_label = ''
        if not holiday:
            off_count = AvailabilityDay.objects.filter(
                submission__period=period,
                submission__status__in=['SUBMITTED', 'APPROVED'],
                date=d,
                availability='OFF',
            ).count()

            work_days_qs = AvailabilityDay.objects.filter(
                submission__period=period,
                submission__status__in=['SUBMITTED', 'APPROVED'],
                date=d,
                availability='WORK',
            )

            available_user_ids = work_days_qs.values_list('submission__user_id', flat=True)
            driver_count = UserProfile.objects.filter(user_id__in=available_user_ids, can_drive=True).count()
            driver_shortage = driver_count < requires_drive_count

            if driver_shortage:
                heatmap_status = 'driver_shortage'
                heatmap_label = '運転手不足'
            elif off_count >= shift_settings.danger_threshold:
                heatmap_status = 'danger'
                heatmap_label = '危険'
            elif off_count >= shift_settings.warning_threshold:
                heatmap_status = 'warning'
                heatmap_label = '注意'
            else:
                heatmap_status = 'ok'
                heatmap_label = 'OK'

        date_info.append({
            'date': d,
            'weekday': WEEKDAY_NAMES[d.weekday()],
            'is_holiday': holiday,
            'holiday_name': holiday_name,
            'is_fixed_weekday': is_fixed,
            'requires_reason': requires_reason,
            'off_count': off_count,
            'heatmap_status': heatmap_status,
            'heatmap_label': heatmap_label,
        })

    # 既存データ取得
    submission, _ = AvailabilitySubmission.objects.get_or_create(
        user=target_user, period=period,
    )
    existing_days = {
        day.date: day
        for day in submission.days.all()
    }

    # プリフィル（work_pattern に基づくデフォルト値）
    # 未提出(DRAFT, submitted_at=None)の場合は既存データを無視してデフォルトを再適用
    use_existing = submission.submitted_at is not None
    for info in date_info:
        day = existing_days.get(info['date'])
        if day and use_existing:
            info['availability'] = day.availability
            info['absence_category'] = day.absence_category
            info['substitute_user_id'] = day.substitute_user_id
            info['comment'] = day.comment
        else:
            if info['is_holiday']:
                default_avail = 'OFF'
            elif work_pattern == 'FULL':
                default_avail = 'WORK'
            elif work_pattern == 'PART':
                if fixed_weekday_list:
                    default_avail = 'WORK' if info['is_fixed_weekday'] else 'OFF'
                else:
                    default_avail = 'OFF'
            elif work_pattern == 'HELPER':
                default_avail = 'OFF'
            else:
                default_avail = 'WORK'
            info['availability'] = default_avail
            info['absence_category'] = ''
            info['substitute_user_id'] = None
            info['comment'] = ''

    # ユーザー一覧（代替者候補 & 代理提出用）
    all_users = User.objects.filter(is_active=True).order_by('last_name', 'first_name')

    if request.method == 'POST':
        now = timezone.now()
        is_proxy = user_id is not None
        if is_proxy:
            submission.submitted_by_admin = True
            submission.is_late_submission = True

        validation_errors = []
        day_data_list = []

        for d in dates:
            date_str = d.strftime('%Y-%m-%d')
            avail = request.POST.get(f'avail_{date_str}', 'WORK')
            # 休日は強制OFF
            if is_holiday(d):
                avail = 'OFF'
            absence_cat = ''
            sub_user_id = None
            comment = ''
            if avail == 'OFF' and not is_holiday(d):
                absence_cat = request.POST.get(f'absence_{date_str}', '')
                if absence_cat == 'SUBSTITUTE':
                    sub_id = request.POST.get(f'substitute_{date_str}')
                    if sub_id:
                        sub_user_id = int(sub_id)
                comment = request.POST.get(f'comment_{date_str}', '')

                # 理由必須: 本来WORKの日のみ
                requires_reason = (
                    work_pattern == 'FULL'
                    or (work_pattern == 'PART' and d.weekday() in fixed_weekday_list)
                )
                if requires_reason and not absence_cat:
                    validation_errors.append(
                        f'{d.strftime("%-m/%-d")}({WEEKDAY_NAMES[d.weekday()]}) の休み理由を選択してください。'
                    )

            day_data_list.append({
                'date': d,
                'availability': avail,
                'absence_category': absence_cat,
                'substitute_user_id': sub_user_id,
                'comment': comment,
            })

        if validation_errors:
            for err in validation_errors:
                messages.error(request, err)
        else:
            for data in day_data_list:
                AvailabilityDay.objects.update_or_create(
                    submission=submission, date=data['date'],
                    defaults={
                        'availability': data['availability'],
                        'absence_category': data['absence_category'],
                        'substitute_user_id': data['substitute_user_id'],
                        'comment': data['comment'],
                    },
                )

            submission.remarks = request.POST.get('remarks', '')
            submission.status = 'SUBMITTED'
            submission.submitted_at = now
            submission.save()
            messages.success(request, 'シフト希望を提出しました。')
            if is_proxy:
                return redirect('shifts:admin_dashboard')
            return redirect('shifts:my_submissions')

    context = {
        'period': period,
        'date_info': date_info,
        'submission': submission,
        'target_user': target_user,
        'is_proxy': user_id is not None,
        'all_users': all_users,
        'work_pattern': work_pattern,
        'fixed_weekday_list': fixed_weekday_list,
        'is_closed': is_closed,
    }
    return render(request, 'shifts/submit_availability.html', context)


# ---------- 6-2: 提出状況確認 ----------

@login_required
def my_submissions(request):
    periods = SchedulePeriod.objects.all()[:3]
    submissions = []
    for period in periods:
        sub = AvailabilitySubmission.objects.filter(
            user=request.user, period=period,
        ).first()
        days = sub.days.all() if sub else []
        submissions.append({
            'period': period,
            'submission': sub,
            'days': days,
        })
    context = {'submissions': submissions}
    return render(request, 'shifts/my_submissions.html', context)


# ---------- 6-3: 確定シフト閲覧 ----------

@login_required
def view_schedule(request):
    period = SchedulePeriod.objects.filter(status='PUBLISHED').first()
    if not period:
        period = SchedulePeriod.objects.filter(status='FIXED').first()
    if not period:
        messages.info(request, '公開済みのシフトはありません。')
        return render(request, 'shifts/view_schedule.html', {'assignments': [], 'period': None})

    assignments = ShiftAssignment.objects.filter(
        date__gte=period.start_date,
        date__lte=period.end_date,
    ).select_related('sales_location', 'user')

    context = {
        'period': period,
        'assignments': assignments,
        'current_user': request.user,
    }
    return render(request, 'shifts/view_schedule.html', context)


# ---------- 6-4: 会社カレンダー管理 ----------

@staff_member_required
def admin_company_holidays(request):
    if request.method == 'POST':
        action = request.POST.get('action')
        if action == 'add':
            add_type = request.POST.get('add_type', 'single')
            name = request.POST.get('name', '')
            if add_type == 'range':
                start_str = request.POST.get('start_date')
                end_str = request.POST.get('end_date')
                if start_str and end_str:
                    start_d = datetime.date.fromisoformat(start_str)
                    end_d = datetime.date.fromisoformat(end_str)
                    count = 0
                    current = start_d
                    while current <= end_d:
                        _, created = CompanyHoliday.objects.get_or_create(date=current, defaults={'name': name})
                        if created:
                            count += 1
                        current += datetime.timedelta(days=1)
                    messages.success(request, f'{count}件の休日を追加しました。')
            else:
                date_str = request.POST.get('date')
                if date_str:
                    d = datetime.date.fromisoformat(date_str)
                    CompanyHoliday.objects.get_or_create(date=d, defaults={'name': name})
                    messages.success(request, f'{d} を追加しました。')
        elif action == 'delete':
            holiday_id = request.POST.get('holiday_id')
            CompanyHoliday.objects.filter(pk=holiday_id).delete()
            messages.success(request, '削除しました。')
        return redirect('shifts:admin_company_holidays')

    holidays = CompanyHoliday.objects.all()
    context = {'holidays': holidays}
    return render(request, 'shifts/admin_company_holidays.html', context)


# ---------- 6-5: 募集期間管理 ----------

@staff_member_required
def admin_periods(request):
    auto_close_expired_periods()
    if request.method == 'POST':
        action = request.POST.get('action')
        if action == 'create':
            start_str = request.POST.get('start_date')
            end_str = request.POST.get('end_date', '')
            open_str = request.POST.get('submission_open_at')
            close_str = request.POST.get('submission_close_at')
            if start_str and open_str and close_str:
                start_date = datetime.date.fromisoformat(start_str)
                end_date = (
                    datetime.date.fromisoformat(end_str)
                    if end_str
                    else start_date + datetime.timedelta(days=13)
                )
                submission_open_at = datetime.datetime.fromisoformat(open_str)
                submission_close_at = datetime.datetime.fromisoformat(close_str)
                period = SchedulePeriod.objects.create(
                    start_date=start_date,
                    end_date=end_date,
                    submission_open_at=submission_open_at,
                    submission_close_at=submission_close_at,
                )
                # 募集開始通知を送信
                try:
                    notif = notify_period_open(period)
                    messages.success(
                        request,
                        f'募集期間を作成しました。通知送信: LINE={notif.sent_line_count}, メール={notif.sent_email_count}',
                    )
                except Exception:
                    messages.success(request, '募集期間を作成しました。（通知送信でエラーが発生しました）')
        elif action == 'notify_reminder':
            period_id = request.POST.get('period_id')
            if period_id:
                period = get_object_or_404(SchedulePeriod, pk=period_id)
                submitted_ids = AvailabilitySubmission.objects.filter(
                    period=period, submitted_at__isnull=False,
                ).values_list('user_id', flat=True)
                unsubmitted_profiles = UserProfile.objects.filter(
                    user__is_active=True,
                ).exclude(user_id__in=submitted_ids).select_related('user')
                count = unsubmitted_profiles.count()
                if count == 0:
                    messages.info(request, '全員が既に提出済みです。')
                else:
                    try:
                        notif = notify_manual_reminder(period, unsubmitted_profiles)
                        messages.success(
                            request,
                            f'未提出者 {count}名 にリマインドを送信しました。'
                            f'（LINE={notif.sent_line_count}, メール={notif.sent_email_count}）',
                        )
                    except Exception:
                        messages.error(request, 'リマインド送信中にエラーが発生しました。')
        elif action == 'change_status':
            period_id = request.POST.get('period_id')
            new_status = request.POST.get('status')
            if period_id and new_status:
                period = get_object_or_404(SchedulePeriod, pk=period_id)
                period.status = new_status
                period.save()
                messages.success(request, 'ステータスを変更しました。')
        elif action == 'delete':
            period_id = request.POST.get('period_id')
            if period_id:
                period = get_object_or_404(SchedulePeriod, pk=period_id)
                period.delete()
                messages.success(request, '募集期間を削除しました。')
        return redirect('shifts:admin_periods')

    periods = SchedulePeriod.objects.all()
    context = {'periods': periods, 'status_choices': SchedulePeriod.STATUS_CHOICES}
    return render(request, 'shifts/admin_periods.html', context)


# ---------- 6-6: ダッシュボード ----------

@staff_member_required
def admin_dashboard(request):
    auto_close_expired_periods()
    all_periods = SchedulePeriod.objects.filter(
        status__in=['OPEN', 'REVIEW', 'FIXED', 'PUBLISHED'],
    ).order_by('-start_date')

    period_id = request.GET.get('period_id')
    if period_id:
        period = all_periods.filter(pk=period_id).first()
    else:
        period = all_periods.first()

    if not period:
        messages.info(request, '対象の募集期間がありません。')
        return render(request, 'shifts/admin_dashboard.html', {'days': [], 'period': None, 'all_periods': all_periods})

    settings = ShiftSettings.load()
    dates = list(generate_date_range(period.start_date, period.end_date))
    requires_drive_count = SalesLocation.objects.filter(requires_drive=True, excluded_from_shift=False).count()

    # 提出状況集計
    submissions = AvailabilitySubmission.objects.filter(
        period=period, status__in=['SUBMITTED', 'APPROVED'],
    ).prefetch_related('days')

    # 全ユーザー一覧（未提出確認用）
    all_active_users = User.objects.filter(is_active=True)
    submitted_user_ids = submissions.values_list('user_id', flat=True)
    not_submitted_users = all_active_users.exclude(id__in=submitted_user_ids)

    days = []
    for d in dates:
        holiday = is_holiday(d)
        if holiday:
            days.append({
                'date': d,
                'weekday': WEEKDAY_NAMES[d.weekday()],
                'is_holiday': True,
                'holiday_name': get_holiday_name(d),
                'available_count': 0,
                'driver_count': 0,
                'off_count': 0,
                'driver_shortage': False,
                'heatmap_status': '',
                'heatmap_label': '',
            })
            continue

        # この日にWORKのユーザーを取得
        work_days = AvailabilityDay.objects.filter(
            submission__period=period,
            submission__status__in=['SUBMITTED', 'APPROVED'],
            date=d,
            availability='WORK',
        ).select_related('submission__user')

        # この日にOFFのユーザー数を取得
        off_count = AvailabilityDay.objects.filter(
            submission__period=period,
            submission__status__in=['SUBMITTED', 'APPROVED'],
            date=d,
            availability='OFF',
        ).count()

        available_user_ids = work_days.values_list('submission__user_id', flat=True)
        driver_count = UserProfile.objects.filter(
            user_id__in=available_user_ids,
            can_drive=True,
        ).count()

        driver_shortage = driver_count < requires_drive_count

        # ヒートマップステータス判定
        if driver_shortage:
            heatmap_status = 'driver_shortage'
            heatmap_label = '運転手不足'
        elif off_count >= settings.danger_threshold:
            heatmap_status = 'danger'
            heatmap_label = '危険'
        elif off_count >= settings.warning_threshold:
            heatmap_status = 'warning'
            heatmap_label = '注意'
        else:
            heatmap_status = 'ok'
            heatmap_label = 'OK'

        days.append({
            'date': d,
            'weekday': WEEKDAY_NAMES[d.weekday()],
            'is_holiday': False,
            'holiday_name': '',
            'available_count': work_days.count(),
            'driver_count': driver_count,
            'off_count': off_count,
            'driver_shortage': driver_shortage,
            'heatmap_status': heatmap_status,
            'heatmap_label': heatmap_label,
        })

    context = {
        'period': period,
        'days': days,
        'requires_drive_count': requires_drive_count,
        'not_submitted_users': not_submitted_users,
        'all_users': all_active_users,
        'settings': settings,
        'all_periods': all_periods,
    }
    return render(request, 'shifts/admin_dashboard.html', context)


# ---------- 6-6b: 提出状況確認・承認・却下 ----------

@staff_member_required
def admin_review_submissions(request, period_id):
    period = get_object_or_404(SchedulePeriod, pk=period_id)
    submissions = AvailabilitySubmission.objects.filter(
        period=period,
    ).select_related('user').prefetch_related('days').order_by('user__last_name', 'user__first_name')

    if request.method == 'POST':
        action = request.POST.get('action')
        if action == 'approve':
            ids = request.POST.getlist('submission_ids')
            if ids:
                AvailabilitySubmission.objects.filter(
                    pk__in=ids, period=period, status='SUBMITTED',
                ).update(status='APPROVED')
                messages.success(request, f'{len(ids)}件の提出を承認しました。')
        elif action == 'reject':
            sub_id = request.POST.get('submission_id')
            admin_note = request.POST.get('admin_note', '')
            if sub_id:
                sub = get_object_or_404(AvailabilitySubmission, pk=sub_id, period=period)
                sub.status = 'RETURNED'
                sub.admin_note = admin_note
                sub.save()
                messages.success(request, f'{sub.user.get_full_name() or sub.user.username} の提出を差戻しました。')
        return redirect('shifts:admin_review_submissions', period_id=period_id)

    # 各提出に出勤/休み日数を集計
    submission_data = []
    for sub in submissions:
        days = sub.days.all()
        work_count = sum(1 for d in days if d.availability == 'WORK')
        off_count = sum(1 for d in days if d.availability == 'OFF')
        days_with_weekday = []
        for d in days:
            d.weekday_name = WEEKDAY_NAMES[d.date.weekday()]
            days_with_weekday.append(d)
        submission_data.append({
            'submission': sub,
            'work_count': work_count,
            'off_count': off_count,
            'days': days_with_weekday,
        })

    context = {
        'period': period,
        'submission_data': submission_data,
    }
    return render(request, 'shifts/admin_review_submissions.html', context)


# ---------- 6-7: 日別割当 ----------

@staff_member_required
def admin_daily_assignment(request, target_date):
    d = datetime.date.fromisoformat(target_date)

    if is_holiday(d):
        context = {
            'date': d,
            'weekday': WEEKDAY_NAMES[d.weekday()],
            'is_holiday': True,
            'holiday_name': get_holiday_name(d),
        }
        return render(request, 'shifts/admin_daily_assignment.html', context)

    # 対象期間
    period = SchedulePeriod.objects.filter(
        start_date__lte=d, end_date__gte=d,
    ).first()

    # この日にWORKのユーザーID
    available_user_ids = []
    if period:
        available_user_ids = list(
            AvailabilityDay.objects.filter(
                submission__period=period,
                submission__status__in=['SUBMITTED', 'APPROVED'],
                date=d,
                availability='WORK',
            ).values_list('submission__user_id', flat=True)
        )

    # 売り場をpriority順で取得（シフト対象外を除外）
    locations = SalesLocation.objects.filter(excluded_from_shift=False).order_by(
        models.Case(
            models.When(priority='S', then=0),
            models.When(priority='A', then=1),
            models.When(priority='B', then=2),
            default=3,
            output_field=models.IntegerField(),
        ),
        'no',
    )

    # 既存割当
    existing = {
        sa.sales_location_id: sa
        for sa in ShiftAssignment.objects.filter(date=d).select_related('user')
    }

    # 候補者リスト構築
    available_users = User.objects.filter(id__in=available_user_ids).select_related('profile')
    driver_users = [u for u in available_users if hasattr(u, 'profile') and u.profile.can_drive]
    non_driver_users = [u for u in available_users if not hasattr(u, 'profile') or not u.profile.can_drive]

    location_data = []
    for loc in locations:
        assignment = existing.get(loc.id)
        if loc.requires_drive:
            candidates = driver_users
        else:
            candidates = list(available_users)
        location_data.append({
            'location': loc,
            'assignment': assignment,
            'candidates': candidates,
            'assigned_user_id': assignment.user_id if assignment else None,
        })

    if request.method == 'POST':
        errors = []
        for loc in locations:
            user_id_str = request.POST.get(f'loc_{loc.id}')
            if not user_id_str:
                # 割当解除
                ShiftAssignment.objects.filter(date=d, sales_location=loc).delete()
                continue
            user_id = int(user_id_str)
            assigned_user = User.objects.get(pk=user_id)

            # ドライバー制約チェック
            if loc.requires_drive:
                profile = getattr(assigned_user, 'profile', None)
                if not profile or not profile.can_drive:
                    errors.append(f'{loc.name} は運転必須ですが、{assigned_user.get_full_name()} は運転不可です。')
                    continue

            ShiftAssignment.objects.update_or_create(
                date=d, sales_location=loc,
                defaults={
                    'user': assigned_user,
                    'status': 'DRAFT',
                },
            )

        if errors:
            for e in errors:
                messages.error(request, e)
        else:
            messages.success(request, f'{d} の割当を保存しました。')
        return redirect('shifts:admin_daily_assignment', target_date=target_date)

    context = {
        'date': d,
        'weekday': WEEKDAY_NAMES[d.weekday()],
        'is_holiday': False,
        'location_data': location_data,
        'period': period,
    }
    return render(request, 'shifts/admin_daily_assignment.html', context)


# ---------- 6-8: ユーザープロフィール管理 ----------

@staff_member_required
def admin_user_profiles(request):
    users = User.objects.filter(is_active=True).order_by('last_name', 'first_name')
    user_data = []
    for u in users:
        profile, _ = UserProfile.objects.get_or_create(user=u)
        user_data.append({'user': u, 'profile': profile})
    context = {'user_data': user_data}
    return render(request, 'shifts/admin_user_profiles.html', context)


@staff_member_required
def admin_edit_profile(request, user_id):
    target_user = get_object_or_404(User, pk=user_id)
    profile, _ = UserProfile.objects.get_or_create(user=target_user)

    if request.method == 'POST':
        profile.can_drive = request.POST.get('can_drive') == 'on'
        profile.work_pattern = request.POST.get('work_pattern', 'FULL')
        profile.uses_app = request.POST.get('uses_app') == 'on'
        profile.min_shifts_per_week = int(request.POST.get('min_shifts_per_week', 0))
        profile.max_shifts_per_week = int(request.POST.get('max_shifts_per_week', 5))
        profile.fixed_weekdays = request.POST.get('fixed_weekdays', '')
        profile.save()
        messages.success(request, f'{target_user.get_full_name() or target_user.username} のプロフィールを更新しました。')
        return redirect('shifts:admin_user_profiles')

    context = {
        'target_user': target_user,
        'profile': profile,
        'work_pattern_choices': UserProfile.WORK_PATTERN_CHOICES,
    }
    return render(request, 'shifts/admin_edit_profile.html', context)


# ---------- しきい値設定 ----------

@login_required
def shift_rules(request):
    return render(request, 'shifts/rules.html')


@staff_member_required
def admin_shift_settings(request):
    settings = ShiftSettings.load()

    if request.method == 'POST':
        settings.ok_threshold = int(request.POST.get('ok_threshold', 2))
        settings.warning_threshold = int(request.POST.get('warning_threshold', 4))
        settings.danger_threshold = int(request.POST.get('danger_threshold', 6))
        settings.save()
        messages.success(request, 'しきい値設定を更新しました。')
        return redirect('shifts:admin_shift_settings')

    context = {'settings': settings}
    return render(request, 'shifts/admin_shift_settings.html', context)


# ---------- 販売場所設定 ----------

@staff_member_required
def admin_location_settings(request):
    locations = SalesLocation.objects.all().order_by('no')
    if request.method == 'POST':
        for loc in locations:
            loc.requires_drive = request.POST.get(f'requires_drive_{loc.id}') == 'on'
            loc.priority = request.POST.get(f'priority_{loc.id}', 'A')
            loc.excluded_from_shift = request.POST.get(f'excluded_from_shift_{loc.id}') == 'on'
            loc.save()
        messages.success(request, '販売場所設定を更新しました。')
        return redirect('shifts:admin_location_settings')
    context = {'locations': locations}
    return render(request, 'shifts/admin_location_settings.html', context)


# ---------- ユーザー設定（LINE連携・通知設定） ----------

@login_required
def user_settings(request):
    profile, _ = UserProfile.objects.get_or_create(user=request.user)

    if request.method == 'POST':
        action = request.POST.get('action')
        if action == 'save_notification':
            profile.notify_via_line = request.POST.get('notify_via_line') == 'on'
            profile.notify_via_email = request.POST.get('notify_via_email') == 'on'
            profile.notification_email = request.POST.get('notification_email', '').strip()
            profile.save()
            messages.success(request, '通知設定を保存しました。')
        elif action == 'generate_line_code':
            # 6桁の一時コードを生成してセッションに保存
            code = ''.join(random.choices(string.digits, k=6))
            request.session['line_link_code'] = code
            request.session['line_link_user_id'] = request.user.id
        elif action == 'unlink_line':
            profile.line_user_id = ''
            profile.notify_via_line = False
            profile.save()
            messages.success(request, 'LINE連携を解除しました。')
        return redirect('shifts:user_settings')

    context = {'profile': profile}
    # セッションに連携コードがある場合、コードとLINE URLをコンテキストへ
    line_code = request.session.get('line_link_code')
    if line_code and not profile.line_user_id:
        from django.conf import settings as django_settings
        import urllib.parse
        import logging
        logger = logging.getLogger(__name__)
        basic_id = getattr(django_settings, 'LINE_BOT_BASIC_ID', '')
        context['line_link_code'] = line_code
        if basic_id:
            context['line_url'] = f"https://line.me/R/oaMessage/{basic_id}/?{urllib.parse.quote(line_code)}"
        else:
            logger.warning("LINE_BOT_BASIC_ID is not configured. LINE app link will not be shown.")
    return render(request, 'shifts/user_settings.html', context)


# ---------- LINE Webhook ----------

@csrf_exempt
@require_POST
def line_webhook(request):
    """LINE Messaging API の Webhook エンドポイント。"""
    from .line_bot import get_webhook_handler
    handler = get_webhook_handler()
    if handler is None:
        return HttpResponse(status=200)

    signature = request.headers.get('X-Line-Signature', '')
    body = request.body.decode('utf-8')

    try:
        from linebot.models import FollowEvent, MessageEvent, TextMessage
        from linebot.exceptions import InvalidSignatureError

        @handler.add(MessageEvent, message=TextMessage)
        def handle_text(event):
            text = event.message.text.strip()
            reply_token = event.reply_token
            line_uid = event.source.user_id

            from .line_bot import _get_line_api
            from linebot.models import TextSendMessage
            api = _get_line_api()
            if api is None:
                return

            # セッションを使わず全ユーザーのセッションを検索できないため、
            # 6桁数字のメッセージを受信したらコードと照合する
            if text.isdigit() and len(text) == 6:
                from django.contrib.sessions.models import Session
                from django.utils import timezone as tz
                matched = False
                for session in Session.objects.filter(expire_date__gt=tz.now()):
                    data = session.get_decoded()
                    if data.get('line_link_code') == text:
                        user_id = data.get('line_link_user_id')
                        if user_id:
                            try:
                                profile = UserProfile.objects.get(user_id=user_id)
                                profile.line_user_id = line_uid
                                profile.notify_via_line = True
                                profile.save()
                                api.reply_message(reply_token, TextSendMessage(text='LINE連携が完了しました！'))
                                matched = True
                            except UserProfile.DoesNotExist:
                                pass
                        break
                if not matched:
                    api.reply_message(reply_token, TextSendMessage(text='コードが一致しませんでした。アプリで新しいコードを発行してください。'))
            else:
                api.reply_message(reply_token, TextSendMessage(text='シフト管理アプリのLINE Botです。アプリ内で連携コードを発行してここに入力してください。'))

        handler.handle(body, signature)
    except Exception:
        pass

    return HttpResponse(status=200)


# ---------- CSVエクスポート ----------

@staff_member_required
def admin_export_submissions_csv(request, period_id):
    period = get_object_or_404(SchedulePeriod, pk=period_id)
    dates = list(generate_date_range(period.start_date, period.end_date))
    submissions = AvailabilitySubmission.objects.filter(
        period=period, status__in=['SUBMITTED', 'APPROVED']
    ).select_related('user').prefetch_related('days')

    response = HttpResponse(content_type='text/csv; charset=utf-8')
    filename = f"シフト提出_{period.start_date}_{period.end_date}.csv"
    response['Content-Disposition'] = (
        f"attachment; filename*=UTF-8''{urllib.parse.quote(filename)}"
    )
    response.write('\ufeff')  # BOM

    writer = csv.writer(response)

    # ヘッダー行
    header = ['名前']
    for d in dates:
        header.append(f"{d.strftime('%m/%d')}")
        header.append(f"コメント")
    header.append('備考')
    writer.writerow(header)

    # データ行
    for sub in submissions:
        days_map = {day.date: day for day in sub.days.all()}
        row = [sub.user.get_full_name() or sub.user.username]
        for d in dates:
            day = days_map.get(d)
            row.append(day.get_availability_display() if day else '')
            row.append(day.comment if day else '')
        row.append(sub.remarks)
        writer.writerow(row)

    return response
