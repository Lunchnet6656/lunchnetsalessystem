"""価格表・割引設定の画面の裏側（確認画面の文章づくり・登録・変更履歴）。ビューからはここを呼ぶ。"""
from sales.models import PriceChangeLog


def user_label(user):
    if user is None:
        return "（不明）"
    name = f"{user.last_name} {user.first_name}".strip()
    return name or user.username


def log_change(user, kind, summary):
    return PriceChangeLog.objects.create(kind=kind, summary=summary,
                                         user=user if getattr(user, "is_authenticated", False) else None)


def recent_changes(kind, limit=20):
    qs = PriceChangeLog.objects.filter(kind=kind).select_related("user")
    logs = list(qs[:limit]) if limit else list(qs)
    for log in logs:
        log.user_label = user_label(log.user)
    return logs


# ===== 価格表 =====
import datetime
from dataclasses import dataclass, field

from django.db import transaction

from sales.models import PRICE_PATTERNS, PriceRank, PriceTable, PriceTableCell, SalesLocation
from sales.pricing import pattern_of

WEEKDAYS_JA = "月火水木金土日"
BIG_CHANGE_RATIO = 0.3   # これ以上変わるマスは桁間違いの可能性があるので警告（要件§16）
MENU_WEEK_START = 2      # メニューの週は水曜はじまり（Product.week）


def md(day):
    """「11/1（日）」"""
    return f"{day.month}/{day.day}（{WEEKDAYS_JA[day.weekday()]}）"


def ranks():
    return list(PriceRank.objects.all())


def cells_of(table):
    if table is None:
        return {}
    return {(c.rank_id, c.pattern): c.price for c in table.cells.all()}


def table_in_effect(on_date, exclude=None):
    """その日に使う価格表（その日までに始まっている一番新しいもの）。"""
    qs = PriceTable.objects.filter(valid_from__lte=on_date).order_by("-valid_from").prefetch_related("cells")
    if exclude is not None:
        qs = qs.exclude(pk=exclude.pk)
    return qs.first()


def table_before(on_date, exclude=None):
    """その日の前日まで使われる価格表（新しい価格表の比べる相手）。"""
    return table_in_effect(on_date - datetime.timedelta(days=1), exclude=exclude)


def pending_tables(today):
    return list(PriceTable.objects.filter(valid_from__gt=today).order_by("valid_from").prefetch_related("cells"))


def past_tables(today):
    """今の価格表より前の価格表（新しい順・終了日つき）。"""
    started = list(PriceTable.objects.filter(valid_from__lte=today).order_by("-valid_from").prefetch_related("cells"))
    rank_list = ranks()
    rows = []
    for i, table in enumerate(started):
        newer = started[i - 1] if i else None
        cells = cells_of(table)
        rows.append({"table": table,
                     "by_rank": ["/".join(str(cells.get((r.id, p), "—")) for p in PRICE_PATTERNS) for r in rank_list],
                     "valid_to": newer.valid_from - datetime.timedelta(days=1) if newer else None})
    return rows


def locations_by_pattern():
    """価格パターンごとの販売所名（価格表の列見出しの「35か所」に使う）。"""
    result = {p: [] for p in PRICE_PATTERNS}
    for loc in SalesLocation.objects.order_by("no"):
        pattern = pattern_of(loc)
        if pattern:
            result[pattern].append(loc.name)
    return result


def matrix(rank_list, cells, base=None):
    """テンプレート用：行ごとに [{pattern, price, base, changed}] を並べる。"""
    rows = []
    for rank in rank_list:
        row = []
        for pattern in PRICE_PATTERNS:
            price = cells.get((rank.id, pattern))
            before = (base or {}).get((rank.id, pattern))
            row.append({"pattern": pattern, "price": price, "base": before,
                        "changed": base is not None and before is not None and price != before,
                        "name": f"cell_{rank.id}_{pattern}"})
        rows.append({"rank": rank, "cells": row})
    return rows


@dataclass
class PriceForm:
    valid_from: object = None
    note: str = ""
    cells: dict = field(default_factory=dict)
    errors: list = field(default_factory=list)
    cell_errors: set = field(default_factory=set)


def read_price_form(post, rank_list):
    form = PriceForm(note=(post.get("note") or "").strip()[:100])
    raw_date = (post.get("valid_from") or "").strip()
    if raw_date:
        try:
            form.valid_from = datetime.date.fromisoformat(raw_date)
        except ValueError:
            form.errors.append("開始日を正しく入れてください。")
    else:
        form.errors.append("開始日を入れてください。")
    for rank in rank_list:
        for pattern in PRICE_PATTERNS:
            name = f"cell_{rank.id}_{pattern}"
            text = (post.get(name) or "").strip()
            if text.isdigit() and int(text) >= 1:
                form.cells[(rank.id, pattern)] = int(text)
            else:
                form.cell_errors.add(name)
                form.cells[(rank.id, pattern)] = text
    if form.cell_errors:
        form.errors.append("値段は1円以上の整数で入れてください。")
    return form


