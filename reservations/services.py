"""予約システムのドメインロジック（純粋関数中心・テスト容易に分離）。

仕様: .company/engineering/harness/specs/w001-予約システム-要件定義.md

メニューは水〜翌火で固定（週キー＝水曜日付 'YYYY-MM-DD'）。
締切＝受取日の前営業日16:00。営業日判定は sales 側の is_business_day（土日祝除外）を流用。
"""
from __future__ import annotations

import os
from datetime import date, datetime, time, timedelta

import secrets

from django.db import IntegrityError, transaction
from django.db.models import Sum
from django.utils import timezone

from sales.management.commands.generate_status_json import is_business_day
from sales.models import Product, SalesLocation
from reservations.models import LineMember, Reservation, ReservationItem


# 締切時刻（時）。既定16＝前営業日16:00。動作確認時のみ環境変数 RESERVE_DEADLINE_HOUR で上書き可。
DEADLINE_HOUR = int(os.environ.get("RESERVE_DEADLINE_HOUR", "16"))
_ACTIVE = list(Reservation.ACTIVE_STATUSES)


class ReservationError(Exception):
    """予約作成時の業務エラー（締切超過・枠満杯など）。利用者に見せる文言を持つ。"""


class DuplicateReservation(ReservationError):
    """同一会員・同一受取日に既に有効な予約がある（二重注文防止）。既存予約を保持して誘導に使う。"""

    def __init__(self, existing):
        self.existing = existing
        super().__init__("すでにこの日のご予約があります。")


def week_key_for_date(d: date) -> str:
    """受取日が属する週（水曜起算）のキー 'YYYY-MM-DD'。メニューは水〜翌火固定。"""
    days_since_wed = (d.weekday() - 2) % 7  # 水曜=2
    week_start = d - timedelta(days=days_since_wed)
    return week_start.strftime("%Y-%m-%d")


def menu_for_date(d: date):
    """受取日のメニュー（Product のクエリセット・全件）。"""
    return Product.objects.filter(week=week_key_for_date(d)).order_by("no", "id")


def reservable_menu(d: date):
    """予約可能な弁当メニュー。割増アイテム（大盛りごはん）は弁当ではないので除外。"""
    return menu_for_date(d).exclude(name__contains="大盛")


def next_business_day(d: date) -> date:
    nxt = d + timedelta(days=1)
    while not is_business_day(nxt):
        nxt += timedelta(days=1)
    return nxt


def previous_business_day(d: date) -> date:
    prev = d - timedelta(days=1)
    while not is_business_day(prev):
        prev -= timedelta(days=1)
    return prev


def default_pickup_date(today: date) -> date:
    """既定の受取日＝翌営業日。"""
    return next_business_day(today)


def selectable_pickup_dates(today: date, now: datetime | None = None) -> list[date]:
    """選べる受取日＝翌営業日が属する週（水〜翌火）の中の、翌営業日以降・営業日・締切前の日。

    メニューは週固定なので「その週の範囲内」に限定する（しょうへい確定 2026-06-09）。
    """
    default = default_pickup_date(today)
    wk = week_key_for_date(default)
    week_start = datetime.strptime(wk, "%Y-%m-%d").date()
    out = []
    for i in range(7):  # 水〜翌火の7日
        d = week_start + timedelta(days=i)
        if d < default:
            continue
        if not is_business_day(d):
            continue
        if not is_open_for(d, now=now):
            continue
        out.append(d)
    return out


def reservation_deadline(pickup_date: date) -> datetime:
    """締切＝受取日の前営業日16:00（aware datetime）。"""
    prev = previous_business_day(pickup_date)
    naive = datetime.combine(prev, time(DEADLINE_HOUR, 0))
    return timezone.make_aware(naive, timezone.get_current_timezone())


def is_open_for(pickup_date: date, now: datetime | None = None) -> bool:
    """その受取日の予約受付が締切前で開いているか。"""
    now = now or timezone.localtime()
    return now < reservation_deadline(pickup_date)


DEFAULT_LARGE_SURCHARGE = 50  # 「大盛りごはん」Product が無い週のフォールバック


def unit_price_for(location: SalesLocation, product: Product):
    """拠点の price_type に対応する単価（price_A / price_B / price_C）。"""
    mapping = {"A": product.price_A, "B": product.price_B, "C": product.price_C}
    return mapping.get((location.price_type or "A").upper().strip(), product.price_A)


def large_surcharge_for(location: SalesLocation, d: date):
    """大盛り1個あたりの割増。その週の「大盛りごはん」Product 価格を採用（無ければ既定50）。"""
    big = Product.objects.filter(week=week_key_for_date(d), name__contains="大盛").first()
    if big is not None:
        return unit_price_for(location, big)
    return DEFAULT_LARGE_SURCHARGE


