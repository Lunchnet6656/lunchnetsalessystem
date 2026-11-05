"""持参数の修正と消し込み（振分の現場でタブレットから使う）。

予定（振分表から届いた数）は変えず、現在の数だけを直して履歴に残す。
タブレットと電話番のPCで同じ日を同時に直すことがあるので、画面を開いた時の数と
今の数が1マスでも違えば全件取りやめる（黙って上書きしない）。
"""
import logging
from datetime import timedelta
from dataclasses import dataclass, field

from django.core.management import call_command
from django.db import transaction
from django.utils import timezone

from sales.item_quantity_import import next_business_day
from sales.models import DailyReport, ItemQuantity, ItemQuantityChange, ItemQuantityUpload

logger = logging.getLogger(__name__)

WEEKDAYS = "月火水木金土日"


def editable_dates(today):
    # 「明日」は次の営業日（金曜の電話番が月曜分を直せるように）
    return [today, next_business_day(today)]


def short_date(d):
    return f"{d.month}/{d.day}({WEEKDAYS[d.weekday()]})"


def user_label(user):
    if user is None:
        return ""
    return f"{user.last_name} {user.first_name}".strip() or user.username


def _key(product_id, location_id):
    return f"{product_id}:{location_id}"


class Day:
    """その日の持参数を 店×メニュー の形で持つ。"""

    def __init__(self, d, lock=False):
        self.date = d
        qs = ItemQuantity.objects.filter(target_date=d.isoformat()).select_related("product", "sales_location")
        if lock:
            qs = qs.select_for_update(of=("self",))
        self.rows = list(qs)
        self.cells = {}
        products, locations = {}, {}
        for iq in self.rows:
            self.cells[(iq.product_id, iq.sales_location_id)] = iq
            products[iq.product_id] = iq.product
            locations[iq.sales_location_id] = iq.sales_location
        self.products = sorted(products.values(), key=lambda p: (p.no, p.id))
        self.locations = sorted(locations.values(), key=lambda loc: (loc.no, loc.id))
        self.target_week = self.rows[0].target_week if self.rows else None

    def value(self, product_id, location_id):
        iq = self.cells.get((product_id, location_id))
        return iq.quantity if iq else 0

    def location_totals(self):
        totals = {loc.id: 0 for loc in self.locations}
        for (_, lid), iq in self.cells.items():
            totals[lid] += iq.quantity
        return totals

    def off_location_ids(self):
        """全メニュー0の店＝その日は中止。直して0にした店は戻せるよう、予定も0の店だけ。"""
        now, plan = {}, {}
        for (_, lid), iq in self.cells.items():
            now[lid] = now.get(lid, 0) + iq.quantity
            plan[lid] = plan.get(lid, 0) + iq.planned_quantity
        return {loc.id for loc in self.locations if not now.get(loc.id) and not plan.get(loc.id)}

    def get_or_create(self, product_id, location_id):
        iq = self.cells.get((product_id, location_id))
        if iq is None:
            # 振分表が送っていないマス（予定0）。その店は営業しているので現場で足せる
            iq = ItemQuantity.objects.create(
                target_date=self.date.isoformat(), target_week=self.target_week,
                product_id=product_id, sales_location_id=location_id, quantity=0, planned_quantity=0,
            )
            self.cells[(product_id, location_id)] = iq
        return iq


def latest_upload(d):
    return ItemQuantityUpload.objects.filter(target_date=d, ok=True).first()


def screen(d, today, user):
    day = Day(d)
    upload = latest_upload(d)
    off = day.off_location_ids()
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
        "products": [{"id": p.id, "name": p.name} for p in day.products],
        "locations": [
            {"id": loc.id, "name": loc.name, "off": loc.id in off, "submitted": loc.name in submitted}
            for loc in day.locations
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
        "has_data": bool(day.rows),
        "last_upload": upload,
        "history": history,
        "change_count": sum(1 for h in history if h["kind"] == "change"),
        "login_name": user_label(user),
        "grid_data": data,
    }


def _history(d, upload):
    changes = ItemQuantityChange.objects.filter(target_date=d).select_related(
        "product", "sales_location", "changed_by")
    items = [{
        "kind": "change", "at": c.changed_at, "location": c.sales_location.name, "product": c.product.name,
        "before": c.before, "after": c.after, "by": user_label(c.changed_by),
        # 受信より前の修正は再送で上書きされて消えている
        "stale": bool(upload and c.changed_at < upload.received_at),
    } for c in changes]
    items += [{"kind": "upload", "at": u.received_at}
              for u in ItemQuantityUpload.objects.filter(target_date=d, ok=True)]
    items.sort(key=lambda h: h["at"], reverse=True)
    return items


@dataclass
class SaveResult:
    ok: bool = False
    saved: int = 0
    error: str = ""
    reupload: bool = False
    conflicts: list = field(default_factory=list)
    publish: str = ""  # "" / "ok" / "failed"


def _parse_changes(raw):
    """[{product_id, location_id, value, base}] を整数に。おかしければ None。"""
    if not isinstance(raw, list) or not raw:
        return None
    parsed = {}
    for c in raw:
        try:
            pid, lid = int(c["product_id"]), int(c["location_id"])
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
        locations = {loc.id: loc for loc in day.locations}
        open_ids = set(locations) - day.off_location_ids()
        if any(pid not in products or lid not in open_ids for pid, lid in changes):
            result.error = "修正できないマス"
            return result

        for (pid, lid), (value, base) in changes.items():
            current = day.value(pid, lid)
            if current != base:
                result.conflicts.append({
                    "product_id": pid, "location_id": lid, "current": current,
                    "label": f"{locations[lid].name}×{products[pid].name}",
                })
        if result.conflicts:
            return result

        before_totals = day.location_totals()
        for (pid, lid), (value, base) in changes.items():
            if value == base:
                continue
            iq = day.get_or_create(pid, lid)
            iq.quantity = value
            iq.save(update_fields=["quantity"])
            ItemQuantityChange.objects.create(
                target_date=d, product_id=pid, sales_location_id=lid,
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
        _key(pid, lid) for pid, lid in ItemQuantity.objects.filter(
            target_date=d.isoformat(), checked_at__isnull=False,
        ).values_list("product_id", "sales_location_id")
    ]


def save_checks(d, user, raw, today):
    """消し込みの✓を保存する。保存できた件数を返す（None＝受け付けない）。"""
    if d not in editable_dates(today) or not isinstance(raw, list):
        return None
    items = {}
    for c in raw:
        try:
            items[(int(c["product_id"]), int(c["location_id"]))] = bool(c["checked"])
        except (KeyError, TypeError, ValueError):
            return None
    with transaction.atomic():
        day = Day(d, lock=True)
        product_ids = {p.id for p in day.products}
        open_ids = {loc.id for loc in day.locations} - day.off_location_ids()
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
