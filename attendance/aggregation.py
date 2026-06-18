"""勤怠集計のロジック（純粋関数）。

打刻（TimeRecord）から日次の実働・休憩・深夜・時間外を1分単位で計算し、
締め期間（○月度）の解決と月次合計を行う。DBへの書き込みはしない。

要件定義: .company/engineering/harness/specs/w002-勤怠管理給与計算-要件定義.md
スプリント契約: .company/engineering/harness/specs/w002-スプリント2-勤怠集計と管理.md
"""
import calendar
from collections import namedtuple
from datetime import date, datetime, timedelta

from django.utils import timezone

from .models import TimeRecord

# 1日の法定労働時間。これを超えた実働を時間外として区分する。
# 週40時間超の判定はスプリント3（給与計算）で扱う（契約書 決定3）。
DAILY_REGULAR_MINUTES = 8 * 60

_WEEKDAYS_JA = ["月", "火", "水", "木", "金", "土", "日"]


# --- 締め期間（○月度）の解決 -------------------------------------------------

def _closing_date(year, month, closing_day):
    """その年月の締め日を返す。締め日が月末を超える設定なら月末に丸める。"""
    last_day = calendar.monthrange(year, month)[1]
    return date(year, month, min(closing_day, last_day))


def _prev_month(year, month):
    return (year - 1, 12) if month == 1 else (year, month - 1)


def _next_month(year, month):
    return (year + 1, 1) if month == 12 else (year, month + 1)


def resolve_period_end(reference_date, closing_day):
    """指定日が属する締め期間の終了日（締め日）を返す。"""
    this_close = _closing_date(reference_date.year, reference_date.month, closing_day)
    if reference_date <= this_close:
        return this_close
    ny, nm = _next_month(reference_date.year, reference_date.month)
    return _closing_date(ny, nm, closing_day)


def period_start_for(period_end, closing_day):
    """締め日（period_end）に対する期間開始日＝前月の締め日の翌日を返す。"""
    py, pm = _prev_month(period_end.year, period_end.month)
    return _closing_date(py, pm, closing_day) + timedelta(days=1)


def recent_period_ends(closing_day, count, reference_date):
    """指定日を含む期間から過去 count 個分の締め日を新しい順で返す（期間セレクタ用）。"""
    ends = []
    end = resolve_period_end(reference_date, closing_day)
    for _ in range(count):
        ends.append(end)
        end = period_start_for(end, closing_day) - timedelta(days=1)
    return ends


def period_label(period_end):
    """締め期間の表示名。締め日の属する月で「○年○月度」と呼ぶ。"""
    return f"{period_end.year}年{period_end.month}月度"


# --- 表示用フォーマッタ ------------------------------------------------------

def fmt_minutes(minutes):
    """分を "H:MM" 形式にする。0/None は "—"。"""
    if not minutes:
        return "—"
    return f"{minutes // 60}:{minutes % 60:02d}"


def fmt_time(dt):
    """打刻 datetime を "HH:MM" のローカル時刻文字列にする。None は空文字。"""
    if dt is None:
        return ""
    return timezone.localtime(dt).strftime("%H:%M")


# --- 日次の集計 --------------------------------------------------------------

DayCalc = namedtuple(
    "DayCalc",
    ["span_minutes", "break_minutes", "work_minutes", "night_minutes", "overtime_minutes"],
)


def _to_local_naive_minute(dt):
    """aware datetime を localtime に直し、秒を切り捨てた naive datetime にする。

    1分単位の計算のため秒は捨てる。日本はサマータイムが無く tz 変換は単純。
    """
    local = timezone.localtime(dt)
    return local.replace(second=0, microsecond=0, tzinfo=None)


