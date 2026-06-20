"""勤怠の書き込み系ビジネスロジック（サービス層）。

ビュー（attendance/views.py）はこの層を呼ぶだけにとどめる。
スプリント1＝スタッフのスマホ打刻。スプリント2＝管理者の打刻修正・販売の
まとめ入力・月締め。集計（読み取り専用）は aggregation.py 側。
"""
from datetime import datetime, timedelta

from django.db import IntegrityError, transaction
from django.utils import timezone

from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

from . import aggregation, payroll
from .models import (
    HourlyWage,
    ManualWorkHours,
    Payslip,
    PayslipAdjustment,
    PayrollPeriod,
    PayrollSetting,
    Staff,
    StaffPayrollProfile,
    TimeRecord,
    TimeRecordEdit,
)

# 当日の打刻状況。テンプレートの表示出し分けに使う。
STATE_NOT_CLOCKED_IN = "not_clocked_in"
STATE_WORKING = "working"
STATE_DONE = "done"


class PunchError(Exception):
    """打刻が業務ルール上できないときに送出する（二重打刻・出勤前の退勤など）。"""


def get_active_staff(user):
    """ログインユーザーに紐づく在籍スタッフを返す。無ければ None。"""
    return (
        Staff.objects.filter(user=user, is_active=True)
        .select_related("store")
        .first()
    )


def _today_records(staff):
    """本日（JST）の有効な打刻を、打刻時刻の古い順で返す。"""
    return list(
        TimeRecord.objects.filter(
            staff=staff,
            work_date=timezone.localdate(),
            is_deleted=False,
        ).order_by("recorded_at")
    )


def get_punch_state(staff):
    """本日の打刻状況をまとめた dict を返す。ビューはこれをテンプレートへ渡す。"""
    records = _today_records(staff)
    clock_in = next((r for r in records if r.kind == TimeRecord.KIND_CLOCK_IN), None)
    clock_out = next((r for r in records if r.kind == TimeRecord.KIND_CLOCK_OUT), None)

    if clock_in is None:
        state = STATE_NOT_CLOCKED_IN
    elif clock_out is None:
        state = STATE_WORKING
    else:
        state = STATE_DONE

    return {
        "state": state,
        "clock_in_at": clock_in.recorded_at if clock_in else None,
        "clock_out_at": clock_out.recorded_at if clock_out else None,
        "can_undo": bool(records),
    }


@transaction.atomic
def clock_in(staff):
    """出勤を打刻する。本日すでに出勤済みなら PunchError。"""
    if any(r.kind == TimeRecord.KIND_CLOCK_IN for r in _today_records(staff)):
        raise PunchError("本日はすでに出勤を打刻しています。")
    try:
        return TimeRecord.objects.create(
            staff=staff,
            kind=TimeRecord.KIND_CLOCK_IN,
            recorded_at=timezone.now(),
            work_date=timezone.localdate(),
            source=TimeRecord.SOURCE_APP,
        )
    except IntegrityError:
        # 二度押しが競合した場合の DB制約による弾き。利用者には同じ案内を返す。
        raise PunchError("本日はすでに出勤を打刻しています。")


@transaction.atomic
def clock_out(staff):
    """退勤を打刻する。出勤打刻が無い／本日すでに退勤済みなら PunchError。"""
    records = _today_records(staff)
    clock_in_record = next(
        (r for r in records if r.kind == TimeRecord.KIND_CLOCK_IN), None
    )
    if clock_in_record is None:
        raise PunchError("先に出勤を打刻してください。")
    if any(r.kind == TimeRecord.KIND_CLOCK_OUT for r in records):
        raise PunchError("本日はすでに退勤を打刻しています。")
    try:
        return TimeRecord.objects.create(
            staff=staff,
            kind=TimeRecord.KIND_CLOCK_OUT,
            recorded_at=timezone.now(),
            # 退勤は出勤と同じ勤務日に揃える（日付をまたぐ勤務に備える）。
            work_date=clock_in_record.work_date,
            source=TimeRecord.SOURCE_APP,
        )
    except IntegrityError:
        raise PunchError("本日はすでに退勤を打刻しています。")


