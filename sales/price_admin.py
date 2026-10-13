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
from collections import namedtuple
from dataclasses import dataclass, field

from django.db import transaction

from sales.models import PricePattern, PriceRank, PriceTable, PriceTableCell, SalesLocation
from sales.pricing import CUSTOMER_COLUMN, pattern_of

WEEKDAYS_JA = "月火水木金土日"
BIG_CHANGE_RATIO = 0.3   # これ以上変わるマスは桁間違いの可能性があるので警告（要件§16）
MENU_WEEK_START = 2      # メニューの週は水曜はじまり（Product.week）

# 価格表の列。販売所用は価格パターン、顧客別は「値段」の1列だけ（code は ""）。
Column = namedtuple("Column", "code name")
CUSTOMER_COLUMNS = [Column(CUSTOMER_COLUMN, "値段")]


def md(day):
    """「11/1（日）」"""
    return f"{day.month}/{day.day}（{WEEKDAYS_JA[day.weekday()]}）"


def ranks(*cell_maps):
    """価格表の行：使用中の値段の種類＋渡した価格表にマスがある終了済みの種類（過去の表を崩さないため）。"""
    used = {rank_id for cells in cell_maps for (rank_id, _) in cells}
    return [r for r in PriceRank.objects.all() if r.active or r.id in used]


def columns(*cell_maps):
    """販売所用の価格表の列：使用中の価格パターン＋渡した価格表にマスがある終了済みのパターン。"""
    used = {code for cells in cell_maps for (_, code) in cells}
    return [Column(p.code, p.name) for p in PricePattern.objects.all() if p.active or p.code in used]


def columns_for(customer, *cell_maps):
    return CUSTOMER_COLUMNS if customer is not None else columns(*cell_maps)


def current_rank_prices(today, rank_list):
    """種類ごとの今の値段の文字（例「A 700／B 650／C 600」）。辞書で種類を選ぶときの判断材料。"""
    cells = cells_of(table_in_effect(today))
    cols = columns(cells)
    return {r.id: "／".join(f"{c.code} {cells.get((r.id, c.code), '—')}" for c in cols) for r in rank_list}


def cells_of(table):
    if table is None:
        return {}
    return {(c.rank_id, c.pattern): c.price for c in table.cells.all()}


def _tables(customer):
    qs = PriceTable.objects.prefetch_related("cells")
    return qs.filter(customer=customer) if customer is not None else qs.filter(customer__isnull=True)


def table_in_effect(on_date, exclude=None, customer=None):
    """その日に使う価格表（その日までに始まっている一番新しいもの）。customer なしは販売所用。"""
    qs = _tables(customer).filter(valid_from__lte=on_date).order_by("-valid_from")
    if exclude is not None:
        qs = qs.exclude(pk=exclude.pk)
    return qs.first()


def table_before(on_date, exclude=None, customer=None):
    """その日の前日まで使われる価格表（新しい価格表の比べる相手）。"""
    return table_in_effect(on_date - datetime.timedelta(days=1), exclude=exclude, customer=customer)


def pending_tables(today, customer=None):
    return list(_tables(customer).filter(valid_from__gt=today).order_by("valid_from"))


def past_tables(today, customer=None):
    """今の価格表より前の価格表（新しい順・終了日つき）。"""
    started = list(_tables(customer).filter(valid_from__lte=today).order_by("-valid_from"))
    all_cells = [cells_of(t) for t in started]
    rank_list = ranks(*all_cells)
    rows = []
    for i, table in enumerate(started):
        newer = started[i - 1] if i else None
        cells = all_cells[i]
        cols = columns_for(customer, cells)
        rows.append({"table": table,
                     "cols_label": "/".join(c.code for c in cols),
                     "by_rank": ["/".join(str(cells.get((r.id, c.code), "—")) for c in cols) for r in rank_list],
                     "valid_to": newer.valid_from - datetime.timedelta(days=1) if newer else None})
    return {"rows": rows, "ranks": rank_list}


