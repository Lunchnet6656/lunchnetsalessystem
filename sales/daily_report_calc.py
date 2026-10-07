"""日計表の割引欄の組み立てと、サーバー側の合計計算（入力フォーム・編集画面で共通）。

- 割引欄はコードに金額を書かず、その日・その販売所の値段から作る
  ・ご飯／割引返金 … その日に有効な割引項目（DiscountItem）
  ・クーポン／サービス販売 … その日その販売所で売る弁当の値段
- 編集画面は保存済みの割引明細（当時の名前と単価）から作る
- 割引合計・総売上・差額はサーバーで計算し直して保存する（画面が古くても正しい値が残る）
仕様: .company/engineering/harness/specs/lunchnetsale-価格と割引のマスタ化-要件定義.md §8.4・§9
"""
from dataclasses import dataclass

from sales.discounts import (
    COUPON_650_SINCE, SERVICE_FLAT_LABEL, LineSpec, items_on, legacy_base_price, legacy_lines,
)

GROUP_ORDER = ("rice", "coupon", "refund", "service")
GROUP_TITLES = {"rice": "ご飯", "coupon": "クーポン", "refund": "割引・返金", "service": "サービス販売個数"}

# 移行期間に旧カラムへも同じ数を書く（CSV・管理画面の互換のため）。値段が旧欄の意味と一致するときだけ。
LEGACY_TIER_FIELDS = {
    "coupon": ("coupon_type_750", "coupon_type_700", "coupon_type_600"),
    "service": ("service_type_750", "service_type_700", "service_type_600"),
}
LEGACY_DISCOUNT_FIELDS = (
    "no_rice_quantity", "extra_rice_quantity", "discount_50", "discount_100",
    "coupon_type_600", "coupon_type_700", "coupon_type_750",
    "service_type_600", "service_type_700", "service_type_750", "service_type_100",
)


@dataclass
class DiscountField:
    group: str
    name: str          # POST の名前
    label: str         # 画面の表示（例「750円」「なし（▲100円）」）
    line_label: str    # 明細に残す名前（例「クーポン750円」）
    unit_amount: int   # 1個あたりの割引額（符号付き）
    base_price: int
    item: object = None
    quantity: int = 0


def _yen(amount):
    return f"▲{abs(amount):,}円" if amount < 0 else f"＋{amount:,}円"


def _item_field(item, quantity=0):
    unit = item.unit_amount
    label = item.label if item.label.endswith("円") else f"{item.label}（{_yen(unit)}）"
    return DiscountField("rice" if item.group == "rice" else "refund", f"disc_item_{item.id}",
                         label, item.label, unit, item.amount, item, quantity)


def _coupon_field(base, quantity=0):
    return DiscountField("coupon", f"disc_coupon_{base}", f"{base:,}円", f"クーポン{base}円",
                         -base, base, None, quantity)


def _service_field(base, service_price, quantity=0):
    return DiscountField("service", f"disc_service_{base}", f"{base:,}円", f"サービス{base}円",
                         service_price - base, base, None, quantity)


def _service_flat_field(service_price, quantity=0):
    return DiscountField("service", "disc_service_flat", "サービス販売数", SERVICE_FLAT_LABEL,
                         service_price, service_price, None, quantity)


def service_mode(location):
    """販売所のサービス方式：None（なし）／"flat"（割引＝サービス価格×個数）／"tiers"（弁当の値段ごと）。"""
    style = (getattr(location, "service_style", "") or "").strip()
    if not style or style == "なし" or getattr(location, "service_name", "") == "なし":
        return None
    return "flat" if style == "割引" else "tiers"


def input_fields(on_date, location, bento_prices):
    """入力フォームの割引欄（数はすべて0）。"""
    fields = [_item_field(item) for item in items_on(on_date)]
    fields += [_coupon_field(base) for base in bento_prices]
    mode = service_mode(location)
    service_price = int(getattr(location, "service_price", 0) or 0)
    if mode == "tiers":
        fields += [_service_field(base, service_price) for base in bento_prices]
    elif mode == "flat":
        fields.append(_service_flat_field(service_price))
    return sort_fields(fields)


def edit_fields(report, location, bento_prices):
    """編集画面の割引欄。保存済みの明細は当時の名前と単価のまま、その日に有効なのに明細がない欄は0で足す。"""
    saved = list(report.discount_lines.select_related("item"))
    if saved:
        lines = [LineSpec(l.group, l.item, l.label, l.base_price, l.unit_amount, l.quantity) for l in saved]
    else:
        # 移行コマンドをまだ流していない日計表は、旧カラムから組み立てる
        lines = legacy_lines(report, infer_from_total=int(report.total_discount or 0))

    fields, seen = [], set()
    for line in lines:
        field = _field_from_line(line)
        fields.append(field)
        seen.add(field.name)
    for field in input_fields(report.date, location, bento_prices) if location else []:
        if field.name not in seen:
            fields.append(field)
    return sort_fields(fields)


