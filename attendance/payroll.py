"""給与計算エンジン（純粋関数）。

スプリント2の日次集計（aggregation.py）を金額に変える。
基本賃金＋法定割増（スプリント3）に、Phase 2 で手当・控除・差引支給額を加える。
金額計算はすべて Decimal。

要件定義: .company/engineering/harness/specs/w002-勤怠管理給与計算-要件定義.md
スプリント契約: w002-スプリント3-給与計算エンジン.md ／ w002-スプリント5-手当と控除.md
"""
from collections import namedtuple
from datetime import timedelta
from decimal import ROUND_CEILING, ROUND_HALF_UP, Decimal

from . import aggregation
from .models import (
    HourlyWage,
    ManualWorkHours,
    PayrollPeriod,
    PayslipAdjustment,
    StaffPayrollProfile,
    TimeRecord,
)

# 月60時間超で割増率が切り替わる境界（分）。
OVER60H_THRESHOLD_MINUTES = 60 * 60
# 週の法定労働時間（分）。これを超えた分が週の時間外。
WEEKLY_REGULAR_MINUTES = 40 * 60
_MINUTES_PER_HOUR = Decimal(60)

# 引数が渡されなかったことを表すセンチネル（None＝「プロフィール無し」と区別する）。
_UNSET = object()


def resolve_hourly_wage(staff, as_of_date, wages=None):
    """as_of_date 時点で有効な時給額（円）を返す。登録が無ければ 0。

    wages（そのスタッフの HourlyWage リスト）を渡すと追加クエリを発行しない。
    """
    if wages is None:
        wages = list(
            HourlyWage.objects.filter(staff=staff, effective_from__lte=as_of_date)
        )
    applicable = [w for w in wages if w.effective_from <= as_of_date]
    if not applicable:
        return 0
    return max(applicable, key=lambda w: w.effective_from).amount


def weekly_overtime_minutes(day_rows):
    """週40時間超の追加時間外（分）を返す。日次8時間超との二重計上は避ける。

    法定週は日曜起算（暫定）。期間の端の半端な週は期間内の日だけで集計する。
    """
    # 週の日曜日 -> [実働合計, 日次時間外合計]
    weeks = {}
    for row in day_rows:
        calc = row["calc"]
        if calc is None:
            continue
        d = row["date"]
        # Python の weekday() は月=0..日=6。日曜起算へ (weekday()+1)%7 日戻す。
        sunday = d - timedelta(days=(d.weekday() + 1) % 7)
        bucket = weeks.setdefault(sunday, [0, 0])
        bucket[0] += calc.work_minutes
        bucket[1] += calc.overtime_minutes
    total_extra = 0
    for work, daily_ot in weeks.values():
        # 週40h超のうち、日次8h超で既に時間外な分を引いた残りが追加分。
        extra = work - WEEKLY_REGULAR_MINUTES - daily_ot
        if extra > 0:
            total_extra += extra
    return total_extra


# --- 所得税：国税庁「月額表 甲欄 電算機計算の特例」（令和8年分以降） -----------
# 出典: 国税庁 zeigakuhyo2026/data/denshi_01.pdf。全員が扶養控除等申告書を提出済み
# （甲欄）と仮定する暫定。年度が変わったらこの定数群を更新する。

# 第2表：扶養親族等1人あたりの控除（源泉控除対象配偶者・親族とも同額）。
DEPENDENT_DEDUCTION = 31667


def _salary_income_deduction(a):
    """第1表：給与所得控除の額。a＝社保控除後の給与（円）。1円未満切り上げ。"""
    a = Decimal(a)
    if a <= 158333:
        d = Decimal(54167)
    elif a <= 299999:
        d = a * Decimal("0.30") + 6667
    elif a <= 549999:
        d = a * Decimal("0.20") + 36667
    elif a <= 708330:
        d = a * Decimal("0.10") + 91667
    else:
        d = Decimal(162500)
    return int(d.quantize(Decimal(1), rounding=ROUND_CEILING))


def _basic_deduction(a):
    """第3表：基礎控除の額。a＝社保控除後の給与（円）。"""
    if a <= 2120833:
        return 48334
    if a <= 2162499:
        return 40000
    if a <= 2204166:
        return 26667
    if a <= 2245833:
        return 13334
    return 0


