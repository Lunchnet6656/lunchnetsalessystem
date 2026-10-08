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


def current_rank_prices(today, rank_list):
    """種類ごとの今の値段の文字（例「A 700／B 650／C 600」）。辞書で種類を選ぶときの判断材料。"""
    cells = cells_of(table_in_effect(today))
    return {r.id: "／".join(f"{p} {cells.get((r.id, p), '—')}" for p in PRICE_PATTERNS) for r in rank_list}


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
            form.errors.append("開始日を正しく入力してください。")
    else:
        form.errors.append("開始日を入力してください。")
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
        form.errors.append("値段は1円以上の整数で入力してください。")
    return form


def validate_price_form(form, today, editing=None):
    """入力チェック。form.errors に足していく。比べる相手（開始日の前日まで使う価格表）を返す。"""
    if form.valid_from is None:
        return None
    if form.valid_from < today:
        form.errors.append("開始日に過去の日付は指定できません。過去の値段を変更する場合は開発部に相談してください。")
    duplicate = PriceTable.objects.filter(valid_from=form.valid_from)
    if editing is not None:
        duplicate = duplicate.exclude(pk=editing.pk)
    if duplicate.exists():
        form.errors.append(f"{md(form.valid_from)}から始まる価格表はすでに登録されています。")
    base = table_before(form.valid_from, exclude=editing)
    if not form.cell_errors and base is not None and cells_of(base) == form.cells:
        form.errors.append("現在の価格表と同じ値段です。変更するマスを編集してください。")
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
            where = f"（{counts[pattern]}か所）" if counts[pattern] else "（使用中の販売所なし）"
            name = "大盛りの追加料金" if not rank.is_bento else rank.name
            sign = "" if rank.is_bento else "＋"
            old_text = f"{sign}{old:,}円" if old is not None else "（新規設定）"
            lines.append(f"{name} の 価格{pattern}　{old_text} → {sign}{new:,}円{where}")
            if old and abs(new - old) / old >= BIG_CHANGE_RATIO:
                ratio = new / old
                how = f"現在の{ratio:.0f}倍" if ratio >= 2 else f"現在より{abs(new - old) / old:.0%}{'高い' if new > old else '安い'}"
                warnings.append(f"{name} の 価格{pattern}　{old:,}円 → {new:,}円 は、{how}です。桁を確認してください。")
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
    summary = f"{md(table.valid_from)}からの価格表を取り消しました"
    table.delete()
    log_change(user, "price_table", summary)
    return summary


# ===== 割引設定（ご飯・割引返金） =====
from django.db.models import Q

from sales.models import DiscountItem

GROUP_TITLES = dict(DiscountItem.GROUP_CHOICES)


def yen(item_or_amount, direction=None):
    if isinstance(item_or_amount, DiscountItem):
        amount, direction = item_or_amount.amount, item_or_amount.direction
    else:
        amount = item_or_amount
    return f"▲{amount:,}円" if direction == "minus" else f"＋{amount:,}円"


def _successor(item):
    """金額を変える予定で作った、次の項目（同じグループ・同じ名前で、終了日の翌日から始まる）。"""
    if item.valid_to is None:
        return None
    return DiscountItem.objects.filter(group=item.group, label=item.label,
                                       valid_from=item.valid_to + datetime.timedelta(days=1)).first()


def _predecessor(item):
    return DiscountItem.objects.filter(group=item.group, label=item.label,
                                       valid_to=item.valid_from - datetime.timedelta(days=1)).first()


