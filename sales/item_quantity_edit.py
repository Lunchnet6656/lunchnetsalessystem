"""持参数の修正と消し込み（振分の現場でタブレットから使う）。

予定（振分表から届いた数）は変えず、現在の数だけを直して履歴に残す。
タブレットと電話番のPCで同じ日を同時に直すことがあるので、画面を開いた時の数と
今の数が1マスでも違えば全件取りやめる（黙って上書きしない）。

列＝販売場所（ItemQuantity）＋振分だけの項目（AllocationExtraQuantity・なだや等）。
並びと消し込み不要は販売場所データの［振分設定］。列のキーは "L<販売場所id>" / "X<項目id>"。
"""
import logging
from datetime import timedelta
from dataclasses import dataclass, field

from django.core.management import call_command
from django.db import transaction
from django.utils import timezone

from sales.item_quantity_import import next_business_day
from sales.models import (
    AllocationExtra, AllocationExtraQuantity, AllocationLayout, SalesLocation, DailyReport, ItemQuantity, ItemQuantityChange, ItemQuantityUpload,
)

logger = logging.getLogger(__name__)

WEEKDAYS = "月火水木金土日"

# 大盛りごはん（No.11）は弁当ではない。振分・消し込みの対象外で、合計にも入れない（日計表の total_quantity と同じ扱い）
EXTRA_PRODUCT_NO = 11


def editable_dates(today):
    # 「明日」は次の営業日（金曜の電話番が月曜分を直せるように）
    return [today, next_business_day(today)]


def short_date(d):
    return f"{d.month}/{d.day}({WEEKDAYS[d.weekday()]})"


def user_label(user):
    if user is None:
        return ""
    return f"{user.last_name} {user.first_name}".strip() or user.username


def _hm(t):
    return f"{t.hour}:{t.minute:02d}"


def _key(product_id, column_key):
    return f"{product_id}:{column_key}"


@dataclass
class Column:
    """持参数の画面の1列（販売場所 or 振分だけの項目）。"""
    key: str
    name: str
    order: tuple
    check_skip: bool
    location: object = None  # SalesLocation
    extra: object = None     # AllocationExtra
    temp_slot: bool = False  # 振分設定の「臨時の列」の行（臨時の列をまとめて入れる位置）

    @property
    def route(self):
        """振分の便（シフトの便とは別）。便の区切りに使う。"""
        owner = self.location or self.extra
        return owner.allocation_route if owner else None


TEMP_SLOT_KEY = "T"


def temp_slot_column(temp_order):
    return Column(key=TEMP_SLOT_KEY, name="臨時の列", check_skip=False, temp_slot=True,
                  order=_temp_order(temp_order, 0, 0))


def _temp_order(temp_order, col, extra_id):
    # 臨時の列は振分設定の「臨時の列」の行の位置に、振分表の列の順でまとめて入る。位置が未設定なら末尾
    return (0, temp_order, 2, col, extra_id) if temp_order is not None else (2, 0, 2, col, extra_id)


def location_column(loc):
    return Column(
        key=f"L{loc.id}", name=loc.name, location=loc, check_skip=loc.allocation_check_skip,
        # 振分の並びが空の販売場所（新しく足した店）は末尾に販売場所No.順
        order=(int(loc.allocation_order is None), loc.allocation_order or 0, 0, loc.no, loc.id),
    )


def extra_column(extra, temp_order=None):
    if extra.temp_date:
        order = _temp_order(temp_order, extra.allocation_order or 0, extra.id)
    else:
        order = (int(extra.allocation_order is None), extra.allocation_order or 0, 1, 0, extra.id)
    return Column(key=f"X{extra.id}", name=extra.name, extra=extra, check_skip=extra.check_skip, order=order)


def allocation_columns():
    """振分設定の並び（全販売場所＋全振分だけの項目）。販売場所データの［振分設定］タブで使う。"""
    cols = [location_column(loc) for loc in SalesLocation.objects.select_related("allocation_route")]
    cols += [extra_column(x) for x in AllocationExtra.objects.filter(temp_date__isnull=True).select_related("allocation_route")]
    cols.append(temp_slot_column(AllocationLayout.temp_order_value()))
    return sorted(cols, key=lambda c: c.order)