def locations_by_pattern(cols=None):
    """価格パターンごとの販売所名（価格表の列見出しの「35か所」に使う）。"""
    cols = cols if cols is not None else columns()
    codes = {c.code for c in cols}
    result = {c.code: [] for c in cols}
    for loc in SalesLocation.objects.order_by("no"):
        pattern = pattern_of(loc, codes=codes)
        if pattern:
            result[pattern].append(loc.name)
    return result


def column_heads(cols, customer=None):
    """テンプレート用：列見出し（名前と販売所の数）。顧客別は数を出さない。"""
    if customer is not None:
        return [{"code": c.code, "name": c.name, "names": None} for c in cols]
    where = locations_by_pattern(cols)
    return [{"code": c.code, "name": c.name, "names": where[c.code]} for c in cols]


def matrix(rank_list, cells, base=None, cols=None):
    """テンプレート用：行ごとに [{pattern, price, base, changed}] を並べる。"""
    cols = cols if cols is not None else columns(cells, base or {})
    rows = []
    for rank in rank_list:
        row = []
        for col in cols:
            price = cells.get((rank.id, col.code))
            before = (base or {}).get((rank.id, col.code))
            row.append({"pattern": col.code, "price": price, "base": before,
                        "changed": base is not None and before is not None and price != before,
                        "name": f"cell_{rank.id}_{col.code}"})
        rows.append({"rank": rank, "cells": row})
    return rows


@dataclass
class PriceForm:
    valid_from: object = None
    note: str = ""
    cells: dict = field(default_factory=dict)
    errors: list = field(default_factory=list)
    cell_errors: set = field(default_factory=set)


def read_price_form(post, rank_list, cols):
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
        for col in cols:
            name = f"cell_{rank.id}_{col.code}"
            text = (post.get(name) or "").strip()
            if text.isdigit() and int(text) >= 1:
                form.cells[(rank.id, col.code)] = int(text)
            else:
                form.cell_errors.add(name)
                form.cells[(rank.id, col.code)] = text
    if form.cell_errors:
        form.errors.append("値段は1円以上の整数で入力してください。")
    return form


def validate_price_form(form, today, editing=None, customer=None):
    """入力チェック。form.errors に足していく。比べる相手（開始日の前日まで使う価格表）を返す。"""
    if form.valid_from is None:
        return None
    if form.valid_from < today:
        form.errors.append("開始日に過去の日付は指定できません。過去の値段を変更する場合は開発部に相談してください。")
    duplicate = _tables(customer).filter(valid_from=form.valid_from)
    if editing is not None:
        duplicate = duplicate.exclude(pk=editing.pk)
    if duplicate.exists():
        form.errors.append(f"{md(form.valid_from)}から始まる価格表はすでに登録されています。")
    base = table_before(form.valid_from, exclude=editing, customer=customer)
    if not form.cell_errors and base is not None and cells_of(base) == form.cells:
        form.errors.append("現在の価格表と同じ値段です。変更するマスを編集してください。")
    return base


def _bento_tiers(rank_list, cells, pattern):
    prices = {cells.get((r.id, pattern)) for r in rank_list if r.is_bento}
    return sorted((p for p in prices if isinstance(p, int) and p > 0), reverse=True)


def customers_missing(rank_list, on_date):
    """その日の顧客別価格表で、値段が入っていない種類がある独自価格の顧客 → [(顧客, [種類名])]。"""
    from orders.models import Customer
    from sales.pricing import CUSTOM
    result = []
    for customer in Customer.objects.filter(is_active=True, price_type=CUSTOM).order_by("company_name", "name"):
        cells = cells_of(table_in_effect(on_date, customer=customer))
        missing = [r.name for r in rank_list if (r.id, CUSTOMER_COLUMN) not in cells]
        if missing:
            result.append((customer, missing))
    return result


def _cell_label(rank, col, customer):
    name = "大盛りの追加料金" if not rank.is_bento else rank.name
    return name if customer is not None else f"{name} の {col.name}"


