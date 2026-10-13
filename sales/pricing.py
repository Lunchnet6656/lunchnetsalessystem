"""弁当の販売価格を決める処理（日計表・予約・受注で共通）。

値段の決め方：
1. メニューに値段の種類（Product.rank）がある → その日に始まっている一番新しい価格表のマス
2. 種類がない、またはその日に始まっている価格表がない → 今までどおり Product.price_A/B/C
3. 独自価格の顧客 → その日に始まっている、その顧客だけの価格表のマス（種類のないメニューは役割で引く）

日付で価格表の版を引くので、同じ週のメニューのまま週の途中から値上げできる。
仕様: .company/engineering/harness/specs/lunchnetsale-価格と割引のマスタ化-要件定義.md §7
      .company/engineering/harness/specs/lunchnetsale-価格の一元化-要件と画面設計.md §4
"""
import datetime
import logging

from sales.models import PricePattern, PriceRank, PriceTable

logger = logging.getLogger(__name__)

LEGACY_PRICE_FIELDS = {"A": "price_A", "B": "price_B", "C": "price_C"}
CUSTOM = "CUSTOM"          # 顧客の価格タイプ「独自価格」
CUSTOMER_COLUMN = ""       # 顧客別価格表のマスの pattern


def pattern_codes():
    """使用中の価格パターンの記号（列の並び順）。"""
    return list(PricePattern.objects.filter(active=True).values_list("code", flat=True))


def pattern_names():
    """記号 → 画面の名前（終了したパターンも含む。過去の価格表の列見出しに使う）。"""
    return dict(PricePattern.objects.values_list("code", "name"))


def known_codes():
    """登録されている価格パターンの記号（終了したものも含む）。販売所を何件も見るときは先に1回だけ引く。"""
    return set(PricePattern.objects.values_list("code", flat=True))


def pattern_of(location, default=None, codes=None):
    """販売所の価格パターンの記号。登録されていない記号なら default。"""
    pattern = (getattr(location, "price_type", "") or "").upper().strip()
    return pattern if pattern in (codes if codes is not None else known_codes()) else default


def is_custom(customer):
    return getattr(customer, "price_type", "") == CUSTOM


def _to_date(on_date):
    if isinstance(on_date, datetime.datetime):
        return on_date.date()
    if isinstance(on_date, datetime.date):
        return on_date
    # ItemQuantity.target_date / Product.week は "2026-10-07" と "20261007" が混在する
    text = str(on_date).strip()
    for fmt in ("%Y-%m-%d", "%Y%m%d"):
        try:
            return datetime.datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    raise ValueError(f"日付として読めません: {on_date!r}")


def legacy_price(product, pattern):
    field = LEGACY_PRICE_FIELDS.get(pattern)
    return int(getattr(product, field) or 0) if field else 0


def legacy_role(product):
    """種類のない古いメニューを、どの種類として扱うか（★あり→特選、大盛りごはん→大盛り、それ以外→通常）。"""
    name = product.name or ""
    if "大盛り" in name:
        return "large"
    return "star" if "★" in name else "normal"


def table_in_effect(on_date, customer=None):
    """その日に使う価格表。customer を渡すとその顧客の価格表、渡さなければ販売所用。"""
    qs = PriceTable.objects.filter(valid_from__lte=on_date)
    qs = qs.filter(customer=customer) if customer is not None else qs.filter(customer__isnull=True)
    return qs.order_by("-valid_from").prefetch_related("cells").first()


class PriceBook:
    """1日分の値段表。日計表のように同じ日の値段を何品も引くとき、価格表の読み込みを1回で済ませる。
    customer を渡すと customer_price() でその顧客の値段を引ける。"""

    def __init__(self, on_date, customer=None):
        self.on_date = _to_date(on_date)
        self.table = table_in_effect(self.on_date)
        self._cells = (
            {(c.rank_id, c.pattern): c.price for c in self.table.cells.all()} if self.table else {}
        )
        self.customer = customer
        self.own_table = table_in_effect(self.on_date, customer) if is_custom(customer) else None
        self._own_cells = {c.rank_id: c.price for c in self.own_table.cells.all()} if self.own_table else {}
        self._role_ids = None

    def price(self, product, pattern):
        if not pattern:
            return 0
        if product.rank_id and self.table:
            price = self._cells.get((product.rank_id, pattern))
            if price is not None:
                return price
            logger.warning(
                "価格表 %s に %s/%s のマスがないため price_%s を使います（%s）",
                self.table.valid_from, product.rank_id, pattern, pattern, product,
            )
        return legacy_price(product, pattern)

    def _rank_id_for(self, product):
        if product.rank_id:
            return product.rank_id
        if self._role_ids is None:
            self._role_ids = dict(PriceRank.objects.exclude(role="").values_list("role", "id"))
        return self._role_ids.get(legacy_role(product))

    def customer_price(self, product):
        """その顧客の値段。独自価格の顧客で値段が未設定なら None（注文を保存させない）。"""
        if not is_custom(self.customer):
            return self.price(product, getattr(self.customer, "price_type", "") or "A")
        price = self._own_cells.get(self._rank_id_for(product))
        if price is None:
            logger.warning("顧客 %s の価格表に %s の値段がありません（%s）", self.customer, product, self.on_date)
        return price


def price_for(product, pattern, on_date):
    """1品だけ引くとき用。何品も引くなら PriceBook を使う。"""
    return PriceBook(on_date).price(product, pattern)


def is_bento(product):
    """クーポン・サービスの欄を作る対象か（大盛りごはんは弁当ではない）。"""
    if product.rank_id:
        return product.rank.is_bento
    return "大盛り" not in (product.name or "")


def bento_prices_for(products, pattern, on_date):
    """その日その価格パターンで売る弁当の値段の一覧（重複なし・高い順・0円は除く）。
    クーポン欄とサービス販売欄はこの値段から作る。"""
    book = PriceBook(on_date)
    prices = {book.price(p, pattern) for p in products if is_bento(p)}
    return sorted((p for p in prices if p > 0), reverse=True)