@transaction.atomic
def undo_last_punch(staff):
    """本日の直近の打刻を取り消す（論理削除）。取り消せる打刻が無ければ PunchError。"""
    records = _today_records(staff)
    if not records:
        raise PunchError("取り消せる打刻がありません。")
    last = records[-1]
    last.is_deleted = True
    last.save(update_fields=["is_deleted", "updated_at"])
    return last


# --- スプリント2：締め期間 ---------------------------------------------------

def get_or_create_period(period_end):
    """締め日に対応する PayrollPeriod を返す（無ければ作成）。"""
    setting = PayrollSetting.current()
    period, _ = PayrollPeriod.objects.get_or_create(
        period_end=period_end,
        defaults={
            "period_start": aggregation.period_start_for(
                period_end, setting.closing_day
            ),
            "closing_day": setting.closing_day,
        },
    )
    return period


def period_is_locked(work_date):
    """その勤務日が締め済み期間に含まれるか。"""
    return PayrollPeriod.objects.filter(
        period_start__lte=work_date,
        period_end__gte=work_date,
        is_closed=True,
    ).exists()


@transaction.atomic
def close_period(period_end, user):
    """期間を締める。以降その期間の打刻・修正・まとめ入力はロックされる。"""
    period = get_or_create_period(period_end)
    period.is_closed = True
    period.closed_at = timezone.now()
    period.closed_by = user
    period.save(
        update_fields=["is_closed", "closed_at", "closed_by", "updated_at"]
    )
    return period


@transaction.atomic
def reopen_period(period_end):
    """締めを解除し、その期間を再び編集できるようにする。"""
    period = get_or_create_period(period_end)
    period.is_closed = False
    period.closed_at = None
    period.closed_by = None
    period.save(
        update_fields=["is_closed", "closed_at", "closed_by", "updated_at"]
    )
    return period


# --- スプリント2：打刻の修正・販売のまとめ入力 -------------------------------

def _aware(work_date, t):
    """勤務日＋時刻から、現在のタイムゾーンの aware datetime を作る。"""
    return timezone.make_aware(datetime.combine(work_date, t))


def _apply_punch(staff, work_date, kind, target_dt, editor, reason):
    """1種別（出勤 or 退勤）の打刻を目標時刻へ合わせ、修正履歴を残す。

    target_dt が None なら既存打刻を取り消す。差分が無ければ何もしない。
    """
    existing = (
        TimeRecord.objects.filter(
            staff=staff, work_date=work_date, kind=kind, is_deleted=False
        ).first()
    )

    if target_dt is None:
        if existing is None:
            return
        before = aggregation.fmt_time(existing.recorded_at)
        existing.is_deleted = True
        existing.save(update_fields=["is_deleted", "updated_at"])
        TimeRecordEdit.objects.create(
            time_record=existing,
            editor=editor,
            action=TimeRecordEdit.ACTION_DELETED,
            before_value=before,
            after_value="—",
            reason=reason,
        )
        return

    if existing is None:
        record = TimeRecord.objects.create(
            staff=staff,
            kind=kind,
            recorded_at=target_dt,
            work_date=work_date,
            source=TimeRecord.SOURCE_MANUAL,
        )
        TimeRecordEdit.objects.create(
            time_record=record,
            editor=editor,
            action=TimeRecordEdit.ACTION_CREATED,
            before_value="—",
            after_value=aggregation.fmt_time(target_dt),
            reason=reason,
        )
        return

    # 既存あり：分単位で見て差分が無ければ修正履歴も残さない。
    before = aggregation.fmt_time(existing.recorded_at)
    after = aggregation.fmt_time(target_dt)
    if before == after:
        return
    existing.recorded_at = target_dt
    existing.is_corrected = True
    existing.save(update_fields=["recorded_at", "is_corrected", "updated_at"])
    TimeRecordEdit.objects.create(
        time_record=existing,
        editor=editor,
        action=TimeRecordEdit.ACTION_UPDATED,
        before_value=before,
        after_value=after,
        reason=reason,
    )


