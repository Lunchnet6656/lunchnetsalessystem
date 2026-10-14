"""直行直帰の出勤日と勤務時間の計算。

出勤日は「日計表の担当者名が本人の "姓 名"」と「出勤追加」から集める。
1日の勤務時間 ＝ 定時（終業 − 始業）＋ 早出 ＋ 残業 − 遅刻 − 早退。
定時はその日の売り場の就業時間（開始日がその日以前で一番新しいもの）。未設定の日は勤務時間に入れない。

仕様: .company/engineering/harness/specs/lunchnetsale-直行直帰-勤務時間計算-要件定義.md §5
"""
import datetime
from bisect import bisect_right
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation

from django.contrib.auth import get_user_model

from sales.models import (
    DailyReport,
    DirectReturnDayAdjustment,
    DirectReturnExtraAttendance,
    DirectReturnWorkTime,
    SalesLocation,
)

CLOSING_DAY = 15
WEEKDAY_JA = ['月', '火', '水', '木', '金', '土', '日']
ZERO = Decimal('0')
QUARTER = Decimal('0.25')
MAX_ADJUST_HOURS = Decimal('24')
ADJUST_FIELDS = ['early_hours', 'late_hours', 'leave_early_hours', 'overtime_hours']


# ===== 締め期間 =====

def period_of(year, month):
    """◯月分の期間（前月16日〜当月15日）を返す。"""
    end = datetime.date(year, month, CLOSING_DAY)
    prev = end.replace(day=1) - datetime.timedelta(days=1)
    return datetime.date(prev.year, prev.month, CLOSING_DAY + 1), end


def period_containing(d):
    """日付 d が入る締め期間の (年, 月)。"""
    if d.day <= CLOSING_DAY:
        return d.year, d.month
    nxt = (d.replace(day=1) + datetime.timedelta(days=32)).replace(day=1)
    return nxt.year, nxt.month


def shift_month(year, month, delta):
    index = year * 12 + (month - 1) + delta
    return index // 12, index % 12 + 1


def period_label(year, month):
    start, end = period_of(year, month)
    return f"{month}月分（{start.month}/{start.day}〜{end.month}/{end.day}）"


def period_choices(today):
    """期間セレクトの選択肢：来月分＋直近12か月分（新しい順）。"""
    year, month = period_containing(today)
    choices = []
    for delta in range(1, -12, -1):
        y, m = shift_month(year, month, delta)
        choices.append({'value': f"{y}-{m:02d}", 'label': period_label(y, m), 'year': y, 'month': m})
    return choices


def parse_period(value, today):
    """'2026-10' → (2026, 10)。読めなければ今日が入る期間。"""
    try:
        y, m = (int(x) for x in str(value).split('-'))
        if 1 <= m <= 12 and 2000 <= y <= 2100:
            return y, m
    except (TypeError, ValueError):
        pass
    return period_containing(today)


# ===== 表示用 =====

def format_date_ja(d):
    return f"{d.month}/{d.day}({WEEKDAY_JA[d.weekday()]})"


def format_hours(value):
    """Decimal の時間を小数2桁で（4→4.00、4.5→4.50）。"""
    if value is None:
        return ''
    return f"{value:.2f}"


def person_name(user):
    """日計表の担当者名と突き合わせる名前（"姓 名"。既存の数え方と同じ）。"""
    return f"{user.last_name} {user.first_name}"


def hours_between(start, end):
    minutes = (end.hour * 60 + end.minute) - (start.hour * 60 + start.minute)
    return (Decimal(minutes) / Decimal(60)).quantize(Decimal('0.01'))


# ===== 入力チェック =====

def parse_adjust_hours(raw):
    """早出などの入力を Decimal に。空欄は0。おかしければ (None, エラー文)。"""
    text = (raw or '').strip()
    if text == '':
        return ZERO, None
    try:
        value = Decimal(text)
    except InvalidOperation:
        return None, '数字で入れてください。'
    if not value.is_finite():
        return None, '数字で入れてください。'
    if value < 0:
        return None, '0以上で入れてください。'
    if value > MAX_ADJUST_HOURS:
        return None, '24h以内で入れてください。'
    if value % QUARTER != 0:
        return None, '0.25h単位で入れてください。'
    return value, None


# ===== 就業時間 =====

class WorkTimeBook:
    """売り場ごとの就業時間を開始日順に持ち、日付から引く。"""

    def __init__(self, location_ids=None):
        qs = DirectReturnWorkTime.objects.all()
        if location_ids is not None:
            qs = qs.filter(location_id__in=location_ids)
        self._rows = {}
        for wt in qs.order_by('location_id', 'effective_from'):
            self._rows.setdefault(wt.location_id, []).append(wt)
        self._dates = {loc: [wt.effective_from for wt in rows] for loc, rows in self._rows.items()}

    def on(self, location_id, d):
        rows = self._rows.get(location_id)
        if not rows:
            return None
        i = bisect_right(self._dates[location_id], d)
        return rows[i - 1] if i else None


def locations_by_name():
    """店名 → 売り場。日計表の location は店名文字列なので、No.ではなく店名で引く。"""
    result = {}
    for loc in SalesLocation.objects.order_by('no', 'id'):
        result.setdefault(loc.name.strip(), loc)
    return result


