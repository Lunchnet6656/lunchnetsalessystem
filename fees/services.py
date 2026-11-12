"""一括発行の行づくり・発行・取消。"""
import datetime
from dataclasses import dataclass, field

from django.db import IntegrityError, transaction
from django.utils import timezone

from sales.models import IssuerSettings

from . import calc
from .models import FeeDestination, FeeStatement, month_start


class FeeIssueError(Exception):
    pass


def staff_name(user):
    """PDFの「担当」。見本どおり姓と名を全角スペースで区切る。"""
    name = "　".join(p for p in (user.last_name, user.first_name) if p)
    return name or user.username


def issuer_snapshot():
    s = IssuerSettings.load()
    postal = s.postal_code.strip()
    if postal and not postal.startswith("〒"):
        postal = f"〒{postal}"
    return {
        "company_name": s.company_name,
        "postal_code": postal,
        "address": s.address,
        "tel": s.tel,
        "fax": s.fax,
        # 「載せる」がOFFの間は計算書に出さない（請求書には出る）
        "registration_number": s.registration_number if s.show_registration_on_fee else "",
        "seal": s.seal_image_data,
    }


@dataclass
class MonthContext:
    """1か月ぶんの共通の材料（売り場ごとに読み直さない）。"""
    month: datetime.date
    today: datetime.date
    holidays: dict
    company_holidays: set

    @classmethod
    def load(cls, month, today=None):
        month = month_start(month)
        return cls(month, today or timezone.localdate(), calc.load_holidays(month),
                   calc.load_company_holidays(month))


def build_detail(location, month_ctx, include_service):
    reports = calc.load_reports(location.name, month_ctx.month)
    detail = calc.compose(month_ctx.month, reports, month_ctx.holidays, include_service,
                          calc.service_label_of(location))
    return detail, reports


@dataclass
class IssueRow:
    destination: FeeDestination
    rate: object
    detail: dict
    amounts: dict
    check: dict
    statement: FeeStatement = None
    blocks: list = field(default_factory=list)   # 発行できない理由
    warns: list = field(default_factory=list)    # [(キー, 表示, [日付...])]
    diff_total: int = None                       # 発行済みで、今の日計表と総売上が違うときの今の総売上

    @property
    def location(self):
        return self.destination.location

    @property
    def selectable(self):
        return not self.blocks and self.statement is None

    @property
    def default_checked(self):
        keys = {w[0] for w in self.warns}
        return self.selectable and "unconfirmed" not in keys and "no_reports" not in keys

    @property
    def has_attention(self):
        return bool(self.blocks or self.warns or self.diff_total is not None)


def build_row(destination, month_ctx, statement=None):
    location = destination.location
    rate = destination.rate_on(month_ctx.month)
    detail, reports = build_detail(location, month_ctx, destination.include_service)
    check = calc.checks(month_ctx.month, reports, month_ctx.holidays, month_ctx.company_holidays, month_ctx.today)
    total = detail["totals"]["amount"]
    amounts = calc.fee_amounts(total, rate) if rate is not None else {"total_sales": total}
    row = IssueRow(destination, rate, detail, amounts, check, statement)
    if check["missing_price"]:
        row.blocks.append(("missing_price", f"単価なし {len(check['missing_price'])}日", check["missing_price"]))
    if not destination.addressee.strip():
        row.blocks.append(("addressee", "宛名なし", []))
    if rate is None:
        row.blocks.append(("rate", "率なし", []))
    if check["unconfirmed"]:
        row.warns.append(("unconfirmed", f"未確認 {len(check['unconfirmed'])}日", check["unconfirmed"]))
    if check["unsubmitted"]:
        row.warns.append(("unsubmitted", f"未提出 {len(check['unsubmitted'])}日", check["unsubmitted"]))
    if check["no_reports"]:
        row.warns.append(("no_reports", "日計表なし", []))
    if statement is not None:
        # 発行時の反映ON/OFFで計算し直して比べる（設定を後で変えただけでは差ありにしない）
        if statement.include_service != destination.include_service:
            detail_now, _ = build_detail(location, month_ctx, statement.include_service)
            now_total = detail_now["totals"]["amount"]
        else:
            now_total = total
        if now_total != statement.total_sales:
            row.diff_total = now_total
    return row