@transaction.atomic
def set_day_punches(staff, work_date, clock_in_time, clock_out_time, editor, reason=""):
    """1日分の出退勤を設定する。打刻修正と販売のまとめ入力で共通に使う。

    clock_in_time / clock_out_time は datetime.time または None（その種別を消す）。
    退勤が出勤以下の時刻なら、日付をまたぐ勤務として退勤を翌日扱いにする。
    締め済み期間なら PunchError。
    """
    if period_is_locked(work_date):
        raise PunchError("この期間は締め済みのため編集できません。")

    clock_in_dt = _aware(work_date, clock_in_time) if clock_in_time else None

    clock_out_dt = None
    if clock_out_time:
        out_date = work_date
        # 退勤 ≤ 出勤 は日付をまたぐ勤務（出勤の翌日に退勤）とみなす。
        if clock_in_time and clock_out_time <= clock_in_time:
            out_date = work_date + timedelta(days=1)
        clock_out_dt = _aware(out_date, clock_out_time)

    _apply_punch(
        staff, work_date, TimeRecord.KIND_CLOCK_IN, clock_in_dt, editor, reason
    )
    _apply_punch(
        staff, work_date, TimeRecord.KIND_CLOCK_OUT, clock_out_dt, editor, reason
    )


# --- スプリント3：給与計算の確定・取消 ---------------------------------------

@transaction.atomic
def confirm_payroll(period_end, user):
    """期間の給与計算を確定し、Payslip を凍結保存する。

    締め済みでなければ PunchError。既存の確定分は作り直す（取消→確定の単純化）。
    在籍スタッフ全員分の Payslip を作り、確定後の表示・PDF発行の基準にする。
    """
    period = get_or_create_period(period_end)
    # 同時確定の競合（2端末で同時に「確定」）を防ぐため、期間行を行ロックしてから
    # delete→create する。ロックを取らないと一意制約 uniq_payslip_per_period_staff
    # 違反で後発トランザクションが 500 になる。
    period = PayrollPeriod.objects.select_for_update().get(pk=period.pk)
    if not period.is_closed:
        raise PunchError("先に月締めをしてから給与計算を確定してください。")

    setting = PayrollSetting.current()
    staff_list = list(Staff.objects.filter(is_active=True).select_related("store"))
    calcs = payroll.compute_payslips(
        staff_list, period.period_start, period.period_end, setting
    )

    Payslip.objects.filter(payroll_period=period).delete()
    now = timezone.now()
    payslips = [
        Payslip(
            payroll_period=period,
            staff=staff,
            work_days=c.work_days,
            work_minutes=c.work_minutes,
            normal_minutes=c.normal_minutes,
            break_minutes=c.break_minutes,
            overtime_minutes=c.overtime_minutes,
            over60h_minutes=c.over60h_minutes,
            night_minutes=c.night_minutes,
            holiday_minutes=c.holiday_minutes,
            hourly_wage=c.hourly_wage,
            base_wage_yen=c.base_wage_yen,
            break_deduction_yen=c.break_deduction_yen,
            overtime_premium_yen=c.overtime_premium_yen,
            night_premium_yen=c.night_premium_yen,
            holiday_premium_yen=c.holiday_premium_yen,
            total_yen=c.total_yen,
            commute_allowance_yen=c.commute_allowance_yen,
            other_allowance_yen=c.other_allowance_yen,
            gross_yen=c.gross_yen,
            health_insurance_yen=c.health_insurance_yen,
            nursing_insurance_yen=c.nursing_insurance_yen,
            pension_yen=c.pension_yen,
            employment_insurance_yen=c.employment_insurance_yen,
            income_tax_yen=c.income_tax_yen,
            resident_tax_yen=c.resident_tax_yen,
            other_deduction_yen=c.other_deduction_yen,
            total_deduction_yen=c.total_deduction_yen,
            net_pay_yen=c.net_pay_yen,
            confirmed_at=now,
            confirmed_by=user,
        )
        for staff in staff_list
        for c in (calcs[staff.id],)
    ]
    Payslip.objects.bulk_create(payslips)
    return payslips