def _field_from_line(line):
    if line.item is not None:
        return _item_field(line.item, line.quantity)
    if line.group == "coupon":
        return _coupon_field(line.base_price, line.quantity)
    if line.label == SERVICE_FLAT_LABEL:
        return _service_flat_field(line.unit_amount, line.quantity)
    # サービスの単価は保存時点の「サービス価格 − 弁当の値段」をそのまま使う
    field = _service_field(line.base_price, 0, line.quantity)
    field.unit_amount = line.unit_amount
    return field


def sort_fields(fields):
    def key(f):
        flat = f.name == "disc_service_flat"
        sort_order = f.item.sort_order if f.item is not None else 0
        return (GROUP_ORDER.index(f.group), flat, sort_order, -f.base_price)
    return sorted(fields, key=key)


def grouped(fields):
    """テンプレート用：[(グループ名, 見出し, [欄...]), ...]（欄のないグループは出さない）。"""
    return [(g, GROUP_TITLES[g], [f for f in fields if f.group == g])
            for g in GROUP_ORDER if any(f.group == g for f in fields)]


def signature(fields):
    """画面に出した割引欄の一覧。送信時に作り直した欄と比べ、日付・販売所の変更に気づくため。"""
    return ",".join(sorted(f.name for f in fields))


def read_quantities(fields, post):
    """POST の数を欄に入れ、明細（LineSpec）を返す。0個の欄は明細にしない。"""
    lines = []
    for field in fields:
        try:
            qty = max(0, int(str(post.get(field.name, "0")).strip() or 0))
        except ValueError:
            qty = 0
        field.quantity = qty
        if qty:
            lines.append(LineSpec(field.group, field.item, field.line_label, field.base_price,
                                  field.unit_amount, qty))
    return lines


def legacy_values(lines, on_date, coupon_650_since=COUPON_650_SINCE):
    """明細から旧カラムの値を作る（移行期間の二重書き込み用）。対応する旧欄がない値段は書かない。"""
    values = dict.fromkeys(LEGACY_DISCOUNT_FIELDS, 0)
    for line in lines:
        if line.item is not None and line.item.legacy_field in values:
            values[line.item.legacy_field] += line.quantity
        elif line.label == SERVICE_FLAT_LABEL:
            values["service_type_100"] += line.quantity
        elif line.group in LEGACY_TIER_FIELDS:
            for field in LEGACY_TIER_FIELDS[line.group]:
                if legacy_base_price(field, on_date, coupon_650_since) == line.base_price:
                    values[field] += line.quantity
                    break
    return values


@dataclass
class EntryRow:
    """値段計算用の商品1行（入力フォームの item_data・編集画面の entry の共通形）。"""
    product_no: int
    unit_price: int
    quantity: int
    remaining: int
    is_large: bool = False

    @property
    def sales_quantity(self):
        return max(0, self.quantity - self.remaining)

    @property
    def total_sales(self):
        return self.sales_quantity * self.unit_price


def price_summary(rows):
    """値段ごとの販売数（高い順・全部）。旧欄 sales_price_quantity_1〜3 は上から3つ。"""
    summary = {}
    for row in rows:
        if row.unit_price <= 0:
            continue
        s = summary.setdefault(row.unit_price, {"price": row.unit_price, "quantity": 0, "amount": 0,
                                                "is_large": row.is_large})
        s["quantity"] += row.sales_quantity
        s["amount"] += row.total_sales
        s["is_large"] = s["is_large"] and row.is_large
    return [summary[p] for p in sorted(summary, reverse=True)]


def totals(rows, others_total, lines, payments):
    """日計表の合計をサーバーで計算する。持参数・残数・販売数の合計は大盛り（No.11）を除く（画面と同じ）。"""
    counted = [r for r in rows if r.product_no != 11]
    summary = price_summary(rows)
    sales_total = sum(r.total_sales for r in rows)
    discount = sum(l.unit_amount * l.quantity for l in lines)
    revenue = sales_total + others_total + discount
    result = {
        "total_quantity": sum(r.quantity for r in counted),
        "total_remaining": sum(r.remaining for r in counted),
        "total_sales_quantity": sum(r.sales_quantity for r in counted),
        "total_others_sales": others_total,
        "total_discount": discount,
        "total_revenue": revenue,
        "sales_difference": sum(payments) - revenue,
    }
    for i in range(3):
        result[f"sales_price_quantity_{i + 1}"] = summary[i]["quantity"] if i < len(summary) else 0
    return result
