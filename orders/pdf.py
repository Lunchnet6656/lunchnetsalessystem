import io
import os
from decimal import Decimal, ROUND_DOWN

from django.template.loader import render_to_string
import weasyprint

from .models import OrderSettings

FONTS_DIR = os.path.join(os.path.dirname(__file__), 'static', 'orders', 'fonts')


def _customer_name_font_size(display_name, department, base_pt):
    """会社名・部署名の最長行に応じてフォントサイズ(pt)を返す"""
    max_len = max(len(display_name), len(department) if department else 0)
    if base_pt == 14:  # 納品書 (A5幅60%列 ≈ 76mm)
        if max_len <= 13:
            return 14
        elif max_len <= 17:
            return 12
        elif max_len <= 21:
            return 10
        else:
            return 9
    else:  # 領収書 base_pt=15 (A5横向き、幅十分)
        if max_len <= 17:
            return 15
        elif max_len <= 22:
            return 13
        elif max_len <= 27:
            return 11
        else:
            return 10


def build_delivery_slip_context(order):
    """納品書テンプレート用コンテキストを生成して返す"""
    settings = OrderSettings.load()
    items = list(order.items.all())
    extra_items = list(order.extra_items.all())

    sum_large = sum(item.quantity_large for item in items)
    sum_regular = sum(item.quantity_regular for item in items)
    sum_small = sum(item.quantity_small for item in items)
    sum_quantity = sum(item.quantity for item in items)

    bento_total = sum(item.subtotal for item in items)
    extra_total = sum(ei.subtotal for ei in extra_items)
    total = int(bento_total) + int(extra_total)

    tax_rate = settings.tax_rate
    total_decimal = Decimal(str(total))
    tax_divisor = Decimal(str(1 + float(tax_rate) / 100))
    tax_amount = int((total_decimal - total_decimal / tax_divisor).quantize(
        Decimal('1'), rounding=ROUND_DOWN
    ))

    for item in items:
        item.unit_price_formatted = f'{int(item.unit_price):,}'
        item.subtotal_formatted = f'{int(item.subtotal):,}'

    for ei in extra_items:
        ei.unit_price_formatted = f'{int(ei.unit_price):,}'
        ei.subtotal_formatted = f'{int(ei.subtotal):,}'

    return {
        'order': order,
        'settings': settings,
        'items': items,
        'extra_items': extra_items,
        'has_extra_items': len(extra_items) > 0,
        'delivery_date': order.delivery_date.strftime('%Y年%m月%d日'),
        'customer_name': order.customer.display_name(),
        'customer_department': order.customer.department if order.customer.customer_type == 'B2B' else '',
        'customer_name_font_size': f"{_customer_name_font_size(order.customer.display_name(), order.customer.department, 14)}pt",
        'sum_large': sum_large,
        'sum_regular': sum_regular,
        'sum_small': sum_small,
        'sum_quantity': sum_quantity,
        'bento_total_formatted': f'{int(bento_total):,}',
        'extra_total_formatted': f'{int(extra_total):,}',
        'total_formatted': f'{total:,}',
        'tax_rate': tax_rate,
        'tax_amount_formatted': f'{tax_amount:,}',
        'font_dir': FONTS_DIR,
        'payment_method': order.customer.payment_method,
        'customer_notes': order.customer.notes,
        'is_catering': order.customer.bento_type == 'CATERING',
    }


def generate_delivery_slip(order):
    """納品書PDFを生成してBytesIOバッファを返す"""
    context = build_delivery_slip_context(order)
    html = render_to_string('orders/delivery_slip.html', context)
    pdf_bytes = weasyprint.HTML(string=html).write_pdf()
    buffer = io.BytesIO(pdf_bytes)
    return buffer


def build_receipt_context(order):
    """領収書テンプレート用コンテキストを生成して返す"""
    settings = OrderSettings.load()
    total = order.total

    tax_rate = settings.tax_rate
    total_decimal = Decimal(str(int(total)))
    tax_divisor = Decimal(str(1 + float(tax_rate) / 100))
    tax_amount = int((total_decimal - total_decimal / tax_divisor).quantize(
        Decimal('1'), rounding=ROUND_DOWN
    ))
    tax_excluded = int(total) - tax_amount

    issue_date = order.delivery_date or order.order_date

    return {
        'order': order,
        'settings': settings,
        'customer_name': order.customer.display_name(),
        'customer_department': order.customer.department if order.customer.customer_type == 'B2B' else '',
        'customer_name_font_size': f"{_customer_name_font_size(order.customer.display_name(), order.customer.department, 15)}pt",
        'issue_date': issue_date.strftime('%Y年%m月%d日'),
        'total_formatted': f'{int(total):,}',
        'tax_excluded_formatted': f'{tax_excluded:,}',
        'tax_amount_formatted': f'{tax_amount:,}',
        'tax_rate': tax_rate,
        'receipt_memo': order.receipt_memo,
        'font_dir': FONTS_DIR,
    }


def generate_receipt(order):
    """領収書PDFを生成してBytesIOバッファを返す"""
    context = build_receipt_context(order)
    html = render_to_string('orders/receipt.html', context)
    pdf_bytes = weasyprint.HTML(string=html).write_pdf()
    buffer = io.BytesIO(pdf_bytes)
    return buffer