def discount_overview(today):
    """一覧の中身：グループごとの今の項目と予定、終わった項目、これから始まる新しい項目。"""
    items = list(DiscountItem.objects.all())
    current = [i for i in items if i.valid_from <= today and (i.valid_to is None or i.valid_to >= today)]
    groups = []
    for group, title in DiscountItem.GROUP_CHOICES:
        rows = []
        for item in sorted((i for i in current if i.group == group), key=lambda i: (i.sort_order, i.id)):
            successor = _successor(item)
            if successor:
                plan = f"{md(successor.valid_from)}から {yen(successor)}"
                plan_item = successor
            elif item.valid_to is not None:
                plan = f"{md(item.valid_to)}で終了"
                plan_item = item
            else:
                plan, plan_item = "", None
            rows.append({"item": item, "plan": plan, "plan_item": plan_item})
        upcoming = [i for i in items if i.group == group and i.valid_from > today and _predecessor(i) is None]
        groups.append({"group": group, "title": title, "rows": rows, "upcoming": upcoming})
    ended = [i for i in items if i.valid_to is not None and i.valid_to < today]
    return groups, sorted(ended, key=lambda i: i.valid_to, reverse=True)


def _read_date(text, errors, label="開始日"):
    text = (text or "").strip()
    if not text:
        errors.append(f"{label}を入力してください。")
        return None
    try:
        return datetime.date.fromisoformat(text)
    except ValueError:
        errors.append(f"{label}を正しく入力してください。")
        return None


def _read_amount(text, errors):
    text = (text or "").strip()
    if text.isdigit() and int(text) >= 1:
        return int(text)
    errors.append("金額は1円以上の整数で入力してください。")
    return None


def plan_change(item, post, today):
    """「金額を変える」の入力チェックと確認の文章。戻り値：(errors, plan)"""
    errors = []
    amount = _read_amount(post.get("amount"), errors)
    start = _read_date(post.get("valid_from"), errors)
    if start and start < today:
        errors.append("開始日に過去の日付は指定できません。過去の値段を変更する場合は開発部に相談してください。")
    if start and start <= item.valid_from:
        errors.append(f"開始日は {md(item.valid_from)} より後の日付を指定してください。")
    if _successor(item) or item.valid_to is not None:
        errors.append("この項目にはすでに予定があります。先に［予定取消］してください。")
    if amount is not None and amount == item.amount:
        errors.append("現在と同じ金額です。変更する金額を入力してください。")
    if errors:
        return errors, None
    sentence = (f"{md(start)}から「{item.label}」は {yen(item)} → {yen(amount, item.direction)} に変更されます。"
                f"{md(start - datetime.timedelta(days=1))}までは {yen(item)} が適用されます。")
    return [], {"action": "change", "item": item, "amount": amount, "valid_from": start, "sentence": sentence,
                "summary": f"「{item.label}」の金額変更を登録しました（{md(start)}から {yen(item)} → {yen(amount, item.direction)}）"}


def plan_add(post, today):
    errors = []
    group = post.get("group")
    if group not in GROUP_TITLES:
        errors.append("グループを選択してください。")
    label = (post.get("label") or "").strip()[:50]
    if not label:
        errors.append("項目名を入力してください。")
    direction = post.get("direction")
    if direction not in ("minus", "plus"):
        errors.append("引く／足すを選択してください。")
    amount = _read_amount(post.get("amount"), errors)
    start = _read_date(post.get("valid_from"), errors)
    if start and start < today:
        errors.append("開始日に過去の日付は指定できません。過去の値段を変更する場合は開発部に相談してください。")
    still_used = Q(valid_to__isnull=True) | Q(valid_to__gte=start or today)
    if group in GROUP_TITLES and label and DiscountItem.objects.filter(group=group, label=label).filter(
            still_used).exists():
        errors.append(f"『{GROUP_TITLES[group]}』にはすでに『{label}』があります。別の項目名を入力してください。")
    if errors:
        return errors, None
    sentence = (f"{md(start)}から、日計表の『{GROUP_TITLES[group]}』に『{label}（{yen(amount, direction)}）』の欄が追加されます。"
                f"日計表送信データ（CSV）の末尾に『{label}』の列が追加されます。")
    return [], {"action": "add", "group": group, "label": label, "direction": direction, "amount": amount,
                "valid_from": start, "sentence": sentence,
                "summary": f"項目「{label}（{yen(amount, direction)}）」を追加しました（{GROUP_TITLES[group]}・{md(start)}から）"}


