"""予約システムの社内向け画面（S4）。

仕様: .company/engineering/harness/specs/w001-予約システム-要件定義.md（§4-2/§4-3, §12-1, FR8/FR9）

- manage_pickup        … 当日スタッフ受取画面（@login_required）。拠点×受取日の予約を名前順で表示し、
                          受渡済/ノーショーを更新する。ワンオペの「名前付き別置き」運用の心臓（§12-1）。
- manage_list          … 予約管理一覧＋集計（@staff_member_required）。拠点/受取日/状態でフィルタ。
- manage_locations     … 拠点の予約ON/OFF・1メニュー枠数の設定（@staff_member_required）。
                          これまでシェルで手動操作していた reservation_enabled / default_product_cap を画面化。

顧客向け公開ページ（reserve_*）とは別レイヤー。ログイン必須の社内面。
受取画面はスタッフ（販売員）も使うので @login_required、設定系は管理者のみ @staff_member_required。
"""
from datetime import datetime

from django.contrib.admin.views.decorators import staff_member_required
from django.contrib.auth.decorators import login_required
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_http_methods

from sales.models import SalesLocation
from reservations.models import Reservation

_STATUS_LABELS = dict(Reservation.STATUS_CHOICES)


def _parse_date(raw):
    try:
        return datetime.strptime(raw or "", "%Y-%m-%d").date()
    except ValueError:
        return None


def _summarize(reservations):
    """状態別件数と、有効予約の総食数を数える。"""
    s = {"received": 0, "handed": 0, "noshow": 0, "cancelled": 0, "meals": 0, "total": 0}
    for r in reservations:
        s[r.status] = s.get(r.status, 0) + 1
        s["total"] += 1
        if r.is_active:
            s["meals"] += r.total_quantity
    return s


@login_required
def manage_pickup(request):
    """当日スタッフ受取画面：拠点×受取日の予約を名前順で。受渡/ノーショー更新の入口。"""
    locations = list(SalesLocation.objects.filter(reservation_enabled=True).order_by("name"))
    loc_id = request.GET.get("loc")
    location = next((l for l in locations if str(l.id) == str(loc_id)), None)
    if location is None and locations:
        location = locations[0]
    pickup = _parse_date(request.GET.get("date")) or timezone.localdate()

    reservations = []
    if location is not None:
        reservations = list(
            Reservation.objects
            .filter(sales_location=location, pickup_date=pickup)
            .select_related("member")
            .prefetch_related("items")
            .order_by("member__name", "created_at")
        )
    return render(request, "reservations/manage/pickup.html", {
        "locations": locations,
        "location": location,
        "pickup": pickup,
        "reservations": reservations,
        "summary": _summarize(reservations),
        "today": timezone.localdate(),
    })


@login_required
@require_http_methods(["POST"])
def manage_pickup_update(request):
    """受取画面からの状態更新（受渡済／ノーショー／戻す）。更新後は同じ拠点・日へ戻る。"""
    reservation = get_object_or_404(Reservation, pk=request.POST.get("reservation_id"))
    mapping = {
        "hand": Reservation.STATUS_HANDED,
        "noshow": Reservation.STATUS_NOSHOW,
        "undo": Reservation.STATUS_RECEIVED,
    }
    action = request.POST.get("action")
    if action in mapping:
        reservation.status = mapping[action]
        reservation.save(update_fields=["status"])
    base = reverse("reservations:manage_pickup")
    return redirect(f"{base}?loc={reservation.sales_location_id}"
                    f"&date={reservation.pickup_date:%Y-%m-%d}")


@staff_member_required
def manage_list(request):
    """予約管理一覧＋集計（管理者）。拠点／受取日／状態でフィルタ。"""
    qs = (Reservation.objects
          .select_related("sales_location", "member")
          .prefetch_related("items"))
    loc_id = request.GET.get("loc")
    status = request.GET.get("status")
    date = _parse_date(request.GET.get("date"))
    if loc_id:
        qs = qs.filter(sales_location_id=loc_id)
    if status in _STATUS_LABELS:
        qs = qs.filter(status=status)
    if date:
        qs = qs.filter(pickup_date=date)
    reservations = list(qs.order_by("-pickup_date", "sales_location__name", "member__name")[:300])

    return render(request, "reservations/manage/list.html", {
        "locations": SalesLocation.objects.filter(reservation_enabled=True).order_by("name"),
        "status_choices": Reservation.STATUS_CHOICES,
        "reservations": reservations,
        "summary": _summarize(reservations),
        "f_loc": loc_id or "",
        "f_status": status or "",
        "f_date": date,
    })


@staff_member_required
def manage_locations(request):
    """拠点ごとの予約ON/OFF・枠数（default_product_cap）設定（管理者）。"""
    if request.method == "POST":
        loc = get_object_or_404(SalesLocation, pk=request.POST.get("location_id"))
        loc.reservation_enabled = bool(request.POST.get("reservation_enabled"))
        try:
            loc.default_product_cap = max(0, int(request.POST.get("default_product_cap")))
        except (TypeError, ValueError):
            pass
        loc.save(update_fields=["reservation_enabled", "default_product_cap"])
        return redirect("reservations:manage_locations")

    return render(request, "reservations/manage/locations.html", {
        "locations": SalesLocation.objects.all().order_by("-reservation_enabled", "name"),
    })
