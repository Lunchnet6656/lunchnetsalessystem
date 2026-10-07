"""弁当の販売価格を決める処理（日計表・予約・受注で共通）。

値段の決め方：
1. メニューに値段の種類（Product.rank）がある → その日に始まっている一番新しい価格表のマス
2. 種類がない、またはその日に始まっている価格表がない → 今までどおり Product.price_A/B/C

日付で価格表の版を引くので、同じ週のメニューのまま週の途中から値上げできる。
仕様: .company/engineering/harness/specs/lunchnetsale-価格と割引のマスタ化-要件定義.md §7
"""
import datetime
import logging

from sales.models import PRICE_PATTERNS, PriceTable

logger = logging.getLogger(__name__)

LEGACY_PRICE_FIELDS = {"A": "price_A", "B": "price_B", "C": "price_C"}


def pattern_of(location, default=None):
    """販売所の価格パターン（"A"/"B"/"C"）。不明なら default。"""
    pattern = (getattr(location, "price_type", "") or "").upper().strip()
    return pattern if pattern in PRICE_PATTERNS else default


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


class PriceBook:
    """1日分の値段表。日計表のように同じ日の値段を何品も引くとき、価格表の読み込みを1回で済ませる。"""

    def __init__(self, on_date):
        self.on_date = _to_date(on_date)
        self.table = (
            PriceTable.objects.filter(valid_from__lte=self.on_date)
            .order_by("-valid_from").prefetch_related("cells").first()
        )
        self._cells = (
            {(c.rank_id, c.pattern): c.price for c in self.table.cells.all()} if self.table else {}
        )

    def price(self, product, pattern):
        if pattern not in PRICE_PATTERNS:
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
