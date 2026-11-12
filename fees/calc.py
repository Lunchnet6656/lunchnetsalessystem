"""販売フィー計算書の計算。日計表を読む部分（load_*）と、数字を組み立てる純粋な関数を分けてある。

総売上 ＝ Σ販売数×保存時点の単価（大盛り含む）＋ その他販売 ＋（反映ONなら）サービス販売の値引き
        クーポン・割引・返金は引かない。日計表の「総売上」はそのまま使わない。
税抜＝総売上÷1.08 切り捨て／消費税＝総売上−税抜／税抜手数料＝税抜×率 切り捨て／
手数料消費税＝税抜手数料×10% 切り捨て／支払額＝税抜手数料＋手数料消費税
"""
import calendar
import datetime
from dataclasses import dataclass, field
from decimal import ROUND_DOWN, Decimal

from sales.menu_registry import LARGE_RICE_NO

SALES_TAX = Decimal("1.08")   # 弁当は軽減税率
FEE_TAX = Decimal("0.10")     # 手数料は標準税率
WEEKDAYS = "月火水木金土日"


def _floor(value):
    return int(value.to_integral_value(rounding=ROUND_DOWN))


def fee_amounts(total_sales, rate):
    """総売上（税込）と手数料率（%）から計算書の6つの金額を出す。"""
    total = Decimal(int(total_sales))
    rate = Decimal(str(rate))
    excluded = _floor(total / SALES_TAX)
    fee = _floor(Decimal(excluded) * rate / 100)
    fee_tax = _floor(Decimal(fee) * FEE_TAX)
    return {
        "total_sales": int(total),
        "tax_excluded": excluded,
        "tax_amount": int(total) - excluded,
        "fee_excluded": fee,
        "fee_tax": fee_tax,
        "pay_amount": fee + fee_tax,
    }


# ===== 日計表1件ぶんの材料 =====

@dataclass
class EntryData:
    product_no: int
    unit_price: object  # int または None（移行前の古い記録）
    sales_quantity: int
    remaining: int


@dataclass
class ServiceLine:
    base_price: int    # 元の弁当の値段（定額割引は0）
    unit_amount: int   # 1個あたりの値引き（符号付き・マイナス）
    quantity: int
    flat: bool = False


@dataclass
class ReportData:
    date: datetime.date
    confirmed: bool = True
    entries: list = field(default_factory=list)
    others: list = field(default_factory=list)          # [(名前, 単価, 数)]
    service_lines: list = field(default_factory=list)
    pk: int = None

    @property
    def missing_price(self):
        return any(e.unit_price is None and e.sales_quantity > 0 for e in self.entries)


def month_days(month):
    first = month.replace(day=1)
    last = calendar.monthrange(first.year, first.month)[1]
    return [first.replace(day=d) for d in range(1, last + 1)]


def _desc_prices(values):
    return sorted({v for v in values}, reverse=True)


def compose(month, reports, holidays, include_service, service_name="サービス"):
    """日別明細と合計を組み立てる（PDF・画面・発行時のスナップショットで共通の形）。

    holidays: {日付: 祝日名}。土日祝は行を薄灰色・曜に「祝」。会社休日は祝日扱いにしない。
    """
    by_date = {r.date: r for r in reports}
    priced = [e for r in reports for e in r.entries if e.unit_price is not None and e.sales_quantity > 0]
    prices = _desc_prices(int(e.unit_price) for e in priced if e.product_no != LARGE_RICE_NO)
    large_prices = _desc_prices(int(e.unit_price) for e in priced if e.product_no == LARGE_RICE_NO)
    has_others = any(qty for r in reports for _, _, qty in r.others)
    # 日計表はサービス名を持たない（販売場所の今の名前）ので、月に1つの列にまとめる
    has_service = include_service and any(l.quantity for r in reports for l in r.service_lines)
    service_names = [service_name] if has_service else []

    days, others_sum, service_sum = [], {}, {}
    for d in month_days(month):
        holiday = holidays.get(d, "")
        row = {
            "day": d.day,
            "dow": WEEKDAYS[d.weekday()] + ("祝" if holiday else ""),
            "shade": d.weekday() >= 5 or bool(holiday),
            "bold": d.weekday() == 6 or bool(holiday),
            "has_report": d in by_date,
        }
        report = by_date.get(d)
        if report is not None:
            counts = [0] * len(prices)
            large = [0] * len(large_prices)
            sold = amount = remaining = 0
            for e in report.entries:
                if e.product_no == LARGE_RICE_NO:
                    if e.unit_price is not None and e.sales_quantity > 0:
                        large[large_prices.index(int(e.unit_price))] += e.sales_quantity
                        amount += int(e.unit_price) * e.sales_quantity
                    continue
                remaining += e.remaining
                if e.unit_price is not None and e.sales_quantity > 0:
                    counts[prices.index(int(e.unit_price))] += e.sales_quantity
                    sold += e.sales_quantity
                    amount += int(e.unit_price) * e.sales_quantity
            others = 0
            for name, price, qty in report.others:
                if qty:
                    others += price * qty
                    key = (name, price)
                    others_sum[key] = others_sum.get(key, 0) + qty
            services = []
            if has_service:
                svc = 0
                for line in report.service_lines:
                    if line.quantity:
                        svc += line.unit_amount * line.quantity
                        key = (line.flat, line.base_price, line.unit_amount)
                        service_sum[key] = service_sum.get(key, 0) + line.quantity
                services = [svc]
            amount += others + sum(services)
            row.update(counts=counts, large=large, others=others, services=services,
                       sold=sold, amount=amount, remaining=remaining)
        days.append(row)

    filled = [r for r in days if r["has_report"]]

    def col_sum(key, n):
        return [sum(r[key][i] for r in filled) for i in range(n)]

    totals = {
        "counts": col_sum("counts", len(prices)),
        "large": col_sum("large", len(large_prices)),
        "others": sum(r["others"] for r in filled),
        "services": col_sum("services", len(service_names)),
        "sold": sum(r["sold"] for r in filled),
        "amount": sum(r["amount"] for r in filled),
        "remaining": sum(r["remaining"] for r in filled),
    }
    others_breakdown = [
        {"name": name, "price": price, "qty": qty, "amount": price * qty}
        for (name, price), qty in sorted(others_sum.items(), key=lambda kv: (-kv[0][1], kv[0][0]))
    ]
    service_breakdown = [
        {"name": service_name, "flat": flat, "base": base, "price": base + unit, "unit": unit,
         "qty": qty, "amount": unit * qty}
        for (flat, base, unit), qty in sorted(service_sum.items(), key=lambda kv: (kv[0][0], -kv[0][1]))
    ]
    return {
        "month": f"{month:%Y-%m}",
        "prices": prices,
        "large_prices": large_prices,
        "has_others": has_others,
        "service_names": service_names,
        "days": days,
        "totals": totals,
        "others_breakdown": others_breakdown,
        "service_breakdown": service_breakdown,
    }


