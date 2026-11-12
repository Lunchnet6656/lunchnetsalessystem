"""持参数xlsx（振分表マクロ「データ変換」の出力）の読み取り・検証・保存。

画面アップロード（upload_view）と自動送信API（api_item_quantity）で共用する。
形式：シート名=対象日(yyyymmdd)、1行目C列〜=店名、2行目〜 A=対象wk(yyyymmdd)/B=商品No/C〜=数量。

振分表マクロは空白セルを詰めて書き出すため、空白が1つあるとそれ以降の数字が隣の店へズレる。
ズレた数字を黙って登録しないよう、「1つでもおかしければ1件も保存しない」。
1行目の名前は 販売場所 → 振分だけの項目（なだや等・振分の消し込みにだけ使う）の順で引く。
「臨時:」で始まる名前は振分表の店舗名変換に無い列（学園1等・その日だけの列）。登録なしで受け、その日の画面にだけ出す。
"""
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta

import jpholiday
import openpyxl
from django.db import transaction

from sales.models import AllocationExtra, AllocationExtraQuantity, Holiday, ItemQuantity, Product, SalesLocation

MAX_PRODUCT_ROW = 12  # 2〜12行目＝商品No.1〜11（11=大盛りご飯）
TEMP_PREFIX = "臨時:"  # 振分表マクロが店舗名変換に無い列に付ける印


@dataclass(frozen=True)
class TempColumn:
    """臨時の列（その日だけ）。保存のときに AllocationExtra(temp_date=その日) にする。"""
    name: str
    order: int  # 振分表の列の順


@dataclass
class ParsedItemQuantity:
    target_date: date | None = None
    target_week: date | None = None
    # (product, sales_location, quantity)
    entries: list = field(default_factory=list)
    # (product, AllocationExtra or TempColumn, quantity)。持参数(ItemQuantity)とは別に保存する
    extra_entries: list = field(default_factory=list)
    errors: list = field(default_factory=list)
    # 止めずに知らせるだけのもの
    warnings: list = field(default_factory=list)


def _parse_yyyymmdd(value):
    try:
        return datetime.strptime(str(value).strip(), "%Y%m%d").date()
    except (TypeError, ValueError):
        return None


def parse_item_quantity_workbook(fileobj) -> ParsedItemQuantity:
    result = ParsedItemQuantity()
    try:
        sheet = openpyxl.load_workbook(fileobj, data_only=True).active
    except Exception:
        result.errors.append("Excelファイルとして読み込めませんでした")
        return result

    result.target_date = _parse_yyyymmdd(sheet.title)
    if result.target_date is None:
        result.errors.append(f"シート名「{sheet.title}」が日付(yyyymmdd)ではありません")
    result.target_week = _parse_yyyymmdd(sheet.cell(row=2, column=1).value)
    if result.target_week is None:
        result.errors.append("A2の対象wkが日付(yyyymmdd)ではありません")
    if result.errors:
        return result

    locations = {loc.name: loc for loc in SalesLocation.objects.all()}
    extras = {x.name: x for x in AllocationExtra.objects.filter(temp_date__isnull=True)}
    headers = []  # (列番号, 店名, SalesLocation or AllocationExtra or TempColumn or None)
    for col in range(3, sheet.max_column + 1):
        name = sheet.cell(row=1, column=col).value
        if name is None or str(name).strip() == "":
            continue
        name = str(name).strip()
        if name.startswith(TEMP_PREFIX) and name[len(TEMP_PREFIX):].strip():
            headers.append((col, name, TempColumn(name[len(TEMP_PREFIX):].strip(), col)))
            continue
        headers.append((col, name, locations.get(name) or extras.get(name)))
    if not headers:
        result.errors.append("1行目に店名がありません")
        return result

    # 販売場所にも振分だけの項目にも無い名前は止める（販売場所データの［振分設定］で項目を足してから再送）
    unknown = [name for _, name, loc in headers if loc is None]
    if unknown:
        result.errors.append("アプリに登録されていない店名：" + "、".join(unknown))
    seen = set()
    for _, name, _ in headers:
        if name in seen:
            result.errors.append(f"店名「{name}」が2回出てきます")
        seen.add(name)

    products = {
        p.no: p for p in Product.objects.filter(week=result.target_week)
    }
    for row in range(2, MAX_PRODUCT_ROW + 1):
        product_no = sheet.cell(row=row, column=2).value
        if product_no is None:
            continue
        product = products.get(product_no)
        if product is None:
            result.errors.append(
                f"{row}行目：対象wk {result.target_week:%Y%m%d} のメニューNo.{product_no} がアプリにありません"
            )
            continue
        for col, name, loc in headers:
            value = sheet.cell(row=row, column=col).value
            # 振分だけの列・臨時の列は振分表で空白＝0の書き方なので止めない
            if isinstance(loc, (AllocationExtra, TempColumn)) and (value is None or str(value).strip() == ""):
                value = 0
            if value is None or str(value).strip() == "":
                result.errors.append(
                    f"No.{product_no}（{product.name}）の「{name}」が空白です（列ズレの可能性）"
                )
                continue
            try:
                as_float = float(value)
            except (TypeError, ValueError):
                result.errors.append(f"No.{product_no}の「{name}」が数字ではありません：{value}")
                continue
            if as_float < 0 or not as_float.is_integer():
                result.errors.append(f"No.{product_no}の「{name}」が正しい個数ではありません：{value}")
                continue
            quantity = int(as_float)
            if isinstance(loc, (AllocationExtra, TempColumn)):
                result.extra_entries.append((product, loc, quantity))
            elif loc is not None:
                result.entries.append((product, loc, quantity))

    if not result.entries and not result.errors:
        result.errors.append("登録できる持参数がありません")
    return result