def validate_price_form(form, today, editing=None):
    """入力チェック。form.errors に足していく。比べる相手（開始日の前日まで使う価格表）を返す。"""
    if form.valid_from is None:
        return None
    if form.valid_from < today:
        form.errors.append("開始日に過去の日付は選べません。過去の値段を直すときは開発部に相談してください。")
    duplicate = PriceTable.objects.filter(valid_from=form.valid_from)
    if editing is not None:
        duplicate = duplicate.exclude(pk=editing.pk)
    if duplicate.exists():
        form.errors.append(f"{md(form.valid_from)}から始まる価格表はもうあります。")
    base = table_before(form.valid_from, exclude=editing)
    if not form.cell_errors and base is not None and cells_of(base) == form.cells:
        form.errors.append("今の価格表と同じ値段です。変えるマスを直してください。")
    return base


def _bento_tiers(rank_list, cells, pattern):
    prices = {cells.get((r.id, pattern)) for r in rank_list if r.is_bento}
    return sorted((p for p in prices if isinstance(p, int) and p > 0), reverse=True)


def confirmation(form, base, today, rank_list=None):
    """確認画面の文章。サーバーで作る（画面の表示と登録の判定を必ず一致させるため）。"""
    rank_list = rank_list or ranks()
    base_cells = cells_of(base)
    counts = {p: len(names) for p, names in locations_by_pattern().items()}
    lines, warnings = [], []
    unchanged = 0
    for rank in rank_list:
        for pattern in PRICE_PATTERNS:
            new = form.cells[(rank.id, pattern)]
            old = base_cells.get((rank.id, pattern))
            if old == new:
                unchanged += 1
                continue
            where = f"（{counts[pattern]}か所）" if counts[pattern] else "（今は使っている販売所がありません）"
            name = "大盛りの追加料金" if not rank.is_bento else rank.name
            sign = "" if rank.is_bento else "＋"
            old_text = f"{sign}{old:,}円" if old is not None else "（新しく設定）"
            lines.append(f"{name} の 価格{pattern}　{old_text} → {sign}{new:,}円{where}")
            if old and abs(new - old) / old >= BIG_CHANGE_RATIO:
                ratio = new / old
                how = f"今の{ratio:.0f}倍" if ratio >= 2 else f"今より{abs(new - old) / old:.0%}{'高い' if new > old else '安い'}"
                warnings.append(f"{name} の 価格{pattern}　{old:,}円 → {new:,}円 は、{how}です。桁を確かめてください。")
    coupons = []
    for pattern in PRICE_PATTERNS:
        before, after = _bento_tiers(rank_list, base_cells, pattern), _bento_tiers(rank_list, form.cells, pattern)
        if before != after and counts[pattern]:
            coupons.append(f"価格{pattern}の販売所　{'/'.join(map(str, before)) or 'なし'}円 → {'/'.join(map(str, after))}円")
    start = form.valid_from
    first = lines[0].rsplit("（", 1)[0] if lines else ""
    more = f" ほか{len(lines) - 1}マス" if len(lines) > 1 else ""
    return {
        "start": md(start),
        "until": md(start - datetime.timedelta(days=1)) if base else None,
        "lines": lines,
        "unchanged": unchanged,
        "warnings": warnings,
        "coupons": coupons,
        "mid_week": start.weekday() != MENU_WEEK_START,
        "week_start": md(start - datetime.timedelta(days=(start.weekday() - MENU_WEEK_START) % 7)),
        "is_today": start == today,
        "summary": f"{md(start)}からの価格表（{first}{more}）" if lines else f"{md(start)}からの価格表",
    }


@transaction.atomic
def register_table(user, form, editing=None):
    """確認画面の［登録する］。直すときは開始待ちの価格表を上書きする。"""
    if editing is not None:
        table = editing
        table.valid_from, table.note = form.valid_from, form.note
        table.save()
        table.cells.all().delete()
    else:
        table = PriceTable.objects.create(valid_from=form.valid_from, note=form.note,
                                          created_by=user if user.is_authenticated else None)
    PriceTableCell.objects.bulk_create([
        PriceTableCell(table=table, rank_id=rank_id, pattern=pattern, price=price)
        for (rank_id, pattern), price in form.cells.items()
    ])
    return table


@transaction.atomic
def cancel_table(user, table):
    summary = f"{md(table.valid_from)}からの価格表を取り消し"
    table.delete()
    log_change(user, "price_table", summary)
    return summary