def workdays(month, holidays, company_holidays, until=None):
    """未提出の判定に使う平日（土日・祝日・会社休日を除く）。until より後の日は数えない。"""
    return [d for d in month_days(month)
            if d.weekday() < 5 and d not in holidays and d not in company_holidays
            and (until is None or d <= until)]


def checks(month, reports, holidays, company_holidays, today, off_days=()):
    """発行前チェックの材料。日付のリストで返す（画面で日付を並べるため）。

    off_days: シフトでその売り場を「休み」「中止」にした日。休むと決めた日は未提出にしない。
    """
    dates = set(r.date for r in reports) | set(off_days)
    no_reports = not reports
    return {
        "missing_price": sorted(r.date for r in reports if r.missing_price),
        "unconfirmed": sorted(r.date for r in reports if not r.confirmed),
        # 日計表が1件もない月は「日計表なし」だけ出す（全部の平日が未提出と並ぶと読みにくい）
        "unsubmitted": [] if no_reports else
        [d for d in workdays(month, holidays, company_holidays, until=today) if d not in dates],
        "no_reports": no_reports,
        "pks": {r.date: r.pk for r in reports},
    }


# ===== 日計表を読む =====

def service_label_of(location):
    """サービス販売の列名。日計表はサービス価格しか持たないので、販売場所のサービス名を使う。"""
    name = (getattr(location, "service_name", "") or "").strip()
    if not name or name == "なし":
        return "サービス"
    try:
        Decimal(name)
        return "サービス"  # 古いデータで価格が入っているもの
    except Exception:
        return name


def _service_lines(report):
    from sales.discounts import SERVICE_FLAT_LABEL, legacy_lines
    saved = list(report.discount_lines.all())
    if saved:
        lines = [(l.group, l.label, l.base_price, l.unit_amount, l.quantity) for l in saved]
    else:
        # 割引明細への移行前の記録は旧欄から組み立てる（日計表の編集画面と同じ）
        lines = [(l.group, l.label, l.base_price, l.unit_amount, l.quantity)
                 for l in legacy_lines(report, infer_from_total=int(report.total_discount or 0))]
    return [ServiceLine(0 if label == SERVICE_FLAT_LABEL else base, unit, qty, label == SERVICE_FLAT_LABEL)
            for group, label, base, unit, qty in lines if group == "service"]


def load_reports(location_name, month):
    """販売場所の今の名前で、その月の日計表を読む（No.は振り直し履歴があるので使わない）。"""
    from sales.models import DailyReport
    days = month_days(month)
    qs = (DailyReport.objects.filter(location=location_name, date__range=(days[0], days[-1]))
          .prefetch_related("entries", "discount_lines"))
    out = []
    for r in qs:
        entries = [EntryData(e.product_no, None if e.unit_price is None else int(e.unit_price),
                             int(e.sales_quantity or 0), int(e.remaining_number or 0)) for e in r.entries.all()]
        others = [(r.others_sales_1 or "その他", int(r.others_price1 or 0), int(r.others_sales_quantity1 or 0)),
                  (r.others_sales_2 or "その他", int(r.others_price2 or 0), int(r.others_sales_quantity2 or 0))]
        out.append(ReportData(r.date, bool(r.confirmed), entries,
                              [o for o in others if o[2]], _service_lines(r), r.pk))
    return out


def load_holidays(month):
    """祝日は jpholiday で決める（Holiday 表は2025年6月で止まっていて、10/12 が祝日にならなかった）。
    Holiday 表にある日（臨時の休み等）も祝日として足す。"""
    import jpholiday
    from sales.models import Holiday
    days = month_days(month)
    out = {d: jpholiday.is_holiday_name(d) for d in days if jpholiday.is_holiday(d)}
    for h in Holiday.objects.filter(date__range=(days[0], days[-1])):
        out.setdefault(h.date, h.description or "祝日")
    return out


def load_off_days(location, month):
    """シフトでその売り場を「休み」「中止」にした日。"""
    from shifts.models import ShiftAssignment
    days = month_days(month)
    return set(ShiftAssignment.objects.filter(sales_location=location, date__range=(days[0], days[-1]),
                                              special_type__in=("REST", "CANCEL"))
               .values_list("date", flat=True))


def load_company_holidays(month):
    from shifts.models import CompanyHoliday
    days = month_days(month)
    return set(CompanyHoliday.objects.filter(date__range=(days[0], days[-1])).values_list("date", flat=True))