@transaction.atomic
def cancel_payroll(period_end):
    """確定済みの給与計算を取り消す（Payslip を削除）。再計算できる状態へ戻す。"""
    # 確定処理と同じ期間行をロックして、確定／取消の同時実行による競合を防ぐ。
    period = (
        PayrollPeriod.objects.select_for_update()
        .filter(period_end=period_end)
        .first()
    )
    if period is None:
        return 0
    deleted, _ = Payslip.objects.filter(payroll_period=period).delete()
    return deleted


# --- 最低賃金の一括更新 -----------------------------------------------------

def staff_on_min_wage(reference_date=None):
    """指定日時点で「現在の最低賃金」で働いている在籍スタッフを返す。

    8割が最低賃金というランチネットの運用前提で、最低賃金が上がったときに
    対象を絞り込む基準として使う。最低賃金より高い時給の人は対象外。
    """
    setting = PayrollSetting.current()
    if reference_date is None:
        reference_date = timezone.localdate()
    on_min = []
    for staff in Staff.objects.filter(is_active=True).select_related("store"):
        wage = payroll.resolve_hourly_wage(staff, reference_date)
        if wage == setting.min_wage:
            on_min.append(staff)
    return on_min


@transaction.atomic
def bulk_update_min_wage(new_amount, effective_from, user=None):
    """現在の最低賃金で働いているスタッフだけを新しい最低賃金へ一括更新する。

    PayrollSetting.min_wage も同時に更新する。スタッフごとの HourlyWage は
    新しい1件を追加（履歴は残す）。同じ effective_from に既存があれば更新で上書き。
    最低賃金より高い時給で働いているスタッフ・時給未登録のスタッフは触らない。
    """
    setting = PayrollSetting.current()
    affected = staff_on_min_wage()
    created = 0
    for staff in affected:
        _, was_created = HourlyWage.objects.update_or_create(
            staff=staff,
            effective_from=effective_from,
            defaults={"amount": new_amount},
        )
        if was_created:
            created += 1
    setting.min_wage = new_amount
    setting.save(update_fields=["min_wage", "updated_at"])
    return affected, created


# --- 有給休暇の年度集計（10月〜9月） ---------------------------------------

def paid_leave_year_range(ref_date):
    """ref_date が含まれる有給年度（10月〜翌年9月）の (start, end) を返す。

    例）2026-05-27 → (2025-10-01, 2026-09-30)
        2026-11-15 → (2026-10-01, 2027-09-30)
    """
    from datetime import date as _date
    if ref_date.month >= 10:
        return _date(ref_date.year, 10, 1), _date(ref_date.year + 1, 9, 30)
    return _date(ref_date.year - 1, 10, 1), _date(ref_date.year, 9, 30)


def paid_leave_summary(staff, ref_date=None):
    """スタッフの今期有給休暇サマリを dict で返す。

    付与日数（granted）と、有給年度内の累計取得日数（used）、残日数（remaining）。
    取得日数は ManualWorkHours.paid_leave_days を年度内合計する。
    """
    from django.db.models import Sum
    from .models import ManualWorkHours
    if ref_date is None:
        ref_date = timezone.localdate()
    start, end = paid_leave_year_range(ref_date)
    used = (
        ManualWorkHours.objects
        .filter(
            staff=staff,
            payroll_period__period_end__range=(start, end),
        )
        .aggregate(total=Sum("paid_leave_days"))
        ["total"] or 0
    )
    granted = staff.paid_leave_granted_days
    return {
        "year_label": f"{start.year}/10〜{end.year}/9",
        "year_start": start,
        "year_end": end,
        "granted": granted,
        "used": used,
        "remaining": max(0, granted - used),
    }