def work_time_in_effect(location, d):
    return (
        DirectReturnWorkTime.objects
        .filter(location=location, effective_from__lte=d)
        .order_by('-effective_from')
        .first()
    )


# ===== 出勤日の組み立て =====

@dataclass
class Day:
    date: datetime.date
    location_name: str
    location: object = None          # SalesLocation（日計表の店名が見つからなければ None）
    source: str = 'report'           # 'report'（日計表）／'extra'（出勤追加）
    extra: object = None
    work_time: object = None
    base: Decimal = None             # 定時。未設定なら None
    early: Decimal = ZERO
    late: Decimal = ZERO
    leave_early: Decimal = ZERO
    overtime: Decimal = ZERO
    note: str = ''

    @property
    def is_extra(self):
        return self.source == 'extra'

    @property
    def unset(self):
        return self.base is None

    @property
    def hours(self):
        if self.base is None:
            return None
        return self.base + self.early + self.overtime - self.late - self.leave_early

    @property
    def date_str(self):
        return format_date_ja(self.date)

    @property
    def key(self):
        return self.date.isoformat()

    def adjust_value(self, name):
        return {
            'early_hours': self.early, 'late_hours': self.late,
            'leave_early_hours': self.leave_early, 'overtime_hours': self.overtime,
        }[name]


@dataclass
class Summary:
    user: object
    name: str
    days: list = field(default_factory=list)

    def _sum(self, attr):
        return sum((getattr(d, attr) for d in self.days if not d.unset), ZERO)

    @property
    def day_count(self):
        return len(self.days)

    @property
    def base_total(self):
        return self._sum('base')

    @property
    def early_total(self):
        return self._sum('early')

    @property
    def late_total(self):
        return self._sum('late')

    @property
    def leave_early_total(self):
        return self._sum('leave_early')

    @property
    def overtime_total(self):
        return self._sum('overtime')

    @property
    def hours_total(self):
        return sum((d.hours for d in self.days if not d.unset), ZERO)

    @property
    def extra_count(self):
        return sum(1 for d in self.days if d.is_extra)

    @property
    def unset_days(self):
        return [d for d in self.days if d.unset]


def build_summaries(users, start, end):
    """スタッフごとに期間内の出勤日を組み立てる。返り値は users と同じ順の Summary のリスト。"""
    users = list(users)
    names = {u.pk: person_name(u) for u in users}
    by_name = {}
    for u in users:
        by_name.setdefault(names[u.pk], u)

    reports = (
        DailyReport.objects
        .filter(date__gte=start, date__lte=end, person_in_charge__in=list(by_name))
        .order_by('date', 'id')
        .values('date', 'location', 'person_in_charge')
    )
    extras = (
        DirectReturnExtraAttendance.objects
        .filter(user__in=users, date__gte=start, date__lte=end)
        .select_related('location')
    )
    adjustments = {
        (a.user_id, a.date): a
        for a in DirectReturnDayAdjustment.objects.filter(user__in=users, date__gte=start, date__lte=end)
    }
    loc_by_name = locations_by_name()

    days = {u.pk: {} for u in users}
    # 同じ日に日計表が2件以上あっても1日。売り場は最初に送られたもの
    for r in reports:
        user = by_name[r['person_in_charge']]
        if r['date'] in days[user.pk]:
            continue
        name = (r['location'] or '').strip()
        days[user.pk][r['date']] = Day(date=r['date'], location_name=name, location=loc_by_name.get(name))
    for ex in extras:
        if ex.date in days[ex.user_id]:
            continue  # 登録時に弾いているが、後から日計表が届いた日は日計表を優先
        days[ex.user_id][ex.date] = Day(
            date=ex.date, location_name=ex.location.name, location=ex.location, source='extra', extra=ex,
        )

    location_ids = {d.location.pk for per in days.values() for d in per.values() if d.location}
    book = WorkTimeBook(location_ids)
    summaries = []
    for u in users:
        rows = []
        for d in sorted(days[u.pk]):
            day = days[u.pk][d]
            if day.location:
                day.work_time = book.on(day.location.pk, d)
                if day.work_time:
                    day.base = hours_between(day.work_time.start_time, day.work_time.end_time)
            adj = adjustments.get((u.pk, d))
            if adj:
                day.early, day.late = adj.early_hours, adj.late_hours
                day.leave_early, day.overtime = adj.leave_early_hours, adj.overtime_hours
                day.note = adj.note
            rows.append(day)
        summaries.append(Summary(user=u, name=names[u.pk], days=rows))
    return summaries


def direct_return_users():
    User = get_user_model()
    return (
        User.objects
        .filter(menu_permission__direct_return=True)
        .exclude(last_name='', first_name='')
        .order_by('last_name', 'first_name', 'pk')
    )


def report_location_on(user, d):
    """その人のその日の日計表の売り場名（無ければ None）。出勤追加の二重計上チェック用。"""
    report = (
        DailyReport.objects
        .filter(date=d, person_in_charge=person_name(user))
        .order_by('id')
        .first()
    )
    return report.location if report else None