def _tax_from_taxable_income(b):
    """第4表：課税給与所得金額B（円）から税額。10円未満四捨五入。"""
    if b <= 0:
        return 0
    b = Decimal(b)
    if b <= 162500:
        t = b * Decimal("0.05105")
    elif b <= 275000:
        t = b * Decimal("0.10210") - 8296
    elif b <= 579166:
        t = b * Decimal("0.20420") - 36374
    elif b <= 750000:
        t = b * Decimal("0.23483") - 54113
    elif b <= 1500000:
        t = b * Decimal("0.33693") - 130688
    elif b <= 3333333:
        t = b * Decimal("0.40840") - 237893
    else:
        t = b * Decimal("0.45945") - 408061
    if t < 0:
        t = Decimal(0)
    # 10円未満四捨五入。
    return int((t / 10).quantize(Decimal(1), rounding=ROUND_HALF_UP) * 10)


def income_tax(taxable_after_si, dependents):
    """その月の所得税（源泉徴収・甲欄・電算機計算特例）。

    taxable_after_si ＝ A ＝ 課税支給額 − 社会保険料等（健康・介護・厚年・雇用保険）。
    dependents ＝ 扶養親族等の数。
    """
    a = max(0, taxable_after_si)
    taxable_income = (
        a
        - _salary_income_deduction(a)
        - DEPENDENT_DEDUCTION * dependents
        - _basic_deduction(a)
    )
    return _tax_from_taxable_income(taxable_income)


PayslipCalc = namedtuple(
    "PayslipCalc",
    [
        "staff",
        # 勤怠
        "work_days",
        "work_minutes",   # 総実働＝通常＋時間外＋深夜＋休日
        "normal_minutes", # 通常時間（基本賃金の対象。割増分は含まない）
        "break_minutes",  # 控除した休憩（分・表示用）
        "overtime_minutes",
        "over60h_minutes",
        "night_minutes",
        "holiday_minutes",
        # 支給（賃金）
        "hourly_wage",
        "base_wage_yen",        # 実働分の基本賃金（拘束−休憩 × 時給）。総支給の計算に使う実値
        "break_deduction_yen",  # 休憩控除（拘束分の基本賃金 − 実働分の基本賃金）。支給欄の表示用
        "overtime_premium_yen",
        "night_premium_yen",
        "holiday_premium_yen",
        "total_yen",  # 賃金計（基本＋割増）
        # 支給（手当）
        "commute_allowance_yen",
        "other_allowance_yen",
        # 販売事業向けの手当（食堂は通常0）
        "peddling_allowance_yen",
        "box_wash_allowance_yen",
        "paid_leave_yen",
        "driver_allowance_yen",       # ドライバー手当（is_driver=Trueだけ加算）
        "gasoline_yen",               # 通勤ガソリン代（車/バイク通勤者のみ・非課税）
        # 販売手当の内訳（給与明細表示用）
        "peddling_count",
        "box_wash_count",
        "paid_leave_days",
        "driver_count",
        # 臨時項目の合計（行ごとの内訳は別途PayslipAdjustmentから取得）
        "adjustment_payments_yen",
        "adjustment_deductions_yen",
        "gross_yen",  # 総支給額（賃金＋手当＋臨時支給）
        # 控除
        "health_insurance_yen",
        "nursing_insurance_yen",
        "pension_yen",
        "employment_insurance_yen",
        "income_tax_yen",
        "resident_tax_yen",
        "other_deduction_yen",
        "total_deduction_yen",  # 控除合計（既存控除＋臨時控除）
        # 差引
        "net_pay_yen",
        "below_min_wage",
    ],
)


def _yen(minutes, wage, rate=Decimal(1)):
    """分 × 時給 × 倍率 を円（Decimal）で返す。"""
    return Decimal(minutes) / _MINUTES_PER_HOUR * Decimal(wage) * rate


def _round_yen(amount, unit=1):
    """金額（Decimal）を unit 円単位に四捨五入し int で返す。"""
    unit = Decimal(unit)
    return int((amount / unit).quantize(Decimal(1), rounding=ROUND_HALF_UP) * unit)


