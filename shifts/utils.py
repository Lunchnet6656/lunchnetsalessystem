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