def confirmation(form, base, today, rank_list, cols, customer=None):
    """確認画面の文章。サーバーで作る（画面の表示と登録の判定を必ず一致させるため）。"""
    base_cells = cells_of(base)
    counts = {} if customer is not None else {c: len(n) for c, n in locations_by_pattern(cols).items()}
    lines, warnings = [], []
    unchanged = 0
    for rank in rank_list:
        for col in cols:
            new = form.cells[(rank.id, col.code)]
            old = base_cells.get((rank.id, col.code))
            if old == new:
                unchanged += 1
                continue
            label = _cell_label(rank, col, customer)
            if customer is not None:
                where = ""
            else:
                where = f"（{counts[col.code]}か所）" if counts[col.code] else "（使用中の販売所なし）"
            sign = "" if rank.is_bento else "＋"
            old_text = f"{sign}{old:,}円" if old is not None else "（新規設定）"
            lines.append(f"{label}　{old_text} → {sign}{new:,}円{where}")
            if old and abs(new - old) / old >= BIG_CHANGE_RATIO:
                ratio = new / old
                how = f"現在の{ratio:.0f}倍" if ratio >= 2 else f"現在より{abs(new - old) / old:.0%}{'高い' if new > old else '安い'}"
                warnings.append(f"{label}　{old:,}円 → {new:,}円 は、{how}です。桁を確認してください。")
    coupons, missing = [], []
    if customer is None:
        for col in cols:
            before = _bento_tiers(rank_list, base_cells, col.code)
            after = _bento_tiers(rank_list, form.cells, col.code)
            if before != after and counts[col.code]:
                coupons.append(f"{col.name}の販売所　{'/'.join(map(str, before)) or 'なし'}円 → {'/'.join(map(str, after))}円")
        # 新しく値段を入れた種類は、独自価格の顧客にも値段が要る（販売所の値上げは止めず、知らせるだけ）
        new_ranks = [r for r in rank_list if not any((r.id, c.code) in base_cells for c in cols)] if base else []
        if new_ranks:
            missing = [(c, names) for c, names in customers_missing(new_ranks, form.valid_from)]
    start = form.valid_from
    first = lines[0].rsplit("（", 1)[0] if lines and customer is None else (lines[0] if lines else "")
    more = f" ほか{len(lines) - 1}マス" if len(lines) > 1 else ""
    who = f"{customer}の" if customer is not None else ""
    return {
        "start": md(start),
        "until": md(start - datetime.timedelta(days=1)) if base else None,
        "lines": lines,
        "unchanged": unchanged,
        "warnings": warnings,
        "coupons": coupons,
        "customers_missing": missing,
        "mid_week": customer is None and start.weekday() != MENU_WEEK_START,
        "week_start": md(start - datetime.timedelta(days=(start.weekday() - MENU_WEEK_START) % 7)),
        "is_today": start == today,
        "summary": f"{md(start)}からの{who}価格表（{first}{more}）" if lines else f"{md(start)}からの{who}価格表",
    }


@transaction.atomic
def register_table(user, form, editing=None, customer=None):
    """確認画面の［登録］。編集するときは開始前の価格表を上書きする。"""
    if editing is not None:
        table = editing
        table.valid_from, table.note = form.valid_from, form.note
        table.save()
        table.cells.all().delete()
    else:
        table = PriceTable.objects.create(valid_from=form.valid_from, note=form.note, customer=customer,
                                          created_by=user if user.is_authenticated else None)
    PriceTableCell.objects.bulk_create([
        PriceTableCell(table=table, rank_id=rank_id, pattern=pattern, price=price)
        for (rank_id, pattern), price in form.cells.items()
    ])
    return table


@transaction.atomic
def cancel_table(user, table):
    who = f"{table.customer}の" if table.customer_id else ""
    summary = f"{md(table.valid_from)}からの{who}価格表を取り消しました"
    kind = "customer_price" if table.customer_id else "price_table"
    table.delete()
    log_change(user, kind, summary)
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


# ===== 値段の種類・価格パターン（価格の一元化 §5.5・§5.6） =====
import string

from sales.models import ClassifyRule, MenuProfile, Product


