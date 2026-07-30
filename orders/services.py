"""請求書発行の集計エンジン（v2）。

仕様: 請求書発行_要件定義.md
- 選択された未請求受注 ＋ 紐づく未取込CREDIT調整（＋繰越）を1枚に束ねる。
- Σ受注合計(charge) − Σ控除(credit) = net、税は pdf.py と同一式で割戻し（÷(1+税率)・ROUND_DOWN）。
- 発行(ISSUED)確定時に order.invoice / adjustment.billed_invoice をセットしてロック（二重請求防止）。
- 発行は transaction + select_for_update で競合を防ぐ。
"""
from decimal import Decimal, ROUND_DOWN
from datetime import timedelta

from django.db import transaction

from .models import Order, OrderAdjustment, Invoice, InvoiceLine, OrderSettings

WEEKDAY_JP = ['月', '火', '水', '木', '金', '土', '日']


def _span_dates(by_date, period_start, period_end):
    if period_start and period_end:
        return [period_start + timedelta(days=i)
                for i in range((period_end - period_start).days + 1)]
    return sorted(by_date.keys())


def build_price_matrix(entries, period_start=None, period_end=None):
    """明細を「日付×単価」のマトリクスに集計する（請求書 v2.1）。

    entries: dictのリスト。キー source_type / line_date / quantity / unit_price / amount /
             has_order_item（弁当明細か）/ label。
    - 弁当（order_itemあり・単価>0・数量>0）は単価列へ、それ以外のORDER行（追加商品・仕出し）は「追加」金額へ。
    - period が与えられれば対象月の全日付を（受注が無い日も空行で）並べる。

    戻り値 dict：price_columns / day_rows / col_totals_list / extras_total / charge_total /
    has_extras / credit_rows。day_rows[i]['cells_list'] は price_columns と同じ並び（0はNone）。
    """
    by_date = {}
    credit_rows = []
    prices = set()
    large_count_total = 0
    large_upcharge_total = Decimal('0')
    extra_name_totals = {}  # 追加商品の品名別合計 {name: {'qty', 'amount'}}
    for e in entries:
        d = e['line_date']
        if e['source_type'] == InvoiceLine.SOURCE_ADJUSTMENT:
            credit_rows.append({
                'date': d, 'weekday': WEEKDAY_JP[d.weekday()],
                'label': e.get('label', ''), 'amount': e['amount'],
            })
            continue
        info = by_date.setdefault(
            d, {'cells': {}, 'large': 0, 'extras': Decimal('0'),
                'extra_items': {}, 'amount': Decimal('0')})
        info['amount'] += e['amount']
        up = e.get('unit_price')
        qty = e.get('quantity')
        # 弁当明細か（保存済みは has_order_item、プレビューdictは order_item の有無で判定）
        has_oi = e.get('has_order_item')
        if has_oi is None:
            has_oi = e.get('order_item') is not None
        is_bento = has_oi and up not in (None, 0) and bool(qty)
        if is_bento:
            up = int(up)
            info['cells'][up] = info['cells'].get(up, 0) + qty
            prices.add(up)
            # 大盛り：基本単価に上乗せ分（+50 等）を別集計。上乗せ額 = 小計 − 単価×数量。
            # 一体型（チャーハン等）は大盛り割増なし＝上乗せ0なので大盛り列には入れない（通常の弁当として計上）。
            ql = e.get('quantity_large') or 0
            if ql:
                upcharge = e['amount'] - Decimal(up) * qty
                if upcharge > 0:
                    info['large'] += ql
                    large_count_total += ql
                    large_upcharge_total += upcharge
        else:
            # 追加商品・仕出し等（弁当以外）。品名ごとに数量・金額を集計して「追加」欄に明記する。
            info['extras'] += e['amount']
            name = (e.get('label') or '追加').strip() or '追加'
            di = info['extra_items'].setdefault(name, {'qty': 0, 'amount': Decimal('0')})
            gi = extra_name_totals.setdefault(name, {'qty': 0, 'amount': Decimal('0')})
            if e.get('quantity'):
                di['qty'] += e['quantity']
                gi['qty'] += e['quantity']
            di['amount'] += e['amount']
            gi['amount'] += e['amount']

    price_columns = sorted(prices, reverse=True)  # 降順（高い順）
    has_large = large_count_total > 0
    large_unit = int(large_upcharge_total / large_count_total) if large_count_total else 0
    span = _span_dates(by_date, period_start, period_end)

    day_rows = []
    for d in span:
        info = by_date.get(d)
        if info:
            extra_items = [
                {'name': n, 'qty': v['qty'], 'amount': v['amount']}
                for n, v in info['extra_items'].items()
            ]
            _cells = [info['cells'].get(p) or None for p in price_columns]
            day_rows.append({
                'date': d, 'weekday': WEEKDAY_JP[d.weekday()], 'has_order': True,
                'cells_list': _cells,
                'day_qty': sum(c for c in _cells if c) or None,  # その日のお弁当食数（販売価格列の合計）
                'large': info['large'] or None,
                'extras': info['extras'] or None,
                'extra_items': extra_items,
                'amount': info['amount'],
            })
        else:
            day_rows.append({
                'date': d, 'weekday': WEEKDAY_JP[d.weekday()], 'has_order': False,
                'cells_list': [None for _ in price_columns],
                'day_qty': None,
                'large': None, 'extras': None, 'extra_items': [], 'amount': None,
            })

    col_totals_list = [
        sum((r['cells_list'][i] or 0) for r in day_rows if r['has_order'])
        for i in range(len(price_columns))
    ]
    bento_total_qty = sum(col_totals_list)  # お弁当の総食数（各単価列の合計）
    extras_total = sum(((r['extras'] or 0) for r in day_rows if r['has_order']), Decimal('0'))
    charge_total = sum(((r['amount'] or 0) for r in day_rows if r['has_order']), Decimal('0'))
    return {
        'price_columns': price_columns,
        'day_rows': day_rows,
        'col_totals_list': col_totals_list,
        'bento_total_qty': bento_total_qty,
        'has_large': has_large,
        'large_unit': large_unit,
        'large_total': large_count_total,
        'extras_total': extras_total,
        'extra_name_totals': [
            {'name': n, 'qty': v['qty'], 'amount': v['amount']}
            for n, v in extra_name_totals.items()
        ],
        'charge_total': charge_total,
        'has_extras': extras_total > 0,
        'credit_rows': credit_rows,
    }


