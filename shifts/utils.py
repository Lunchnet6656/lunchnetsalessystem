import datetime

import jpholiday
from django.utils import timezone

from .models import CompanyHoliday


def is_holiday(date):
    """土日 or 祝日 or 会社休日"""
    if date.weekday() >= 5:
        return True
    if jpholiday.is_holiday(date):
        return True
    if CompanyHoliday.objects.filter(date=date).exists():
        return True
    return False


def get_holiday_name(date):
    """休日名称を取得"""
    weekday = date.weekday()
    if weekday == 5:
        return '土曜日'
    if weekday == 6:
        return '日曜日'
    holiday_name = jpholiday.is_holiday_name(date)
    if holiday_name:
        return holiday_name
    company = CompanyHoliday.objects.filter(date=date).first()
    if company:
        return company.name
    return ''


def generate_date_range(start, end):
    """日付リストジェネレータ"""
    current = start
    while current <= end:
        yield current
        current += datetime.timedelta(days=1)


def auto_close_expired_periods():
    """締切を過ぎたOPEN期間をREVIEWに自動変更"""
    from .models import SchedulePeriod
    SchedulePeriod.objects.filter(
        status='OPEN',
        submission_close_at__lt=timezone.now(),
    ).update(status='REVIEW')


def assignment_identity_key(obj):
    """割当（ShiftAssignment / PublishedShiftSnapshot）の中身を比較用キーに変換。
    担当者・外部スタッフ・特殊種別の違いを一意に表す。空セルは ''。"""
    if obj is None:
        return ''
    if getattr(obj, 'special_type', ''):
        return 's:' + obj.special_type
    if obj.user_id:
        return 'u:' + str(obj.user_id)
    if obj.external_staff_id:
        return 'e:' + str(obj.external_staff_id)
    return ''


def snapshot_published_assignments(period):
    """現在の割当を公開ベースライン（スナップショット）として保存し直す。
    公開時・修正確定時に呼ぶ。既存スナップショットは作り直す。"""
    from .models import ShiftAssignment, PublishedShiftSnapshot
    PublishedShiftSnapshot.objects.filter(period=period).delete()
    assignments = ShiftAssignment.objects.filter(
        date__gte=period.start_date, date__lte=period.end_date,
    )
    objs = [
        PublishedShiftSnapshot(
            period=period,
            date=a.date,
            sales_location_id=a.sales_location_id,
            user_id=a.user_id,
            external_staff_id=a.external_staff_id,
            special_type=a.special_type,
        )
        for a in assignments
    ]
    PublishedShiftSnapshot.objects.bulk_create(objs)


def get_pending_change_count(period):
    """ライブ割当と公開ベースラインの差分セル数を返す。"""
    from .models import ShiftAssignment, PublishedShiftSnapshot
    live = {
        (a.date, a.sales_location_id): assignment_identity_key(a)
        for a in ShiftAssignment.objects.filter(
            date__gte=period.start_date, date__lte=period.end_date,
        )
    }
    base = {
        (s.date, s.sales_location_id): assignment_identity_key(s)
        for s in PublishedShiftSnapshot.objects.filter(period=period)
    }
    count = 0
    for key in set(live) | set(base):
        if live.get(key, '') != base.get(key, ''):
            count += 1
    return count


def can_submit_for_period(period, user, is_admin=False):
    """提出可否判定"""
    now = timezone.now()
    if is_admin:
        return period.status in ('OPEN', 'REVIEW')
    if period.status != 'OPEN':
        return False
    if now < period.submission_open_at:
        return False
    if now > period.submission_close_at:
        return False
    return True
