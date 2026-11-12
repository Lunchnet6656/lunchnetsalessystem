"""販売フィー計算書の画面（#206）。画面設計: harness/specs/lunchnetsale-販売フィー計算書-画面設計.md"""
import base64
import datetime
from urllib.parse import quote

from django.contrib import messages
from django.db import transaction
from django.http import Http404, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone

from sales.models import IssuerSettings, SalesLocation
from sales.permissions import NO_PERMISSION_MESSAGE, has_menu_permission, menu_permission_required

from . import pdf, services
from .forms import DestinationForm, IssueForm, IssuerForm, RateForm, normalize_addressee, parse_month
from .models import FeeDestination, FeeRate, FeeStatement, default_pay_date, month_start

VIEW = ("can_view_fees", "can_issue_fees")
ISSUE = "can_issue_fees"


def _can_issue(user):
    return has_menu_permission(user, ISSUE)


def _prev_month(today):
    return (month_start(today) - datetime.timedelta(days=1)).replace(day=1)


def _month_chips(today):
    return {"prev": f"{_prev_month(today):%Y-%m}", "this": f"{today:%Y-%m}"}


def _jp_month(month):
    return f"{month.year}年{month.month}月分"


def _pdf_response(content, filename, attachment=False):
    resp = HttpResponse(content, content_type="application/pdf")
    kind = "attachment" if attachment else "inline"
    ascii_name = filename.encode("ascii", "ignore").decode() or "statement.pdf"
    resp["Content-Disposition"] = f"{kind}; filename=\"{ascii_name}\"; filename*=UTF-8''{quote(filename)}"
    return resp


# ===== 宛先 =====

def _destination_rows():
    rows = []
    today = timezone.localdate()
    for d in (FeeDestination.objects.filter(enabled=True).select_related("location")
              .order_by("location__no", "location__name")):
        rate = d.rate_on(today)
        rows.append({"d": d, "rate": rate, "next": d.next_rate(today),
                     "attention": not d.addressee or rate is None})
    return rows


@menu_permission_required(ISSUE)
def destinations(request):
    rows = _destination_rows()
    enabled_ids = {r["d"].location_id for r in rows}
    candidates = SalesLocation.objects.exclude(pk__in=enabled_ids).order_by("no", "name")
    return render(request, "fees/destinations.html", {
        "rows": rows, "candidates": candidates, "tab": "dest",
        "has_locations": SalesLocation.objects.exists(),
    })


@menu_permission_required(ISSUE)
def destination_add(request):
    location = get_object_or_404(SalesLocation, pk=request.GET.get("location") or 0)
    return redirect(f"{reverse('fees:destination_edit', args=[location.pk])}?add=1")


def _rate_rows(dest, today):
    rows = []
    if not dest or not dest.pk:
        return rows
    current_found = False
    this_month = month_start(today)
    for r in dest.rates.select_related("created_by").order_by("-start_month"):
        if r.start_month > this_month:
            state = "upcoming"
        elif not current_found:
            state, current_found = "current", True
        else:
            state = "ended"
        rows.append({"r": r, "state": state})
    return rows


def _add_rate(dest, rate_form, user):
    FeeRate.objects.create(destination=dest, start_month=rate_form.cleaned_data["start_month"],
                           rate=rate_form.cleaned_data["rate"], created_by=user)