def _name_error(name, model, exclude=None, label="名前"):
    if not name:
        return f"{label}を入力してください。"
    qs = model.objects.filter(name=name)
    if exclude is not None:
        qs = qs.exclude(pk=exclude.pk)
    if qs.exists():
        return f"『{name}』はすでに登録されています。別の{label}を入力してください。"
    return None


def _move(model, obj, step):
    """使用中のものの中で、並び順を1つ上（-1）か下（+1）へ。"""
    items = list(model.objects.filter(active=True))
    if obj not in items:
        return
    pos = items.index(obj)
    target = pos + step
    if not 0 <= target < len(items):
        return
    items[pos], items[target] = items[target], items[pos]
    for order, item in enumerate(items, start=1):
        if item.sort_order != order:
            item.sort_order = order
            item.save(update_fields=["sort_order"])


def _next_sort(model):
    last = model.objects.order_by("-sort_order").first()
    return (last.sort_order + 1) if last else 1


def live_menu_count(rank, today):
    """開始前・販売中の週のメニューで、その種類のもの（週は水曜はじまり・火曜まで）。"""
    from sales.pricing import _to_date
    weeks = set(Product.objects.filter(rank=rank).values_list("week", flat=True))
    oldest = today - datetime.timedelta(days=6)
    count = 0
    for week in weeks:
        try:
            if _to_date(week) >= oldest:
                count += Product.objects.filter(rank=rank, week=week).count()
        except ValueError:
            continue
    return count


def rank_rows(today):
    rows = []
    for rank in PriceRank.objects.all():
        rows.append({"rank": rank,
                     "menus": MenuProfile.objects.filter(rank=rank).count(),
                     "can_end": rank.active and not rank.role})
    return [r for r in rows if r["rank"].active], [r for r in rows if not r["rank"].active]


def rank_end_blockers(rank, today):
    """終了できない理由（なければ空）。"""
    if rank.role:
        return ["基本の種類のため終了できません。"]
    reasons = []
    profiles = MenuProfile.objects.filter(rank=rank).count()
    if profiles:
        reasons.append(f"メニューデータベースに『{rank.name}』のメニューが{profiles}件あります。別の種類に変更してください。")
    rules = ClassifyRule.objects.filter(rank=rank).count()
    if rules:
        reasons.append(f"判定ルールに『{rank.name}』のルールが{rules}件あります。削除または変更してください。")
    live = live_menu_count(rank, today)
    if live:
        reasons.append(f"開始前・販売中の週のメニューに『{rank.name}』が{live}品あります。［週のメニュー確認］で変更してください。")
    return reasons


@transaction.atomic
def add_rank(user, name, is_bento):
    rank = PriceRank.objects.create(name=name, is_bento=is_bento, sort_order=_next_sort(PriceRank))
    log_change(user, "price_master", f"値段の種類『{name}』を追加しました")
    return rank


@transaction.atomic
def rename(user, obj, name, what):
    old = obj.name
    obj.name = name
    obj.save(update_fields=["name"])
    log_change(user, "price_master", f"{what}『{old}』の名前を『{name}』に変更しました")


@transaction.atomic
def end(user, obj, what):
    obj.active = False
    obj.save(update_fields=["active"])
    log_change(user, "price_master", f"{what}『{obj.name}』を終了しました")


def move_rank(rank, step):
    _move(PriceRank, rank, step)


def pattern_usage(pattern):
    from orders.models import Customer
    locations = [loc for loc in SalesLocation.objects.all()
                 if (loc.price_type or "").upper().strip() == pattern.code]
    customers = Customer.objects.filter(is_active=True, price_type=pattern.code).count()
    return len(locations), customers


def pattern_rows():
    rows = []
    for pattern in PricePattern.objects.all():
        locations, customers = pattern_usage(pattern)
        rows.append({"pattern": pattern, "locations": locations, "customers": customers})
    return [r for r in rows if r["pattern"].active], [r for r in rows if not r["pattern"].active]