def premium_unit_yen(wage, full_rate):
    """割増の1時間あたり単価（円）。時給 × フル割増率を1円単位に四捨五入する。

    労基法の端数処理通達（昭63.3.14 基発150号）に従い、円未満は50銭未満切捨て・
    50銭以上切上げ＝四捨五入。例：1225×1.25=1531.25→1531／1226×1.25=1532.5→1533。
    """
    return _round_yen(Decimal(wage) * full_rate)


def _premium_yen(minutes, wage, full_rate):
    """割増額＝「四捨五入した時給単価 × 時間」。最終額も1円単位に四捨五入する。"""
    unit = premium_unit_yen(wage, full_rate)
    return _round_yen(Decimal(unit) * Decimal(minutes) / _MINUTES_PER_HOUR)


def employment_insurance(gross_yen, setting, enrolled):
    """雇用保険料（労働者負担）＝総支給額 × 料率。未加入なら0。"""
    if not enrolled:
        return 0
    return _round_yen(Decimal(gross_yen) * setting.employment_insurance_rate)


def compute_payslip(
    staff,
    period_start,
    period_end,
    setting,
    records=None,
    wages=None,
    profile=_UNSET,
    manual=_UNSET,
    adjustments=_UNSET,
):
    """1スタッフ・1期間の給与計算結果 PayslipCalc を返す。

    時給は period_end 時点で有効なもの1本を使う。
    手動入力（ManualWorkHours）があるスタッフは打刻集計を使わずその値で計算する
    （販売事業の v1：紙タイムカードからの月次入力）。
    """
    # 手動入力（販売事業向け）があれば打刻からの集計より優先する。
    if manual is _UNSET:
        manual = ManualWorkHours.objects.filter(
            staff=staff, payroll_period__period_end=period_end
        ).first()

    if manual is not None:
        # 手動入力（販売）：work_minutes＝「通常勤務」の拘束時間。休憩を引いた分が通常実働。
        # 時間外・深夜・休日はそれぞれ別入力で、通常実働には含めず上乗せする（v2方式）。
        work_days = manual.work_days
        break_min = manual.break_minutes
        normal = max(0, manual.work_minutes - break_min)
        overtime = manual.overtime_minutes
        night = manual.night_minutes
        holiday_min = manual.holiday_minutes
    else:
        rows = aggregation.build_day_rows(
            staff, period_start, period_end, setting, records=records
        )
        work = night = daily_ot = work_days = break_min = 0
        for row in rows:
            calc = row["calc"]
            if calc is None:
                continue
            work_days += 1
            work += calc.work_minutes
            night += calc.night_minutes
            daily_ot += calc.overtime_minutes
            # 打刻側は compute_day で実働から休憩を控除済み。表示用に合算するだけ。
            break_min += calc.break_minutes
        # 時間外＝日次8h超＋週40h超。月60時間で割増率を分ける。
        overtime = daily_ot + weekly_overtime_minutes(rows)
        holiday_min = 0
        # 打刻側の通常時間＝実働(total)から時間外・深夜・休日を除いた残り（v2方式）。
        # ⚠ 深夜と時間外が重なると二重控除されうる（食堂は深夜≒0のため実用上は無視）。
        normal = max(0, work - overtime - night - holiday_min)

    over60h = max(0, overtime - OVER60H_THRESHOLD_MINUTES)
    ot_under60 = overtime - over60h

    wage = resolve_hourly_wage(staff, period_end, wages=wages)

    # 総実働＝通常＋各割増バケットの単純合計（出力・表示用）。
    work = normal + overtime + night + holiday_min

    # v2方式：基本賃金は通常時間のみを1.0倍。割増は各バケットを「フル単価」で独立計算する。
    # フル単価 ＝ 1 ＋ 既存の上乗せ率（時間外1.25／60h超1.50／深夜1.25／休日1.35）。
    base_yen = _round_yen(_yen(normal, wage))
    # 休憩控除（支給欄の表示用）。通常時間＋休憩分の基本賃金から通常分を引いた額。
    # 「基本賃金（拘束）− 休憩控除 ＝ base_yen」で常に整合し、総支給額には影響しない。
    break_deduction_yen = _round_yen(_yen(normal + break_min, wage)) - base_yen
    # 割増は「四捨五入した時給単価 × 時間」（基発150号）。単価を1円単位に四捨五入してから掛ける。
    overtime_yen = (
        _premium_yen(ot_under60, wage, Decimal(1) + setting.overtime_rate)
        + _premium_yen(over60h, wage, Decimal(1) + setting.over60h_rate)
    )
    night_yen = _premium_yen(night, wage, Decimal(1) + setting.night_rate)
    # 休日時間が入力されていればフル単価で加算（打刻ベースは holiday_min=0）。
    holiday_yen = _premium_yen(holiday_min, wage, Decimal(1) + setting.holiday_rate)
    total_yen = _round_yen(
        Decimal(base_yen + overtime_yen + night_yen + holiday_yen),
        setting.wage_rounding_unit,
    )

    # --- Phase 2：手当 ---
    if profile is _UNSET:
        profile = StaffPayrollProfile.objects.filter(staff=staff).first()
    commute = profile.commute_allowance if profile else 0
    other_allowance = profile.other_allowance if profile else 0

    # 販売事業の手当（行商・箱洗い・有給・ドライバー）。manual=None＝食堂は全て0。
    # 単価は会社一律（PayrollSetting）。有給は所定時間×時給で換算する。
    peddling_count = manual.peddling_count if manual is not None else 0
    box_wash_count = manual.box_wash_count if manual is not None else 0
    paid_leave_days = manual.paid_leave_days if manual is not None else 0
    driver_count = manual.driver_count if manual is not None else 0
    peddling_yen = peddling_count * setting.peddling_allowance_yen
    box_wash_yen = box_wash_count * setting.box_wash_allowance_yen
    scheduled_minutes = profile.scheduled_minutes_per_day if profile else 480
    paid_leave_yen = _round_yen(
        _yen(paid_leave_days * scheduled_minutes, wage),
        setting.wage_rounding_unit,
    )
    # ドライバー手当：is_driver=True のスタッフだけ加算（False の人は count があっても0）
    is_driver = bool(profile and profile.is_driver)
    driver_yen = (
        driver_count * setting.driver_allowance_yen if is_driver else 0
    )

    # 通勤ガソリン代（非課税扱い）。
    # 自動車/バイク通勤かつ PayrollPeriod に当月のガソリン単価が入っていれば計算。
    # ガソリン代 = (片道距離 × 2 × 出勤日数 × 単価) / 燃費
    gasoline_yen = 0
    if profile and profile.commute_method in ("car", "motorcycle"):
        period_obj = PayrollPeriod.objects.filter(period_end=period_end).first()
        gas_per_l = period_obj.gasoline_yen_per_liter if period_obj else 0
        # 出勤日数は work_days を流用（食堂は打刻ベース、販売は手動入力）
        if gas_per_l and profile.fuel_efficiency_kml and profile.commute_distance_km:
            distance_round = Decimal(str(profile.commute_distance_km)) * 2 * work_days
            raw = (distance_round * Decimal(gas_per_l)) / Decimal(str(profile.fuel_efficiency_kml))
            gasoline_yen = _round_yen(raw, setting.wage_rounding_unit)

    # 課税対象の総支給額。
    #   通常賃金扱い（課税）：販売手当4種は含む
    #   非課税：通勤手当（commute）、ガソリン代（通勤実費）→ taxable 計算で除外する
    gross_yen = (
        total_yen + commute + other_allowance
        + peddling_yen + box_wash_yen + paid_leave_yen + driver_yen
        + gasoline_yen
    )

    # --- Phase 2：控除 ---
    health = profile.health_insurance if profile else 0
    nursing = profile.nursing_insurance if profile else 0
    pension = profile.pension_insurance if profile else 0
    resident_tax = profile.resident_tax if profile else 0
    other_deduction = profile.other_deduction if profile else 0
    dependents = profile.dependents_count if profile else 0
    ei_enrolled = profile.employment_insurance_enrolled if profile else False

    employment_ins = employment_insurance(gross_yen, setting, ei_enrolled)
    # 課税支給額＝総支給額−非課税分（通勤手当＋通勤ガソリン代）。
    taxable = gross_yen - commute - gasoline_yen
    # A＝課税支給額−社会保険料等（健康・介護・厚年・雇用保険）。
    income_tax_yen = income_tax(
        taxable - health - nursing - pension - employment_ins, dependents
    )

    total_deduction = (
        health + nursing + pension + employment_ins
        + income_tax_yen + resident_tax + other_deduction
    )

    # 臨時項目（PayslipAdjustment）— 雇用保険・所得税の計算後に加算するので、
    # これらの計算基礎には含まれない（年末調整還付金・慶弔金などのため）。
    if adjustments is _UNSET:
        adjustments = list(
            PayslipAdjustment.objects.filter(
                staff=staff, payroll_period__period_end=period_end
            )
        )
    payment_adj = sum(
        a.amount_yen for a in adjustments
        if a.kind == PayslipAdjustment.KIND_PAYMENT
    )
    deduction_adj = sum(
        a.amount_yen for a in adjustments
        if a.kind == PayslipAdjustment.KIND_DEDUCTION
    )
    gross_yen = gross_yen + payment_adj
    total_deduction = total_deduction + deduction_adj
    net_pay = gross_yen - total_deduction

    return PayslipCalc(
        staff=staff,
        work_days=work_days,
        work_minutes=work,
        normal_minutes=normal,
        break_minutes=break_min,
        overtime_minutes=overtime,
        over60h_minutes=over60h,
        night_minutes=night,
        holiday_minutes=holiday_min,
        hourly_wage=wage,
        base_wage_yen=base_yen,
        break_deduction_yen=break_deduction_yen,
        overtime_premium_yen=overtime_yen,
        night_premium_yen=night_yen,
        holiday_premium_yen=holiday_yen,
        total_yen=total_yen,
        commute_allowance_yen=commute,
        other_allowance_yen=other_allowance,
        peddling_allowance_yen=peddling_yen,
        box_wash_allowance_yen=box_wash_yen,
        paid_leave_yen=paid_leave_yen,
        driver_allowance_yen=driver_yen,
        gasoline_yen=gasoline_yen,
        peddling_count=peddling_count,
        box_wash_count=box_wash_count,
        paid_leave_days=paid_leave_days,
        driver_count=driver_count,
        adjustment_payments_yen=payment_adj,
        adjustment_deductions_yen=deduction_adj,
        gross_yen=gross_yen,
        health_insurance_yen=health,
        nursing_insurance_yen=nursing,
        pension_yen=pension,
        employment_insurance_yen=employment_ins,
        income_tax_yen=income_tax_yen,
        resident_tax_yen=resident_tax,
        other_deduction_yen=other_deduction,
        total_deduction_yen=total_deduction,
        net_pay_yen=net_pay,
        # 時給未登録（0）は最低賃金違反とは別物なので警告対象にしない。
        below_min_wage=0 < wage < setting.min_wage,
    )