# --- スタッフ新規追加（User + Staff + 初期時給 を1つの操作で） ----------------

@transaction.atomic
def attach_staff_to_user(
    *,
    user,
    business_unit,
    company,
    store,
    hired_on=None,
    initial_hourly_wage=None,
    birthday=None,
    gender="",
    address="",
    phone="",
    job_description="",
    display_name=None,
):
    """既存の auth.User に Staff（＋初期時給）を後付けする。

    販売システムで既にアカウントを持つ人を、新規アカウントを作り直さずに
    勤怠スタッフへ取り込む導線で使う。display_name 未指定なら User の姓名から合成。
    """
    from .models import HourlyWage as _HW, Staff as _Staff
    if _Staff.objects.filter(user=user).exists():
        raise PunchError(f"「{user.username}」には既に勤怠スタッフが登録されています。")
    if not display_name:
        display_name = f"{user.last_name} {user.first_name}".strip() or user.username
    staff = _Staff.objects.create(
        user=user,
        display_name=display_name,
        business_unit=business_unit,
        company=company,
        store=store,
        hired_on=hired_on,
        birthday=birthday,
        gender=gender,
        address=address,
        phone=phone,
        job_description=job_description,
        is_active=True,
    )
    if initial_hourly_wage:
        from datetime import date as _date
        _HW.objects.create(
            staff=staff,
            amount=initial_hourly_wage,
            effective_from=hired_on or _date.today(),
        )
    return staff


@transaction.atomic
def create_staff_with_user(
    *,
    username,
    password,
    last_name,
    first_name,
    business_unit,
    company,
    store,
    hired_on=None,
    initial_hourly_wage=None,
    birthday=None,
    gender="",
    address="",
    phone="",
    job_description="",
):
    """auth.User を新規作成し、Staff・初期時給まで一括で作る。

    どこかで失敗したら全部ロールバック（User 作成だけ残るような事態を避ける）。
    既存ユーザーへの後付けは attach_staff_to_user を使う。
    苗字・名前は auth.User の標準フィールドに保存（正規の置き場）。
    """
    from django.contrib.auth.models import User as _User
    if _User.objects.filter(username=username).exists():
        raise PunchError(f"ユーザー名「{username}」は既に使われています。")
    user = _User.objects.create_user(
        username=username, password=password,
        last_name=last_name, first_name=first_name,
    )
    return attach_staff_to_user(
        user=user,
        business_unit=business_unit,
        company=company,
        store=store,
        hired_on=hired_on,
        initial_hourly_wage=initial_hourly_wage,
        birthday=birthday,
        gender=gender,
        address=address,
        phone=phone,
        job_description=job_description,
        display_name=f"{last_name} {first_name}".strip(),
    )


# --- 勤務時間の手動入力（販売事業の v1 用） ----------------------------------

def _hours_to_minutes(value):
    """フォームの「時間（小数可）」文字列を分（int）に。空・不正は0。

    丸めは ROUND_HALF_UP で統一する（プロジェクト全体の端数方針＝_round_yen 等と
    同じ）。Python 組込み round() は偶数丸めで、.5分が労働者に不利な切り捨て側へ
    落ちることがあるため使わない。
    """
    value = (value or "").strip()
    if not value:
        return 0
    try:
        return int((Decimal(value) * 60).quantize(Decimal(1), rounding=ROUND_HALF_UP))
    except (InvalidOperation, ValueError):
        return 0


def _int_or_zero(value):
    value = (value or "").strip()
    return int(value) if value.lstrip("-").isdigit() and int(value) >= 0 else 0