@menu_permission_required(ISSUE)
def destination_edit(request, location_pk):
    location = get_object_or_404(SalesLocation, pk=location_pk)
    dest = FeeDestination.objects.filter(location=location).first()
    adding = request.GET.get("add") == "1" or request.POST.get("add") == "1"
    today = timezone.localdate()
    initial = {"enabled": True if adding else (dest.enabled if dest else False),
               "addressee": dest.addressee if dest else "",
               "include_service": dest.include_service if dest else True,
               "note": dest.note if dest else ""}
    form = DestinationForm(initial=initial)
    rate_form = RateForm(initial={"start_month": f"{today:%Y-%m}"})
    cancel_id = request.GET.get("cancel", "")

    if request.method == "POST":
        action = request.POST.get("action")
        # 宛先と率は1つのフォーム。どちらのボタンでも入力済みの値を捨てない（率だけ［追加］して宛先が保存されず、一覧に出ない事故があった）
        rate_filled = bool(request.POST.get("rate", "").strip())
        if action == "save":
            form = DestinationForm(request.POST)
            if rate_filled:
                rate_form = RateForm(request.POST, destination=dest)
            if form.is_valid() and (not rate_filled or rate_form.is_valid()):
                with transaction.atomic():
                    dest = dest or FeeDestination(location=location)
                    dest.enabled = form.cleaned_data["enabled"]
                    dest.addressee = form.cleaned_data["addressee"]
                    dest.include_service = form.cleaned_data["include_service"]
                    dest.note = form.cleaned_data["note"]
                    dest.updated_by = request.user
                    dest.save()
                    if rate_filled:
                        _add_rate(dest, rate_form, request.user)
                messages.success(request, f"{location.name} の宛先を更新")
                return redirect("fees:destinations")
        elif action == "add_rate":
            form = DestinationForm(request.POST)
            rate_form = RateForm(request.POST, destination=dest)
            if rate_form.is_valid():
                with transaction.atomic():
                    # 宛名が空でも率は登録する。宛先の入力も一緒に残す（宛名なしは一覧で「未設定」と出る）
                    dest = dest or FeeDestination(location=location)
                    dest.enabled = bool(request.POST.get("enabled"))
                    dest.addressee = normalize_addressee(request.POST.get("addressee"))
                    dest.include_service = bool(request.POST.get("include_service"))
                    # 備考は行数・文字数を満たすときだけ残す（満たさなければ前の値のまま。［更新］でエラーを出す）
                    if form.is_valid() or "note" not in form.errors:
                        dest.note = form.cleaned_data.get("note", dest.note)
                    dest.updated_by = request.user
                    dest.save()
                    _add_rate(dest, rate_form, request.user)
                m = rate_form.cleaned_data["start_month"]
                messages.success(request, f"{m:%Y/%m} から {pdf.rate_label(rate_form.cleaned_data['rate'])}% を追加")
                url = reverse("fees:destination_edit", args=[location.pk])
                return redirect(f"{url}?add=1" if adding and not dest.enabled else url)
        elif action == "cancel_rate" and dest:
            rate = get_object_or_404(FeeRate, pk=request.POST.get("rate_id") or 0, destination=dest)
            if rate.start_month > month_start(today):
                label = f"{rate.start_month:%Y/%m} から {pdf.rate_label(rate.rate)}%"
                rate.delete()
                messages.success(request, f"{label} を取消")
            else:
                messages.error(request, "適用中・終了の率は取消できない")
            url = reverse("fees:destination_edit", args=[location.pk])
            return redirect(f"{url}?add=1" if adding and not dest.enabled else url)

    return render(request, "fees/destination_edit.html", {
        "location": location, "dest": dest, "form": form, "rate_form": rate_form,
        "rate_rows": _rate_rows(dest, today), "cancel_id": cancel_id, "adding": adding,
        "service_label": services.calc.service_label_of(location),
    })


@menu_permission_required(ISSUE)
def issuer(request):
    obj = IssuerSettings.load()
    form = IssuerForm(instance=obj)
    if request.method == "POST":
        form = IssuerForm(request.POST, instance=obj)
        if form.is_valid():
            obj = form.save(commit=False)
            seal = request.FILES.get("seal_image")
            if seal:
                ctype = seal.content_type or "image/png"
                obj.seal_image_data = f"data:{ctype};base64,{base64.b64encode(seal.read()).decode('ascii')}"
            elif request.POST.get("remove_seal"):
                obj.seal_image_data = ""
            obj.updated_by = request.user
            obj.save()
            messages.success(request, "発行元を更新")
            return redirect("fees:issuer")
    return render(request, "fees/issuer.html", {"form": form, "obj": obj, "tab": "issuer"})


# ===== 計算書（月ごとの1画面：発行・PDF・取消をここで） =====

def _month_page(request, month, form=None, selected=None, error=""):
    today = timezone.localdate()
    can_issue = _can_issue(request.user)
    future = month > month_start(today)
    rows = [] if future else services.issue_rows(month, today)[0]
    if selected is None:
        selected = {r.location.pk for r in rows if r.default_checked}
    for r in rows:
        r.pickable = can_issue and r.selectable
        r.checked = r.pickable and r.location.pk in selected
    issuable = [r for r in rows if r.selectable] if can_issue else []
    checked = [r for r in issuable if r.checked]
    sums = {k: sum(r.amounts[k] for r in checked)
            for k in ("total_sales", "fee_excluded", "fee_tax", "pay_amount")}
    issued = [r for r in rows if r.statement]
    if form is None:
        form = IssueForm(initial={"issue_date": today, "pay_date": default_pay_date(today)})
    voided = (FeeStatement.objects.filter(month=month, status=FeeStatement.STATUS_VOID)
              .order_by("location__no", "number"))
    return render(request, "fees/list.html", {
        "month": month, "month_value": f"{month:%Y-%m}", "month_label": _jp_month(month),
        "future": future, "rows": rows, "issuable": issuable, "form": form, "error": error,
        "checked_count": len(checked), "sums": sums, "can_issue": can_issue,
        "issued_count": len(issued), "issued_total": sum(r.statement.pay_amount for r in issued),
        "unissued_count": len(rows) - len(issued), "voided": voided,
        "chips": _month_chips(today),
        "can_view_reports": has_menu_permission(request.user, "can_view_daily_report_list"),
    })