def compute_payslips(staff_list, period_start, period_end, setting):
    """複数スタッフの計算結果を {staff_id: PayslipCalc} で返す。

    打刻・時給・給与プロフィールの取得を各1クエリにまとめ、N+1を避ける。
    """
    staff_ids = [s.id for s in staff_list]
    records = TimeRecord.objects.filter(
        staff_id__in=staff_ids,
        work_date__range=(period_start, period_end),
        is_deleted=False,
    )
    records_by_staff = {sid: [] for sid in staff_ids}
    for rec in records:
        records_by_staff[rec.staff_id].append(rec)

    wages = HourlyWage.objects.filter(
        staff_id__in=staff_ids, effective_from__lte=period_end
    )
    wages_by_staff = {sid: [] for sid in staff_ids}
    for wage in wages:
        wages_by_staff[wage.staff_id].append(wage)

    profiles_by_staff = {
        p.staff_id: p
        for p in StaffPayrollProfile.objects.filter(staff_id__in=staff_ids)
    }

    manuals_by_staff = {
        m.staff_id: m
        for m in ManualWorkHours.objects.filter(
            staff_id__in=staff_ids, payroll_period__period_end=period_end
        )
    }

    adjustments_by_staff = {sid: [] for sid in staff_ids}
    for adj in PayslipAdjustment.objects.filter(
        staff_id__in=staff_ids, payroll_period__period_end=period_end
    ):
        adjustments_by_staff.setdefault(adj.staff_id, []).append(adj)

    result = {}
    for staff in staff_list:
        result[staff.id] = compute_payslip(
            staff,
            period_start,
            period_end,
            setting,
            records=records_by_staff[staff.id],
            wages=wages_by_staff[staff.id],
            profile=profiles_by_staff.get(staff.id),
            manual=manuals_by_staff.get(staff.id),
            adjustments=adjustments_by_staff.get(staff.id, []),
        )
    return result