@transaction.atomic
def save_manual_hours(
    staff, period, work_days, work_minutes,
    overtime_minutes, night_minutes, holiday_minutes, note="",
    peddling_count=0, box_wash_count=0, paid_leave_days=0,
    driver_count=0, break_minutes=0,
):
    """1スタッフ分の手動勤務時間を保存（または既存がなく全0なら何もしない）。

    給与明細画面からの勤怠インライン編集で使う。既存レコードがあるとき全0でも更新
    する＝管理者の明示的な意思表示として扱う（手取り0円を保存したい場合がある）。

    販売事業部の手当（行商回数・箱洗い数・有給日数・ドライバー回数）も同じレコード
    に入る。食堂でこれらが空のまま渡されても問題ない（既定0）。
    """
    existing = ManualWorkHours.objects.filter(
        payroll_period=period, staff=staff
    ).first()
    all_zero = not (
        work_days or work_minutes or overtime_minutes
        or night_minutes or holiday_minutes or note
        or peddling_count or box_wash_count or paid_leave_days
        or driver_count or break_minutes
    )
    if existing is None and all_zero:
        return None
    obj, _ = ManualWorkHours.objects.update_or_create(
        staff=staff,
        payroll_period=period,
        defaults={
            "work_days": work_days,
            "work_minutes": work_minutes,
            "break_minutes": break_minutes,
            "overtime_minutes": overtime_minutes,
            "night_minutes": night_minutes,
            "holiday_minutes": holiday_minutes,
            "peddling_count": peddling_count,
            "box_wash_count": box_wash_count,
            "paid_leave_days": paid_leave_days,
            "driver_count": driver_count,
            "note": note,
        },
    )
    return obj


@transaction.atomic
def clear_manual_hours(staff, period):
    """スタッフ・期間の手動勤務時間を削除（打刻ベースの集計に戻す）。"""
    deleted, _ = ManualWorkHours.objects.filter(
        staff=staff, payroll_period=period
    ).delete()
    return deleted


# --- スタッフ設定の一括保存（時給＋手当＋控除を1画面で） ---------------------

def _upsert_hourly_wage(staff, amount, effective_from):
    """時給を更新する。直近の有効時給と同額ならスキップ（重複作成しない）。"""
    if amount <= 0:
        return False
    latest = (
        HourlyWage.objects
        .filter(staff=staff, effective_from__lte=effective_from)
        .order_by("-effective_from")
        .first()
    )
    if latest and latest.amount == amount:
        return False
    HourlyWage.objects.update_or_create(
        staff=staff,
        effective_from=effective_from,
        defaults={"amount": amount},
    )
    return True


@transaction.atomic
def save_staff_settings_bulk(staff_list, post_data, effective_from=None):
    """全スタッフ設定（時給・会社・手当・控除）を1フォームから一括保存する。

    50名以上を想定した一括設定で、PC操作の少ない運用者でも1画面で完結できる導線。
    時給は履歴を残しつつ effective_from の新レコードを作る（同額なら何もしない）。
    手当・控除は StaffPayrollProfile を upsert。
    会社（company）の変更は Staff レコード自体を更新する。
    """
    from .models import COMPANY_CHOICES  # 循環インポートを避けるためここで取り込む

    valid_companies = {c for c, _ in COMPANY_CHOICES}
    if effective_from is None:
        effective_from = timezone.localdate()
    wage_changed = 0
    profile_changed = 0
    for staff in staff_list:
        prefix = f"staff_{staff.id}_"
        wage = _int_or_zero(post_data.get(prefix + "hourly_wage"))
        if _upsert_hourly_wage(staff, wage, effective_from):
            wage_changed += 1

        # 会社（LSN/LN/LF）の変更を反映。空や不正値は無視（既存値を維持）。
        company = (post_data.get(prefix + "company") or "").strip()
        if company in valid_companies and company != staff.company:
            staff.company = company
            staff.save(update_fields=["company", "updated_at"])

        commute = _int_or_zero(post_data.get(prefix + "commute_allowance"))
        health = _int_or_zero(post_data.get(prefix + "health_insurance"))
        pension = _int_or_zero(post_data.get(prefix + "pension_insurance"))
        resident = _int_or_zero(post_data.get(prefix + "resident_tax"))
        dependents = _int_or_zero(post_data.get(prefix + "dependents_count"))
        ei_enrolled = post_data.get(prefix + "employment_insurance_enrolled") == "on"

        # 既存プロフィールがあれば差分のあるフィールドだけ更新、無ければ新規作成。
        existing = StaffPayrollProfile.objects.filter(staff=staff).first()
        if existing is None:
            StaffPayrollProfile.objects.create(
                staff=staff,
                commute_allowance=commute,
                health_insurance=health,
                pension_insurance=pension,
                resident_tax=resident,
                dependents_count=dependents,
                employment_insurance_enrolled=ei_enrolled,
            )
            profile_changed += 1
            continue
        if (
            existing.commute_allowance != commute
            or existing.health_insurance != health
            or existing.pension_insurance != pension
            or existing.resident_tax != resident
            or existing.dependents_count != dependents
            or existing.employment_insurance_enrolled != ei_enrolled
        ):
            existing.commute_allowance = commute
            existing.health_insurance = health
            existing.pension_insurance = pension
            existing.resident_tax = resident
            existing.dependents_count = dependents
            existing.employment_insurance_enrolled = ei_enrolled
            existing.save()
            profile_changed += 1
    return wage_changed, profile_changed


