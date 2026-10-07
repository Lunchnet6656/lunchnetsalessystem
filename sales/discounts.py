"""日計表の割引の計算（移行コマンドと、S2のサーバー側計算で共通に使う）。

割引明細（DailyReportDiscountLine）のグループ：
- rice / refund : DiscountItem の金額 × 枚数
- coupon        : −弁当の値段 × 枚数
- service       : （サービス価格 − 弁当の値段）× 個数。サービス方式「割引」（service_type_100）は
                  「サービス割引」の1行で サービス価格 × 個数
割引合計は明細の合計。計算式は static/js/script.js の updateDiscount と同じ。
"""
import datetime
from collections import namedtuple
from decimal import Decimal, InvalidOperation

from django.db import transaction

from sales.models import DailyReportDiscountLine, DiscountItem

# 2025-05-05 のコミット 00b3b4c で、クーポン・サービスの「600円」欄が650円に変わった（欄名は600のまま）。
# 本番に出た日は不明（しょうへいも覚えていない）。移行の照合結果から割り出して直す。
COUPON_650_SINCE = datetime.date(2025, 5, 5)

LineSpec = namedtuple("LineSpec", "group item label base_price unit_amount quantity")
SERVICE_FLAT_LABEL = "サービス割引"


def service_price_of(report):
    """日計表に保存されたサービス価格。
    入力フォームは選択肢の値（＝サービス価格）を service_name に入れ、service_price は0のまま保存している。
    編集画面は service_name と service_price の両方にサービス価格を入れる。"""
    try:
        return int(Decimal(str(report.service_name).strip()))
    except (InvalidOperation, ValueError):
        return int(report.service_price or 0)


def legacy_base_price(field, on_date, coupon_650_since=COUPON_650_SINCE):
    """旧カラム名から、その日の弁当の値段を出す。"""
    if field.endswith("_600"):
        return 600 if on_date < coupon_650_since else 650
    return int(field.rsplit("_", 1)[1])


def items_on(on_date):
    return DiscountItem.objects.filter(valid_from__lte=on_date).exclude(valid_to__lt=on_date)


def legacy_lines(report, items=None, coupon_650_since=COUPON_650_SINCE, infer_from_total=None):
    """旧カラム（no_rice_quantity・coupon_type_* など）から割引明細を作る。枚数0の行は作らない。

    infer_from_total：保存済みの割引合計。サービス方式「割引」の個数があるのにサービス価格が
    日計表に残っていない古い記録（本番2025年に約70件）は、ここから1個あたりの価格を逆算する。"""
    items = list(items_on(report.date)) if items is None else items
    lines = []
    for item in items:
        if not item.legacy_field:
            continue
        qty = int(getattr(report, item.legacy_field) or 0)
        if qty:
            lines.append(LineSpec(item.group, item, item.label, item.amount, item.unit_amount, qty))

    for field in ("coupon_type_750", "coupon_type_700", "coupon_type_600"):
        qty = int(getattr(report, field) or 0)
        if qty:
            base = legacy_base_price(field, report.date, coupon_650_since)
            lines.append(LineSpec("coupon", None, f"クーポン{base}円", base, -base, qty))

    service_price = service_price_of(report)
    for field in ("service_type_750", "service_type_700", "service_type_600"):
        qty = int(getattr(report, field) or 0)
        if qty:
            base = legacy_base_price(field, report.date, coupon_650_since)
            lines.append(LineSpec("service", None, f"サービス{base}円", base, service_price - base, qty))

    qty = int(report.service_type_100 or 0)
    if qty:
        price = service_price_of(report)
        # 保存済みの割引合計が0円のときは、全角「＋」が読めず0円保存になった記録と区別できないので逆算しない
        if price == 0 and infer_from_total:
            rest = infer_from_total - sum(line.unit_amount * line.quantity for line in lines)
            # サービス割引はマイナスの値段。0円やプラスになるなら逆算しない（別の原因のずれを隠さないため）
            if rest % qty == 0 and rest < 0:
                price = rest // qty
        lines.append(LineSpec("service", None, SERVICE_FLAT_LABEL, price, price, qty))
    return lines


def total_discount(report, lines):
    """割引合計＝明細の合計。"""
    return sum(line.unit_amount * line.quantity for line in lines)


@transaction.atomic
def save_lines(report, lines):
    """日計表の割引明細を lines で置き換える。"""
    report.discount_lines.all().delete()
    DailyReportDiscountLine.objects.bulk_create([
        DailyReportDiscountLine(
            report=report, group=line.group, item=line.item, label=line.label,
            base_price=line.base_price, unit_amount=line.unit_amount,
            quantity=line.quantity, amount=line.unit_amount * line.quantity,
        )
        for line in lines
    ])