def night_overlap_minutes(start, end, night_start, night_end):
    """[start, end) と毎日の深夜帯の重なりを分で返す（naive ローカル datetime 前提）。

    night_start >= night_end のとき深夜帯は日付をまたぐ（例 22:00〜翌5:00）。
    日ごとの深夜帯ウィンドウは互いに重ならないため、単純に合算してよい。
    """
    if end <= start:
        return 0
    crosses_midnight = night_start >= night_end
    total = 0
    # start の前日から end の当日まで、日ごとの深夜帯と突き合わせる。
    day = start.date() - timedelta(days=1)
    while day <= end.date():
        window_start = datetime.combine(day, night_start)
        if crosses_midnight:
            window_end = datetime.combine(day + timedelta(days=1), night_end)
        else:
            window_end = datetime.combine(day, night_end)
        lo = max(start, window_start)
        hi = min(end, window_end)
        if hi > lo:
            total += int((hi - lo).total_seconds() // 60)
        day += timedelta(days=1)
    return total


def compute_day(clock_in_dt, clock_out_dt, setting):
    """出勤・退勤の datetime（aware）から日次の集計 DayCalc を返す。"""
    start = _to_local_naive_minute(clock_in_dt)
    end = _to_local_naive_minute(clock_out_dt)
    span = max(0, int((end - start).total_seconds() // 60))
    break_minutes = setting.deduct_break_minutes(span) if span > 0 else 0
    work = max(0, span - break_minutes)
    # 深夜帯の重なりは拘束時間で測り、実働で頭打ちにする（契約書 決定5）。
    night = min(
        night_overlap_minutes(start, end, setting.night_start, setting.night_end),
        work,
    )
    overtime = max(0, work - DAILY_REGULAR_MINUTES)
    return DayCalc(span, break_minutes, work, night, overtime)


# --- 日次明細の行 ------------------------------------------------------------

def _iter_dates(start, end):
    day = start
    while day <= end:
        yield day
        day += timedelta(days=1)


def _build_row(day, rec_in, rec_out, setting):
    """1日分の集計行（テンプレート用 dict）を組み立てる。"""
    has_in = rec_in is not None
    has_out = rec_out is not None
    calc = None
    if has_in and has_out:
        calc = compute_day(rec_in.recorded_at, rec_out.recorded_at, setting)
    touched = [r for r in (rec_in, rec_out) if r is not None]
    return {
        "date": day,
        "iso": day.isoformat(),
        "weekday": _WEEKDAYS_JA[day.weekday()],
        "is_weekend": day.weekday() >= 5,
        "clock_in_time": fmt_time(rec_in.recorded_at) if has_in else "",
        "clock_out_time": fmt_time(rec_out.recorded_at) if has_out else "",
        "has_in": has_in,
        "has_out": has_out,
        "complete": has_in and has_out,
        # 出勤・退勤のちょうど片方だけ＝打刻漏れ。
        "missing": has_in != has_out,
        "calc": calc,
        "work_display": fmt_minutes(calc.work_minutes) if calc else "—",
        "break_display": fmt_minutes(calc.break_minutes) if calc else "—",
        "night_display": fmt_minutes(calc.night_minutes) if calc else "—",
        "overtime_display": fmt_minutes(calc.overtime_minutes) if calc else "—",
        "manual": any(r.source == TimeRecord.SOURCE_MANUAL for r in touched),
        "corrected": any(r.is_corrected for r in touched),
    }


def build_day_rows(staff, period_start, period_end, setting, records=None):
    """スタッフの期間内の全日付について、1日1行の集計行リストを作る。

    records を渡すと追加クエリを発行しない（一覧画面のN+1回避に使う）。
    """
    if records is None:
        records = TimeRecord.objects.filter(
            staff=staff,
            work_date__range=(period_start, period_end),
            is_deleted=False,
        )
    indexed = {(r.work_date, r.kind): r for r in records}
    rows = []
    for day in _iter_dates(period_start, period_end):
        rec_in = indexed.get((day, TimeRecord.KIND_CLOCK_IN))
        rec_out = indexed.get((day, TimeRecord.KIND_CLOCK_OUT))
        rows.append(_build_row(day, rec_in, rec_out, setting))
    return rows


# --- 月次合計 ----------------------------------------------------------------

PeriodSummary = namedtuple(
    "PeriodSummary",
    ["work_days", "work_minutes", "night_minutes", "overtime_minutes", "missing_days"],
)


def summarize_rows(rows):
    """日次行のリストから月次合計を出す。"""
    work_days = work = night = overtime = missing = 0
    for row in rows:
        if row["calc"]:
            work_days += 1
            work += row["calc"].work_minutes
            night += row["calc"].night_minutes
            overtime += row["calc"].overtime_minutes
        if row["missing"]:
            missing += 1
    return PeriodSummary(work_days, work, night, overtime, missing)


def build_period_summaries(staff_list, period_start, period_end, setting):
    """複数スタッフの月次合計を {staff_id: PeriodSummary} で返す（打刻取得は1クエリ）。"""
    staff_ids = [s.id for s in staff_list]
    records = TimeRecord.objects.filter(
        staff_id__in=staff_ids,
        work_date__range=(period_start, period_end),
        is_deleted=False,
    )
    by_staff = {sid: [] for sid in staff_ids}
    for rec in records:
        by_staff[rec.staff_id].append(rec)
    summaries = {}
    for staff in staff_list:
        rows = build_day_rows(
            staff, period_start, period_end, setting, records=by_staff[staff.id]
        )
        summaries[staff.id] = summarize_rows(rows)
    return summaries