def build_invoice_context(invoice):
    """請求書（適格請求書）テンプレート用コンテキストを生成する。

    金額は発行時点のスナップショット（Invoice）を使う。明細は日別（納品日順）。
    """
    from . import services
    settings = OrderSettings.load()
    customer = invoice.customer

    # 単価別マトリクスに集計（月末締めは対象月の全日付を並べる）
    matrix = services.build_price_matrix(
        services.lines_as_entries(invoice.lines.all()),
        invoice.period_start, invoice.period_end,
    )
    price_columns = [f"¥{p:,}" for p in matrix['price_columns']]
    def _extras_text(items):
        # 品名×数量 ＋ 金額（数量なしは品名＋金額）。複数種は行で分ける。
        out = []
        for it in items:
            amt = f"¥{int(it['amount']):,}"
            if it['qty']:
                out.append(f"{it['name']}×{it['qty']} {amt}")
            else:
                out.append(f"{it['name']} {amt}")
        return out

    day_rows = []
    for r in matrix['day_rows']:
        day_rows.append({
            'day': r['date'].strftime('%-d'),
            'weekday': r['weekday'],
            'is_weekend': r['date'].weekday() >= 5,
            'has_order': r['has_order'],
            'cells': [(c if c else '') for c in r['cells_list']],
            'qty': r['day_qty'] if r['day_qty'] else '',
            'large': r['large'] if r['large'] else '',
            'extras_items': _extras_text(r['extra_items']),
            'amount_formatted': f"¥{int(r['amount']):,}" if r['amount'] is not None else '',
        })
    col_totals = [f"{t:,}" for t in matrix['col_totals_list']]
    credit_rows = []
    for r in matrix['credit_rows']:
        credit_rows.append({
            'date': r['date'].strftime('%-m/%-d'),
            'weekday': r['weekday'],
            'label': r['label'],
            'amount_formatted': f"△{int(-r['amount']):,}",
        })

    period = ''
    title_month = ''
    if invoice.period_start and invoice.period_end:
        period = (f"{invoice.period_start.strftime('%Y年%m月%d日')}"
                  f"〜{invoice.period_end.strftime('%Y年%m月%d日')}")
        # タイトルの「◯月分」＝対象月（月末締めの請求月）
        title_month = f"{invoice.period_start.month}月分"

    return {
        'invoice': invoice,
        'settings': settings,
        'customer_name': customer.display_name(),
        'customer_department': customer.department if customer.customer_type == 'B2B' else '',
        'customer_name_font_size': f"{_customer_name_font_size(customer.display_name(), customer.department, 14) + 3}pt",
        'customer_dept_font_size': "10.5pt",
        'title_month': title_month,
        # 社印：顧客が「メール配信」かつ社印画像が登録済みのとき、発行元に重ねて押印する
        'seal_data': (settings.seal_image_data
                      if customer.invoice_delivery == 'EMAIL' and settings.seal_image_data
                      else ''),
        'price_columns': price_columns,
        'day_rows': day_rows,
        'col_totals': col_totals,
        'bento_total_qty': matrix['bento_total_qty'],
        'charge_total_matrix_formatted': f"{int(matrix['charge_total']):,}",
        'has_large': matrix['has_large'],
        'large_unit': f"{matrix['large_unit']:,}",
        'large_total': matrix['large_total'],
        'extras_total_formatted': f"¥{int(matrix['extras_total']):,}" if matrix['has_extras'] else '',
        'extra_totals': [
            (f"{t['name']}×{t['qty']}（¥{int(t['amount']):,}）" if t['qty']
             else f"{t['name']}（¥{int(t['amount']):,}）")
            for t in matrix['extra_name_totals']
        ],
        'has_extras': matrix['has_extras'],
        'credit_rows': credit_rows,
        'period': period,
        'issue_date': invoice.issue_date.strftime('%Y年%m月%d日'),
        'due_date': invoice.due_date.strftime('%Y年%m月%d日') if invoice.due_date else '',
        'charge_total_formatted': f'{int(invoice.charge_total):,}',
        'credit_total_formatted': f'{int(invoice.credit_total):,}',
        'net_amount_formatted': f'{int(invoice.net_amount):,}',
        'tax_excluded_formatted': f'{invoice.tax_excluded:,}',
        'tax_amount_formatted': f'{int(invoice.tax_amount):,}',
        'tax_rate': invoice.tax_rate,
        'has_credit': int(invoice.credit_total) > 0,
        'payment_method': customer.payment_method,
        'font_dir': FONTS_DIR,
    }


def generate_invoice(invoice):
    """請求書PDFを生成してBytesIOバッファを返す"""
    context = build_invoice_context(invoice)
    html = render_to_string('orders/invoice.html', context)
    pdf_bytes = weasyprint.HTML(string=html).write_pdf()
    buffer = io.BytesIO(pdf_bytes)
    return buffer


def generate_invoices_combined(invoices):
    """複数の請求書を1つのPDF（各請求書＝改ページ）に結合してBytesIOで返す。印刷向け。"""
    docs = []
    for inv in invoices:
        html = render_to_string('orders/invoice.html', build_invoice_context(inv))
        docs.append(weasyprint.HTML(string=html).render())
    if not docs:
        return io.BytesIO()
    all_pages = [page for doc in docs for page in doc.pages]
    pdf_bytes = docs[0].copy(all_pages).write_pdf()
    return io.BytesIO(pdf_bytes)
