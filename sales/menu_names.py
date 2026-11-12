"""週の途中のメニュー名の変更（開始日つき）。

Product.name は週の頭の名前のまま持ち、変更は ProductNameChange に開始日つきで持つ。
日計表・受注は「その日の名前」を文字で残すので、実績分析では変更前と変更後が別のメニューとして並ぶ。
（2026-11-12 しょうへい指示：トマトクリームソース→トマトソースの売上を比べたい）
"""
import datetime
from collections import defaultdict

from django.db import transaction

from sales.menu_registry import LARGE_RICE_NO, normalize_name, parse_week, profile_for, week_end, week_state
from sales.models import DailyReportEntry, ProductNameChange


def name_on(product, day, changes=None):
    """その日のメニュー名。day が None なら週の頭の名前。"""
    if day is None:
        return product.name
    if changes is None:
        changes = list(product.name_changes.all())
    current = product.name
    for c in sorted(changes, key=lambda c: c.start_date):
        if c.start_date <= day:
            current = c.name
    return current


def names_on(products, day):
    """{product.id: その日の名前}。一覧で1品ずつ問い合わせないようにまとめて引く。"""
    by_product = defaultdict(list)
    for c in ProductNameChange.objects.filter(product__in=[p.id for p in products]):
        by_product[c.product_id].append(c)
    return {p.id: name_on(p, day, by_product[p.id]) for p in products}


def changes_of_week(products):
    """{product.id: [変更, ...]}（開始日順）。"""
    by_product = defaultdict(list)
    for c in ProductNameChange.objects.filter(product__in=[p.id for p in products]).order_by("start_date"):
        by_product[c.product_id].append(c)
    return dict(by_product)


def _week_of(product):
    return parse_week(product.week)


def validate(product, start, name, today):
    """登録できないときはエラー文を返す。"""
    week = _week_of(product)
    if week is None or product.no == LARGE_RICE_NO or not product.rank_id:
        return "このメニューは名前を変更できません。"
    if week_state(week, today) == "ended":
        return "終了した週は変更できません。"
    if start is None:
        return "開始日を入力してください。"
    if not (week < start <= week_end(week)):
        first = week + datetime.timedelta(days=1)   # 週の初日からなら、Excelを直して再送信する
        return f"開始日は{first.month}/{first.day}〜{week_end(week).month}/{week_end(week).day}の間で入力してください。"
    if not name:
        return "新しいメニュー名を入力してください。"
    if name == name_on(product, start - datetime.timedelta(days=1)):
        return "前日までと同じメニュー名です。"
    return None


@transaction.atomic
def add_change(product, start, name, user, today):
    """名前の変更を登録する。戻り値：(変更, 書き換えた日計表の明細数, 受注の明細数)。エラーは ValueError。"""
    name = normalize_name(name)
    error = validate(product, start, name, today)
    if error:
        raise ValueError(error)
    old_names = _all_names(product)
    change, _ = ProductNameChange.objects.update_or_create(
        product=product, start_date=start,
        defaults={"name": name, "created_by": user if getattr(user, "is_authenticated", False) else None},
    )
    _remember(product, name)
    entries, orders = _resync(product, old_names | {name})
    return change, entries, orders


@transaction.atomic
def remove_change(change, today):
    """変更を取り消す。戻り値：(書き換えた日計表の明細数, 受注の明細数)。"""
    product = change.product
    week = _week_of(product)
    if week is None or week_state(week, today) == "ended":
        raise ValueError("終了した週は変更できません。")
    old_names = _all_names(product)
    change.delete()
    return _resync(product, old_names)


def _all_names(product):
    return {product.name, *product.name_changes.values_list("name", flat=True)}


def _remember(product, name):
    """新しい名前をメニューデータベースに覚えさせる。初めての名前は、この枠の種類・容器で確認済みにする
    （名前だけの変更なので値段の種類は変わらない前提。違う場合はメニューデータベースで直す）。"""
    week = _week_of(product)
    profile = profile_for(name, week)
    if not profile.confirmed and profile.first_seen == week and profile.last_seen == week:
        profile.rank_id, profile.container, profile.confirmed = product.rank_id, product.container_type, True
        profile.save(update_fields=["rank", "container", "confirmed", "updated_at"])


def _resync(product, names):
    """送信済みの日計表・登録済みの受注の名前を、その日の名前にそろえる。"""
    week = _week_of(product)
    changes = list(product.name_changes.all())
    entry_count = 0
    for i in range(7):
        day = week + datetime.timedelta(days=i)
        entry_count += (DailyReportEntry.objects
                        .filter(report__date=day, product_no=product.no, product__in=names)
                        .exclude(product=name_on(product, day, changes))
                        .update(product=name_on(product, day, changes)))
    order_count = 0
    from orders.models import OrderItem   # orders は sales に依存しているので、ここで読む
    days = (OrderItem.objects.filter(product=product, product_name__in=names)
            .values_list("order__delivery_date", flat=True).distinct())
    for day in days:
        new = name_on(product, day, changes)
        order_count += (OrderItem.objects
                        .filter(product=product, order__delivery_date=day, product_name__in=names)
                        .exclude(product_name=new).update(product_name=new))
    return entry_count, order_count