def pattern_end_blockers(pattern):
    locations, customers = pattern_usage(pattern)
    if locations or customers:
        return [f"『{pattern.name}』を使用中の販売所が{locations}か所、顧客が{customers}件あります。先に価格パターンを変更してください。"]
    return []


def _next_code():
    used = set(PricePattern.objects.values_list("code", flat=True))
    for letter in string.ascii_uppercase:
        if letter not in used:
            return letter
    n = 1
    while f"P{n}" in used:
        n += 1
    return f"P{n}"


@transaction.atomic
def add_pattern(user, name):
    pattern = PricePattern.objects.create(code=_next_code(), name=name, sort_order=_next_sort(PricePattern))
    log_change(user, "price_master", f"価格パターン『{name}』を追加しました")
    return pattern


def move_pattern(pattern, step):
    _move(PricePattern, pattern, step)


def assignable_patterns(today):
    """販売所・顧客に設定できる価格パターン＝今日の販売所用価格表に値段がある、使用中のパターン。
    選択肢に値段を添えるため、各パターンに hint（例「750/700/650/＋50」）を付けて返す。"""
    cells = cells_of(table_in_effect(today))
    codes = {code for (_, code) in cells}
    rank_list = ranks()
    result = []
    for p in PricePattern.objects.filter(active=True):
        if p.code not in codes:
            continue
        prices = [cells.get((r.id, p.code)) for r in rank_list]
        p.hint = "/".join(("" if r.is_bento else "＋") + str(v) for r, v in zip(rank_list, prices) if v is not None)
        p.label = f"{p.name}（{p.hint}）" if p.hint else p.name
        result.append(p)
    return result


# ===== 顧客別の価格（価格の一元化 §5.3） =====

def customer_overview(today):
    """顧客別タブの中身：独自価格の顧客ごとに、今日の値段（使用中の種類ごと）と次の変更。"""
    from orders.models import Customer
    from sales.pricing import CUSTOM
    rank_list = ranks()
    rows = []
    for customer in Customer.objects.filter(is_active=True, price_type=CUSTOM).order_by("company_name", "name"):
        current = table_in_effect(today, customer=customer)
        cells = cells_of(current)
        pending = pending_tables(today, customer)
        nearest = pending[0] if pending else None
        changed = 0
        if nearest:
            before = cells_of(table_before(nearest.valid_from, customer=customer))
            changed = sum(1 for k, v in cells_of(nearest).items() if before.get(k) != v)
        prices = [{"rank": r, "price": cells.get((r.id, CUSTOMER_COLUMN))} for r in rank_list]
        rows.append({"customer": customer, "current": current, "prices": prices,
                     "missing": any(p["price"] is None for p in prices),
                     "nearest": nearest, "changed": changed, "more": len(pending) - 1 if pending else 0})
    rows.sort(key=lambda r: (r["nearest"] is None, r["nearest"].valid_from if r["nearest"] else today))
    return rank_list, rows


# 顧客を登録するときに入れる最初の値段。過去の納品日の注文を後から入れても値段が出るよう、古い日付から始める
INITIAL_CUSTOMER_PRICES_FROM = datetime.date(2020, 1, 1)


@transaction.atomic
def create_initial_customer_table(user, customer, prices):
    """顧客の登録・編集画面で入れた最初の値段から、顧客別価格表を作る。prices = {rank_id: 値段}"""
    table = PriceTable.objects.create(customer=customer, valid_from=INITIAL_CUSTOMER_PRICES_FROM,
                                      note="顧客の登録画面で設定",
                                      created_by=user if getattr(user, "is_authenticated", False) else None)
    PriceTableCell.objects.bulk_create([
        PriceTableCell(table=table, rank_id=rank_id, pattern=CUSTOMER_COLUMN, price=price)
        for rank_id, price in prices.items()
    ])
    names = dict(PriceRank.objects.values_list("id", "name"))
    detail = "・".join(f"{names[r]} {p:,}円" for r, p in prices.items())
    log_change(user, "customer_price", f"{customer}の最初の値段を登録しました（{detail}）")
    return table