def calc_tax_included(net_amount, tax_rate):
    """税込金額から内税額を割り戻す。納品書・領収書（pdf.py）と同一式。"""
    total_decimal = Decimal(str(int(net_amount)))
    tax_divisor = Decimal(str(1 + float(tax_rate) / 100))
    if net_amount == 0:
        return 0
    return int((total_decimal - total_decimal / tax_divisor).quantize(
        Decimal('1'), rounding=ROUND_DOWN
    ))


def collect_credits(orders, extra_credits=None):
    """選択受注に紐づく未取込CREDIT調整 ＋ 繰越（extra_credits）を重複なく返す。"""
    seen = set()
    credits = []
    for order in orders:
        for adj in order.adjustments.all():
            if (adj.settlement == OrderAdjustment.SETTLEMENT_CREDIT
                    and adj.billed_invoice_id is None and adj.pk not in seen):
                seen.add(adj.pk)
                credits.append(adj)
    for adj in (extra_credits or []):
        if adj.pk not in seen:
            seen.add(adj.pk)
            credits.append(adj)
    return credits


def _summarize_reason(text, limit=40):
    text = (text or '').replace('\n', ' ').strip()
    return text[:limit]


def build_lines(orders, credits):
    """日別の請求明細（dictのリスト）を組み立てる。受注明細は1行ずつ展開、控除はマイナス行。"""
    lines = []
    sort_order = 0
    # 受注ぶん（納品日順）。各明細を1行ずつ。数量が無い明細（仕出し等）は数量・単価NULL・金額のみ。
    for order in sorted(orders, key=lambda o: (o.delivery_date, o.pk)):
        for item in order.items.all():
            has_qty = bool(item.quantity)
            lines.append({
                'source_type': InvoiceLine.SOURCE_ORDER,
                'order': order,
                'order_item': item,
                'adjustment': None,
                'line_date': order.delivery_date,
                'label': item.product_name,
                'quantity': item.quantity if has_qty else None,
                'quantity_large': item.quantity_large if has_qty else None,
                'unit_price': item.unit_price if has_qty else None,
                'amount': item.subtotal,
                'sort_order': sort_order,
            })
            sort_order += 1
        for ei in order.extra_items.all():
            has_qty = bool(ei.quantity)
            lines.append({
                'source_type': InvoiceLine.SOURCE_ORDER,
                'order': order,
                'order_item': None,
                'adjustment': None,
                'line_date': order.delivery_date,
                'label': ei.product_name,
                'quantity': ei.quantity if has_qty else None,
                'unit_price': ei.unit_price if has_qty else None,
                'amount': ei.subtotal,
                'sort_order': sort_order,
            })
            sort_order += 1
    # 控除ぶん（発生日順・マイナス）
    for adj in sorted(credits, key=lambda a: (a.occurred_on, a.pk)):
        label = f"【控除】{adj.get_kind_display()}"
        reason = _summarize_reason(adj.reason)
        if reason:
            label = f"{label}・{reason}"
        lines.append({
            'source_type': InvoiceLine.SOURCE_ADJUSTMENT,
            'order': adj.order,
            'order_item': None,
            'adjustment': adj,
            'line_date': adj.occurred_on,
            'label': label,
            'quantity': None,
            'unit_price': None,
            'amount': -adj.amount,
            'sort_order': sort_order,
        })
        sort_order += 1
    return lines


def lines_as_entries(line_objs):
    """保存済み InvoiceLine を aggregate_day_rows 用の dict 列に正規化する。"""
    return [{
        'source_type': l.source_type,
        'line_date': l.line_date,
        'quantity': l.quantity,
        'quantity_large': l.quantity_large,
        'unit_price': (int(l.unit_price) if l.unit_price is not None else None),
        'amount': l.amount,
        'label': l.label,
        'has_order_item': l.order_item_id is not None,
    } for l in line_objs]