def reserved_count(location: SalesLocation, pickup_date: date, product: Product) -> int:
    """その拠点×受取日×メニューの有効予約（受付済/受渡済）の個数合計。DBで集計（N+1回避）。"""
    total = ReservationItem.objects.filter(
        reservation__sales_location=location,
        reservation__pickup_date=pickup_date,
        reservation__status__in=_ACTIVE,
        product=product,
    ).aggregate(total=Sum("quantity"))["total"]
    return total or 0


def remaining_for(location: SalesLocation, pickup_date: date, product: Product) -> int:
    """残り予約枠（= 1メニュー上限 − 有効予約数）。"""
    cap = location.default_product_cap or 0
    return max(0, cap - reserved_count(location, pickup_date, product))


@transaction.atomic
def create_reservation(member: LineMember, location: SalesLocation, pickup_date: date,
                       items, now: datetime | None = None) -> Reservation:
    """予約を作成。締切・枠を検証し、満たさなければ ReservationError を送出。

    items: [(Product, large, regular, small), ...]
    枠（default_product_cap）は盛りに関係なく1メニューの合計個数で判定する。
    """
    if not location.reservation_enabled:
        raise ReservationError("この店舗は現在、予約を受け付けていません。")
    if not is_open_for(pickup_date, now=now):
        raise ReservationError("受付は締め切りました（前営業日16時まで）。")

    # 二重注文防止：同一会員・同一受取日に有効な予約があれば新規を作らせない。
    # （複数個は1予約内の個数で足りる＝別予約は事故。変更は取消→取り直し）
    dup = (Reservation.objects
           .filter(member=member, pickup_date=pickup_date, status__in=_ACTIVE)
           .first())
    if dup is not None:
        raise DuplicateReservation(dup)

    cleaned = [(p, int(l), int(r), int(s)) for p, l, r, s in items
               if int(l) + int(r) + int(s) > 0]
    if not cleaned:
        raise ReservationError("メニューと個数を選んでください。")

    surcharge = large_surcharge_for(location, pickup_date)
    # 同時予約の競合を防ぐため拠点行をロック（Postgresで有効・sqliteでは実質no-op）
    locked = SalesLocation.objects.select_for_update().get(pk=location.pk)
    for product, large, regular, small in cleaned:
        total = large + regular + small
        if total > remaining_for(locked, pickup_date, product):
            raise ReservationError(f"「{product.name}」は予約枠が埋まりました。個数を減らしてください。")

    reservation_number = _reservation_number_for(locked)
    try:
        # savepoint：DB一意制約（同一会員×受取日）に競合したら既存へ誘導する
        with transaction.atomic():
            reservation = Reservation.objects.create(
                member=member, sales_location=locked, pickup_date=pickup_date,
                reservation_number=reservation_number,
            )
            for product, large, regular, small in cleaned:
                ReservationItem.objects.create(
                    reservation=reservation, product=product, product_name=product.name,
                    quantity_large=large, quantity_regular=regular, quantity_small=small,
                    unit_price=unit_price_for(locked, product), large_surcharge=surcharge,
                )
    except IntegrityError:
        existing = (Reservation.objects
                    .filter(member=member, pickup_date=pickup_date, status__in=_ACTIVE)
                    .first())
        raise DuplicateReservation(existing)
    return reservation


def _reservation_number_for(location) -> str:
    """予約番号＝売り場識別プレフィックス（拠点no・2桁ゼロ詰め）＋ランダム。例: 01-A3F9B1C2。

    スタッフが番号の頭2桁で売り場を判別できる。ランダム部は衝突回避に再試行。
    """
    prefix = f"{(getattr(location, 'no', 0) or 0):02d}"
    for _ in range(6):
        num = f"{prefix}-{secrets.token_hex(4).upper()}"
        if not Reservation.objects.filter(reservation_number=num).exists():
            return num
    return f"{prefix}-{secrets.token_hex(4).upper()}"


def cancel_reservation(reservation) -> None:
    """お客様自身による取消。受付済のみ取消可（枠を解放＝再予約できる）。"""
    if reservation.status != Reservation.STATUS_RECEIVED:
        raise ReservationError("この予約はすでに取り消せない状態です。")
    reservation.status = Reservation.STATUS_CANCELLED
    reservation.save(update_fields=["status"])


def mark_handed_by_customer(reservation) -> None:
    """お客様自身による受け取り完了。受付済のみ。スタッフの受渡済と同じ状態に倒す。"""
    if reservation.status != Reservation.STATUS_RECEIVED:
        raise ReservationError("この予約は受け取り完了にできない状態です。")
    reservation.status = Reservation.STATUS_HANDED
    reservation.save(update_fields=["status"])