def save_item_quantities(parsed: ParsedItemQuantity) -> int:
    """同じ 対象日×対象wk×メニュー×店 は上書き（予定・現在とも振分表の数に）。保存件数を返す。"""
    # ItemQuantity の日付は文字列カラムに date を渡して "YYYY-MM-DD" で保存してきた（既存データと揃える）
    target_date = parsed.target_date.isoformat()
    target_week = parsed.target_week.isoformat()
    with transaction.atomic():
        existing = {
            (iq.product_id, iq.sales_location_id): iq
            for iq in ItemQuantity.objects.filter(target_date=target_date, target_week=target_week)
        }
        for product, loc, quantity in parsed.entries:
            iq = existing.get((product.id, loc.id))
            if iq is None:
                ItemQuantity.objects.create(
                    target_date=target_date, target_week=target_week, product=product,
                    sales_location=loc, quantity=quantity, planned_quantity=quantity,
                )
                continue
            # 振り分けた数と違う数が届いたら、その消し込みはやり直し
            if iq.quantity != quantity:
                iq.checked_at = None
                iq.checked_by = None
            iq.quantity = quantity
            iq.planned_quantity = quantity
            iq.save(update_fields=["quantity", "planned_quantity", "checked_at", "checked_by"])
        _save_extra_quantities(parsed)
    return len(parsed.entries) + len(parsed.extra_entries)


def _resolve_temp_columns(parsed):
    """臨時の列をその日の AllocationExtra に。再送で無くなった臨時の列は消す（数・履歴ごと）。"""
    temps = {x: None for _, x, _ in parsed.extra_entries if isinstance(x, TempColumn)}
    current = AllocationExtra.objects.filter(temp_date=parsed.target_date)
    current.exclude(name__in=[t.name for t in temps]).delete()
    for t in temps:
        extra, _ = AllocationExtra.objects.update_or_create(
            name=t.name, temp_date=parsed.target_date, defaults={"allocation_order": t.order})
        temps[t] = extra
    parsed.extra_entries = [(p, temps.get(x, x) if isinstance(x, TempColumn) else x, q)
                            for p, x, q in parsed.extra_entries]


def _save_extra_quantities(parsed):
    """振分だけの項目も持参数と同じ上書きのしかた（予定・現在とも振分表の数に・数が変わった✓は外す）。"""
    _resolve_temp_columns(parsed)
    existing = {
        (x.product_id, x.extra_id): x
        for x in AllocationExtraQuantity.objects.filter(target_date=parsed.target_date)
    }
    for product, extra, quantity in parsed.extra_entries:
        x = existing.get((product.id, extra.id))
        if x is None:
            AllocationExtraQuantity.objects.create(
                target_date=parsed.target_date, product=product, extra=extra,
                quantity=quantity, planned_quantity=quantity,
            )
            continue
        if x.quantity != quantity:
            x.checked_at = None
            x.checked_by = None
        x.quantity = quantity
        x.planned_quantity = quantity
        x.save(update_fields=["quantity", "planned_quantity", "checked_at", "checked_by"])


def is_business_day(d: date) -> bool:
    """土日・祝日・会社休日(Holiday)以外。"""
    if d.weekday() >= 5 or jpholiday.is_holiday(d):
        return False
    return not Holiday.objects.filter(date=d).exists()


def next_business_day(d: date) -> date:
    n = d + timedelta(days=1)
    while not is_business_day(n):
        n += timedelta(days=1)
    return n
