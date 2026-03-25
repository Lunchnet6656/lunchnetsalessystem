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
        'font_dir': FONTS_DIR,
    }


def generate_receipt(order):
    """領収書PDFを生成してBytesIOバッファを返す"""
    context = build_receipt_context(order)
    html = render_to_string('orders/receipt.html', context)
    pdf_bytes = weasyprint.HTML(string=html).write_pdf()
    buffer = io.BytesIO(pdf_bytes)
    return buffer