class Day:
    """その日の持参数を 列×メニュー の形で持つ。"""

    def __init__(self, d, lock=False):
        self.date = d
        qs = ItemQuantity.objects.filter(target_date=d.isoformat()).select_related(
            "product", "sales_location", "sales_location__allocation_route")
        xqs = AllocationExtraQuantity.objects.filter(target_date=d).select_related(
            "product", "extra", "extra__allocation_route")
        temp_order = AllocationLayout.temp_order_value()
        if lock:
            qs = qs.select_for_update(of=("self",))
            xqs = xqs.select_for_update(of=("self",))
        self.rows = list(qs)
        self.extra_rows = list(xqs)
        self.cells = {}
        products, columns = {}, {}
        for iq in self.rows:
            col = columns.get(f"L{iq.sales_location_id}") or location_column(iq.sales_location)
            columns[col.key] = col
            self.cells[(iq.product_id, col.key)] = iq
            products[iq.product_id] = iq.product
        for x in self.extra_rows:
            col = columns.get(f"X{x.extra_id}") or extra_column(x.extra, temp_order)
            columns[col.key] = col
            self.cells[(x.product_id, col.key)] = x
            products[x.product_id] = x.product
        self.products = sorted(products.values(), key=lambda p: (p.no, p.id))
        self.columns = sorted(columns.values(), key=lambda c: c.order)
        self.target_week = self.rows[0].target_week if self.rows else None

    @property
    def locations(self):
        return [c.location for c in self.columns if c.location]

    def value(self, product_id, column_key):
        iq = self.cells.get((product_id, column_key))
        return iq.quantity if iq else 0

    def location_totals(self):
        """販売場所ごとの合計（出店状況の判定用。振分だけの項目は入れない）。"""
        totals = {c.key: 0 for c in self.columns if c.location}
        for (_, ck), iq in self.cells.items():
            if ck in totals:
                totals[ck] += iq.quantity
        return totals

    def off_keys(self):
        """全メニュー0の列＝その日は中止。直して0にした列は戻せるよう、予定も0の列だけ。"""
        now, plan = {}, {}
        for (pid, ck), iq in self.cells.items():
            if iq.product.no == EXTRA_PRODUCT_NO:
                continue
            now[ck] = now.get(ck, 0) + iq.quantity
            plan[ck] = plan.get(ck, 0) + iq.planned_quantity
        return {c.key for c in self.columns if not now.get(c.key) and not plan.get(c.key)}

    def get_or_create(self, product_id, column):
        iq = self.cells.get((product_id, column.key))
        if iq is None:
            # 振分表が送っていないマス（予定0）。その列は営業しているので現場で足せる
            if column.location:
                iq = ItemQuantity.objects.create(
                    target_date=self.date.isoformat(), target_week=self.target_week,
                    product_id=product_id, sales_location=column.location, quantity=0, planned_quantity=0,
                )
            else:
                iq = AllocationExtraQuantity.objects.create(
                    target_date=self.date, product_id=product_id, extra=column.extra,
                    quantity=0, planned_quantity=0,
                )
            self.cells[(product_id, column.key)] = iq
        return iq


def latest_upload(d):
    return ItemQuantityUpload.objects.filter(target_date=d, ok=True).first()


def screen(d, today, user):
    day = Day(d)
    upload = latest_upload(d)
    off = day.off_keys()
    submitted = set(DailyReport.objects.filter(date=d).values_list("location", flat=True))
    editable = d in editable_dates(today)
    nxt = next_business_day(today)
    dates = [
        {"date": today, "label": short_date(today), "suffix": "今日"},
        {"date": nxt, "label": short_date(nxt), "suffix": "明日" if nxt == today + timedelta(days=1) else ""},
    ]
    history = _history(d, upload)
    data = {
        "date": d.isoformat(),
        "editable": editable,
        "uploadId": upload.id if upload else None,
        "products": [{"id": p.id, "name": p.name, "extra": p.no == EXTRA_PRODUCT_NO} for p in day.products],
        # 並びは販売場所データの［振分設定］の順。便の区切りは画面側で、便が変わる所に入れる
        "locations": [
            {"id": c.key, "name": c.name, "off": c.key in off, "skip": c.check_skip,
             "submitted": bool(c.location) and c.name in submitted,
             "route": c.route.id if c.route else None, "routeName": c.route.name if c.route else "",
             "routeTime": _hm(c.route.departure_time) if c.route else ""}
            for c in day.columns
        ],
        "cells": {
            _key(pid, lid): {"cur": iq.quantity, "plan": iq.planned_quantity, "ck": iq.checked_at is not None}
            for (pid, lid), iq in day.cells.items()
        },
    }
    return {
        "date": d,
        "date_label": short_date(d),
        "dates": dates,
        "editable": editable,
        "has_data": bool(day.rows or day.extra_rows),
        "last_upload": upload,
        "history": history,
        "change_count": sum(1 for h in history if h["kind"] == "change"),
        "login_name": user_label(user),
        "grid_data": data,
    }


def _history(d, upload):
    changes = ItemQuantityChange.objects.filter(target_date=d).select_related(
        "product", "sales_location", "extra", "changed_by")
    items = [{
        "kind": "change", "at": c.changed_at, "location": (c.sales_location or c.extra).name,
        "product": c.product.name,
        "before": c.before, "after": c.after, "by": user_label(c.changed_by),
        # 受信より前の修正は再送で上書きされて消えている
        "stale": bool(upload and c.changed_at < upload.received_at),
    } for c in changes]
    items += [{"kind": "upload", "at": u.received_at}
              for u in ItemQuantityUpload.objects.filter(target_date=d, ok=True)]
    items.sort(key=lambda h: h["at"], reverse=True)
    return items