def _list_url(month):
    return f"{reverse('fees:list')}?month={month:%Y-%m}"


@menu_permission_required(VIEW)
def statement_list(request):
    today = timezone.localdate()
    month = parse_month(request.GET.get("month") or request.POST.get("month")) or _prev_month(today)
    if request.method != "POST":
        return _month_page(request, month)
    if not _can_issue(request.user):
        messages.error(request, NO_PERMISSION_MESSAGE, extra_tags="alert alert-danger")
        return redirect("dashboard")
    form = IssueForm(request.POST)
    selected = {int(v) for v in request.POST.getlist("location") if v.isdigit()}
    if not form.is_valid():
        return _month_page(request, month, form, selected)
    if not selected:
        return _month_page(request, month, form, selected, error="選択なし")
    if month > month_start(today):
        return _month_page(request, month, form, selected)
    try:
        created = services.issue_statements(month, selected, form.cleaned_data["issue_date"],
                                            form.cleaned_data["pay_date"], request.user)
    except services.FeeIssueError as e:
        return _month_page(request, month, form, selected, error=f"発行できなかった：{e}")
    messages.success(request, f"{_jp_month(month)} {len(created)}件を発行")
    return redirect(_list_url(month))


@menu_permission_required(VIEW)
def issue(request):
    """旧・一括発行のURL（ブックマーク対策）。月ごとの計算書画面へ。"""
    month = parse_month(request.GET.get("month")) or _prev_month(timezone.localdate())
    return redirect(_list_url(month))


@menu_permission_required(ISSUE)
def issue_preview(request):
    month = parse_month(request.GET.get("month"))
    dest = get_object_or_404(FeeDestination, location_id=request.GET.get("location") or 0, enabled=True)
    if month is None:
        raise Http404

    def as_date(value):
        try:
            return datetime.date.fromisoformat(value or "")
        except ValueError:
            return None
    try:
        st = services.preview_statement(dest, month, as_date(request.GET.get("issue_date")),
                                        as_date(request.GET.get("pay_date")))
    except services.FeeIssueError:
        raise Http404
    st.staff_name = services.staff_name(request.user)
    return _pdf_response(pdf.generate(st, preview=True), f"プレビュー_{month:%Y%m}_{dest.location.name}.pdf")


# ===== 詳細・取消・PDF =====

@menu_permission_required(VIEW)
def statement_detail(request, pk):
    st = get_object_or_404(FeeStatement.objects.select_related("location", "voided_by", "replaces"), pk=pk)
    diff_total = None
    if not st.is_void:
        now = services.current_total(st)
        if now != st.total_sales:
            diff_total = now
    return render(request, "fees/detail.html", {
        "st": st, "diff_total": diff_total, "can_issue": _can_issue(request.user),
        "reissue": st.reissues.order_by("-pk").first(), "rate": pdf.rate_label(st.rate),
        "table": pdf.table_context(st.detail),
    })


@menu_permission_required(ISSUE)
def statement_void(request, pk):
    st = get_object_or_404(FeeStatement, pk=pk)
    if st.is_void:
        messages.info(request, "取消済み")
        return redirect("fees:detail", pk=st.pk)
    if request.method == "POST":
        services.void_statement(st, request.user, request.POST.get("void_reason", ""))
        messages.success(request, f"{st.number} を取消")
        # 同じ月の計算書画面へ戻す（その行は未発行に戻り、そのまま再発行できる）
        return redirect(_list_url(st.month))
    return render(request, "fees/void.html", {"st": st, "from_month": request.GET.get("from") == "month"})


@menu_permission_required(VIEW)
def statement_pdf(request, pk):
    st = get_object_or_404(FeeStatement, pk=pk)
    return _pdf_response(pdf.generate(st), f"{st.number}.pdf", attachment=bool(request.GET.get("dl")))


@menu_permission_required(VIEW)
def statement_pdf_month(request):
    """その月の発行済み全件を1つのPDF（1社1ページ）に。取消は入れない。"""
    month = parse_month(request.GET.get("month"))
    if month is None:
        raise Http404
    sts = list(FeeStatement.objects.filter(month=month, status=FeeStatement.STATUS_ISSUED)
               .select_related("location").order_by("location__no", "number"))
    if not sts:
        messages.error(request, "発行済みなし")
        return redirect(_list_url(month))
    try:
        content = pdf.generate_combined(sts)
    except Exception:
        messages.error(request, "PDFを作成できなかった")
        return redirect(_list_url(month))
    return _pdf_response(content, f"計算書_{month:%Y%m}.pdf", attachment=True)