def preview_invoice(orders, extra_credits=None, tax_rate=None):
    """発行せずに集計だけ行い、プレビュー用の構造を返す（金額・明細）。"""
    if tax_rate is None:
        tax_rate = OrderSettings.load().tax_rate
    orders = list(orders)
    credits = collect_credits(orders, extra_credits)
    charge_total = sum((o.total for o in orders), Decimal('0'))
    credit_total = sum((c.amount for c in credits), Decimal('0'))
    net_amount = charge_total - credit_total
    tax_amount = calc_tax_included(net_amount, tax_rate)
    return {
        'orders': orders,
        'credits': credits,
        'lines': build_lines(orders, credits),
        'charge_total': charge_total,
        'credit_total': credit_total,
        'net_amount': net_amount,
        'tax_amount': tax_amount,
        'tax_excluded': int(net_amount) - int(tax_amount),
        'tax_rate': tax_rate,
    }


class InvoiceError(Exception):
    """発行不可（受注が既に請求済み・顧客不一致など）。"""


@transaction.atomic
def issue_invoice(customer, order_ids, extra_credit_ids=None, *,
                  pattern=Invoice.PATTERN_MONTH_END, issue_date=None,
                  period_start=None, period_end=None, due_date=None,
                  issued_by=None, notes=''):
    """受注群を1枚の請求書として発行（ISSUED）し、対象受注・調整をロックする。

    競合防止のため select_for_update で受注をロックし、未請求であることを再確認する。
    """
    from django.utils import timezone
    if issue_date is None:
        issue_date = timezone.localdate()

    # 受注をロックして取得（未請求・顧客一致を厳密に検証）
    orders = list(
        Order.objects.select_for_update()
        .filter(pk__in=list(order_ids))
        .prefetch_related('items', 'extra_items', 'adjustments')
    )
    if not orders:
        raise InvoiceError('請求対象の受注がありません。')
    for o in orders:
        if o.customer_id != customer.pk:
            raise InvoiceError(f'受注 {o.order_number} は選択顧客の受注ではありません。')
        if o.invoice_id is not None:
            raise InvoiceError(f'受注 {o.order_number} は既に請求済みです。')

    extra_credits = []
    if extra_credit_ids:
        extra_credits = list(
            OrderAdjustment.objects.select_for_update()
            .filter(pk__in=list(extra_credit_ids),
                    settlement=OrderAdjustment.SETTLEMENT_CREDIT,
                    billed_invoice__isnull=True)
        )

    data = preview_invoice(orders, extra_credits)

    invoice = Invoice.objects.create(
        invoice_number=Invoice.generate_invoice_number(issue_date),
        customer=customer,
        pattern=pattern,
        period_start=period_start,
        period_end=period_end,
        issue_date=issue_date,
        due_date=due_date,
        status=Invoice.STATUS_ISSUED,
        registration_number=OrderSettings.load().invoice_number,
        # 振込先は顧客に紐づく口座を優先し、未設定なら設定のデフォルト振込先にフォールバック
        bank_info=(customer.bank_account.info if customer.bank_account_id
                   else OrderSettings.load().bank_info),
        tax_rate=data['tax_rate'],
        charge_total=data['charge_total'],
        credit_total=data['credit_total'],
        net_amount=data['net_amount'],
        tax_amount=data['tax_amount'],
        notes=notes,
        issued_by=issued_by,
    )
    InvoiceLine.objects.bulk_create([
        InvoiceLine(invoice=invoice, **line) for line in data['lines']
    ])
    # ロック：受注と取込んだCREDIT調整を請求書に紐づける
    for o in orders:
        o.invoice = invoice
        o.save(update_fields=['invoice'])
    for adj in data['credits']:
        adj.billed_invoice = invoice
        adj.save(update_fields=['billed_invoice'])
    return invoice


@transaction.atomic
def void_invoice(invoice, reason='', user=None):
    """請求書を取消（VOID）。取込んだ受注・調整のロックを解除し、履歴として残す。"""
    if invoice.status == Invoice.STATUS_VOID:
        raise InvoiceError('この請求書は既に取消済みです。')
    invoice.orders.update(invoice=None)
    invoice.adjustments.update(billed_invoice=None)
    invoice.status = Invoice.STATUS_VOID
    invoice.void_reason = reason
    invoice.save(update_fields=['status', 'void_reason', 'updated_at'])
    return invoice


def carryover_credits_for_customer(customer):
    """繰越クレジット候補：既に請求済み受注に後発した未取込CREDIT調整。"""
    return list(
        OrderAdjustment.objects
        .filter(order__customer=customer,
                settlement=OrderAdjustment.SETTLEMENT_CREDIT,
                billed_invoice__isnull=True,
                order__invoice__isnull=False)
        .select_related('order')
        .order_by('occurred_on')
    )