def _column_key(value):
    """列のキー。数字だけ（振分設定より前の画面・販売場所id）は販売場所として読む。"""
    s = str(value)
    return f"L{s}" if s.isdigit() else s


@dataclass
class SaveResult:
    ok: bool = False
    saved: int = 0
    error: str = ""
    reupload: bool = False
    conflicts: list = field(default_factory=list)
    publish: str = ""  # "" / "ok" / "failed"


def _parse_changes(raw):
    """[{product_id, location_id(列のキー), value, base}] を整数に。おかしければ None。"""
    if not isinstance(raw, list) or not raw:
        return None
    parsed = {}
    for c in raw:
        try:
            pid, lid = int(c["product_id"]), _column_key(c["location_id"])
            value, base = c["value"], c["base"]
        except (KeyError, TypeError, ValueError):
            return None
        # bool は int の仲間なので弾く。数字以外・マイナスは1件でもあれば全件取りやめ
        if type(value) is not int or type(base) is not int or value < 0 or base < 0:
            return None
        parsed[(pid, lid)] = (value, base)
    return parsed


def save_changes(d, user, upload_id, raw_changes, today):
    result = SaveResult()
    if d not in editable_dates(today):
        result.error = "閲覧のみの日付"
        return result
    changes = _parse_changes(raw_changes)
    if changes is None:
        result.error = "0以上の整数"
        return result

    with transaction.atomic():
        day = Day(d, lock=True)
        upload = latest_upload(d)
        if (upload.id if upload else None) != upload_id:
            result.reupload = True
            return result
        products = {p.id: p for p in day.products}
        columns = {c.key: c for c in day.columns}
        open_ids = set(columns) - day.off_keys()
        if any(pid not in products or lid not in open_ids for pid, lid in changes):
            result.error = "修正できないマス"
            return result

        for (pid, lid), (value, base) in changes.items():
            current = day.value(pid, lid)
            if current != base:
                result.conflicts.append({
                    "product_id": pid, "location_id": lid, "current": current,
                    "label": f"{columns[lid].name}×{products[pid].name}",
                })
        if result.conflicts:
            return result

        before_totals = day.location_totals()
        for (pid, lid), (value, base) in changes.items():
            if value == base:
                continue
            col = columns[lid]
            iq = day.get_or_create(pid, col)
            iq.quantity = value
            iq.save(update_fields=["quantity"])
            ItemQuantityChange.objects.create(
                target_date=d, product_id=pid, sales_location=col.location, extra=col.extra,
                before=base, after=value, changed_by=user,
            )
            result.saved += 1
        after_totals = day.location_totals()

    result.ok = True
    # 出店状況は店の合計が1以上か0かしか見ないので、またいだ時だけ反映する（明日分は朝8:00の定時で反映）
    crossed = any((before_totals.get(lid, 0) > 0) != (total > 0) for lid, total in after_totals.items())
    if d == today and crossed:
        try:
            call_command("publish_status_json")
            result.publish = "ok"
        except Exception:
            logger.exception("持参数の修正後の publish_status_json に失敗")
            result.publish = "failed"
    return result


def checked_keys(d):
    return [
        _key(pid, f"L{lid}") for pid, lid in ItemQuantity.objects.filter(
            target_date=d.isoformat(), checked_at__isnull=False,
        ).values_list("product_id", "sales_location_id")
    ] + [
        _key(pid, f"X{xid}") for pid, xid in AllocationExtraQuantity.objects.filter(
            target_date=d, checked_at__isnull=False,
        ).values_list("product_id", "extra_id")
    ]


def save_checks(d, user, raw, today):
    """消し込みの✓を保存する。保存できた件数を返す（None＝受け付けない）。"""
    if d not in editable_dates(today) or not isinstance(raw, list):
        return None
    items = {}
    for c in raw:
        try:
            items[(int(c["product_id"]), _column_key(c["location_id"]))] = bool(c["checked"])
        except (KeyError, TypeError, ValueError):
            return None
    with transaction.atomic():
        day = Day(d, lock=True)
        product_ids = {p.id for p in day.products}
        # 消し込み不要の列は✓を付けない（画面でも押せない）
        open_ids = {c.key for c in day.columns if not c.check_skip} - day.off_keys()
        now = timezone.now()
        saved = 0
        for (pid, lid), checked in items.items():
            # 0のマスは✓しなくても済み扱い（画面でも押せない）。記録も作らない
            if pid not in product_ids or lid not in open_ids or day.value(pid, lid) == 0:
                continue
            iq = day.cells[(pid, lid)]
            if checked and iq.checked_at is None:
                iq.checked_at, iq.checked_by = now, user
            elif not checked:
                iq.checked_at, iq.checked_by = None, None
            iq.save(update_fields=["checked_at", "checked_by"])
            saved += 1
    return saved