def issue_rows(month, today=None):
    """一括発行の表の行（計算書を出す売り場すべて・売り場No.順）。"""
    month_ctx = MonthContext.load(month, today)
    issued = {s.location_id: s for s in FeeStatement.objects.filter(month=month_ctx.month,
                                                                   status=FeeStatement.STATUS_ISSUED)}
    dests = (FeeDestination.objects.filter(enabled=True).select_related("location")
             .prefetch_related("rates").order_by("location__no", "location__name"))
    return [build_row(d, month_ctx, issued.get(d.location_id)) for d in dests], month_ctx


def current_total(statement):
    """詳細の「差あり」用：今の日計表で計算し直した総売上（発行時の反映ON/OFFで）。"""
    month_ctx = MonthContext.load(statement.month)
    detail, _ = build_detail(statement.location, month_ctx, statement.include_service)
    return detail["totals"]["amount"]


def preview_statement(destination, month, issue_date=None, pay_date=None):
    """発行前のプレビュー用に、保存しない計算書を作る。"""
    row = build_row(destination, MonthContext.load(month))
    if row.rate is None:
        raise FeeIssueError("率なし")
    return _new_statement(row, "", issue_date, pay_date, user=None)


def _new_statement(row, number, issue_date, pay_date, user):
    d = row.destination
    return FeeStatement(
        number=number, month=month_start(datetime.date.fromisoformat(row.detail["month"] + "-01")),
        location=d.location, issue_date=issue_date, pay_date=pay_date,
        location_name=d.location.name, addressee=d.addressee.strip(), rate=row.rate,
        include_service=d.include_service, detail=row.detail, issuer=issuer_snapshot(),
        staff_name=staff_name(user) if user else "", issued_by=user, **row.amounts,
    )


def issue_statements(month, location_ids, issue_date, pay_date, user):
    """選んだ売り場の計算書をまとめて発行する。1件でも発行できなければ全部やめる。"""
    month_ctx = MonthContext.load(month)
    if month_ctx.month > month_start(month_ctx.today):
        # 画面では表を出さないが、POSTを直接送られても未来の月は発行しない
        raise FeeIssueError("対象月が未来")
    ids = {int(i) for i in location_ids}
    dests = {d.location_id: d for d in FeeDestination.objects.filter(location_id__in=ids, enabled=True)
             .select_related("location").prefetch_related("rates")}
    errors, created = [], []
    try:
        with transaction.atomic():
            for loc_id in sorted(ids, key=lambda i: (dests[i].location.no, dests[i].location.name)
                                 if i in dests else (10 ** 9, "")):
                dest = dests.get(loc_id)
                if dest is None:
                    errors.append("対象外の売り場")
                    continue
                name = dest.location.name
                if FeeStatement.objects.filter(month=month_ctx.month, location_id=loc_id,
                                               status=FeeStatement.STATUS_ISSUED).exists():
                    errors.append(f"{name}（発行済み）")
                    continue
                row = build_row(dest, month_ctx)
                if row.blocks:
                    errors.append(f"{name}（{'・'.join(b[1] for b in row.blocks)}）")
                    continue
                st = _new_statement(row, FeeStatement.next_number(month_ctx.month), issue_date, pay_date, user)
                st.replaces = (FeeStatement.objects.filter(month=month_ctx.month, location_id=loc_id,
                                                           status=FeeStatement.STATUS_VOID, reissues__isnull=True)
                               .order_by("-voided_at", "-pk").first())
                st.save()
                created.append(st)
            if errors:
                raise FeeIssueError("、".join(errors))
    except IntegrityError:
        # 同時に発行された（番号・同じ月×売り場の制約にかかった）
        raise FeeIssueError("同時に発行された計算書あり。表示し直して確認")
    return created


def void_statement(statement, user, reason=""):
    if statement.is_void:
        return False
    statement.status = FeeStatement.STATUS_VOID
    statement.voided_by = user
    statement.voided_at = timezone.now()
    statement.void_reason = reason.strip()[:200]
    statement.save(update_fields=["status", "voided_by", "voided_at", "void_reason"])
    return True