def plan_end(item, post, today):
    errors = []
    last_day = _read_date(post.get("valid_to"), errors, label="終了日")
    if last_day and last_day < today:
        errors.append("終了日に過去の日付は指定できません。")
    if last_day and last_day < item.valid_from:
        errors.append(f"終了日は {md(item.valid_from)} 以降の日付を指定してください。")
    if item.valid_to is not None:
        errors.append("この項目にはすでに予定があります。先に［予定取消］してください。")
    if errors:
        return errors, None
    sentence = (f"「{item.label}」の欄は {md(last_day)} の日計表まで表示されます。"
                f"{md(last_day + datetime.timedelta(days=1))}から表示されなくなります。それより前の日計表は変更されません。")
    return [], {"action": "end", "item": item, "valid_to": last_day, "sentence": sentence,
                "summary": f"「{item.label}」の終了を登録しました（{md(last_day)}まで）"}


def _csv_label_for(group, label):
    if DiscountItem.objects.filter(csv_label=label).exclude(group=group).exists():
        return f"{GROUP_TITLES[group]}{label}"
    return label


@transaction.atomic
def apply_plan(user, plan):
    by = user if user.is_authenticated else None
    if plan["action"] == "change":
        old = plan["item"]
        old.valid_to = plan["valid_from"] - datetime.timedelta(days=1)
        old.updated_by = by
        old.save()
        # CSVの列と旧カラムは前の項目から引き継ぐ（金額を変えるたびに列が増えないように）
        DiscountItem.objects.create(
            group=old.group, label=old.label, direction=old.direction, amount=plan["amount"],
            sort_order=old.sort_order, valid_from=plan["valid_from"], legacy_field=old.legacy_field,
            csv_label=old.csv_label, updated_by=by,
        )
    elif plan["action"] == "add":
        last = DiscountItem.objects.filter(group=plan["group"]).order_by("-sort_order").first()
        DiscountItem.objects.create(
            group=plan["group"], label=plan["label"], direction=plan["direction"], amount=plan["amount"],
            sort_order=(last.sort_order + 1) if last else 1, valid_from=plan["valid_from"],
            csv_label=_csv_label_for(plan["group"], plan["label"]), updated_by=by,
        )
    elif plan["action"] == "end":
        item = plan["item"]
        item.valid_to, item.updated_by = plan["valid_to"], by
        item.save()
    log_change(user, "discount", plan["summary"])


@transaction.atomic
def cancel_plan(user, item, today):
    """予定の取り消し。金額変更の予定（これから始まる項目）は消して前の項目を戻す。終了予定は終了日を外す。"""
    if item.valid_from > today:
        predecessor = _predecessor(item)
        summary = (f"「{item.label}」の金額変更（{md(item.valid_from)}から {yen(item)}）を取り消しました" if predecessor
                   else f"「{item.label}」の追加予定（{md(item.valid_from)}から）を取り消しました")
        item.delete()
        if predecessor:
            predecessor.valid_to = None
            predecessor.save()
    elif item.valid_to is not None and item.valid_to >= today:
        summary = f"「{item.label}」の終了予定（{md(item.valid_to)}まで）を取り消しました"
        item.valid_to = None
        item.save()
    else:
        return None
    log_change(user, "discount", summary)
    return summary


@transaction.atomic
def move_item(item, step, today):
    """並び順を1つ上（-1）か下（+1）へ。金額変更の予定の項目も同じ並びにする。"""
    siblings = sorted((i for i in DiscountItem.objects.filter(group=item.group)
                       if i.valid_to is None or i.valid_to >= today), key=lambda i: (i.sort_order, i.id))
    names = []
    for i in siblings:
        if i.label not in names:
            names.append(i.label)
    pos = names.index(item.label)
    target = pos + step
    if not 0 <= target < len(names):
        return
    names[pos], names[target] = names[target], names[pos]
    for order, label in enumerate(names, start=1):
        DiscountItem.objects.filter(group=item.group, label=label).update(sort_order=order)