# --- 臨時項目（年末調整還付・慶弔金・遡及精算など） ---------------------------

def _adjustment_prefixes(post_data):
    """POSTのキー名から adj_<i>_xxx の各行の prefix を抽出する。"""
    prefixes = set()
    for key in post_data:
        if key.startswith("adj_") and key.endswith("_name"):
            prefixes.add(key[: -len("_name")])
    return sorted(prefixes)


@transaction.atomic
def save_payslip_adjustments(staff, period, post_data):
    """臨時項目の行を upsert／delete する。

    締め済み期間はロック（confirm/取消の対象外を保つため）。
    POSTのキー命名規約：
        adj_<i>_id       既存ID（無ければ新規）
        adj_<i>_name     項目名（空ならスキップ）
        adj_<i>_kind     "payment" or "deduction"
        adj_<i>_amount   金額（円・正の整数）
        adj_<i>_note     備考
        adj_<i>_delete   "on" なら既存を削除
    """
    if period.is_closed:
        raise PunchError("締め済み期間のため臨時項目を編集できません。")
    created = updated = deleted = 0
    for prefix in _adjustment_prefixes(post_data):
        adj_id = (post_data.get(prefix + "_id") or "").strip()
        name = (post_data.get(prefix + "_name") or "").strip()
        kind = (post_data.get(prefix + "_kind") or "").strip()
        amount_raw = (post_data.get(prefix + "_amount") or "").strip()
        note = (post_data.get(prefix + "_note") or "").strip()
        delete = post_data.get(prefix + "_delete") == "on"

        existing = None
        if adj_id.isdigit():
            existing = PayslipAdjustment.objects.filter(
                id=int(adj_id), staff=staff, payroll_period=period
            ).first()

        if existing is not None and delete:
            existing.delete()
            deleted += 1
            continue
        # 空入力（名称・区分・金額のいずれかが欠ける）はスキップ（誤って空行を作らない）
        if not name or kind not in dict(PayslipAdjustment.KIND_CHOICES) or not amount_raw.isdigit():
            continue
        amount = int(amount_raw)
        if existing is None:
            PayslipAdjustment.objects.create(
                staff=staff, payroll_period=period,
                name=name, kind=kind, amount_yen=amount, note=note,
            )
            created += 1
        else:
            changed = (
                existing.name != name
                or existing.kind != kind
                or existing.amount_yen != amount
                or existing.note != note
            )
            if changed:
                existing.name = name
                existing.kind = kind
                existing.amount_yen = amount
                existing.note = note
                existing.save()
                updated += 1
    return created, updated, deleted
