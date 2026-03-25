import io
import os
from decimal import Decimal, ROUND_DOWN

from django.template.loader import render_to_string
import weasyprint

from .models import OrderSettings

FONTS_DIR = os.path.join(os.path.dirname(__file__), 'static', 'orders', 'fonts')


def generate_delivery_slip(order):
    """納品書PDFを生成してBytesIOバッファを返す"""
    settings = OrderSettings.load()
    items = list(order.items.all())

    # 集計
    sum_large = sum(item.quantity_large for item in items)
    sum_regular = sum(item.quantity_regular for item in items)
    sum_small = sum(item.quantity_small for item in items)
    sum_quantity = sum(item.quantity for item in items)
    total = order.total

    # 消費税計算
    tax_rate = settings.tax_rate
    total_decimal = Decimal(str(int(total)))
    tax_divisor = Decimal(str(1 + float(tax_rate) / 100))
    tax_amount = int((total_decimal - total_decimal / tax_divisor).quantize(
        Decimal('1'), rounding=ROUND_DOWN
    ))

    # アイテムにフォーマット済み金額を付与
    for item in items:
        item.unit_price_formatted = f'{int(item.unit_price):,}'
        item.subtotal_formatted = f'{int(item.subtotal):,}'

    # テンプレートコンテキスト
    context = {
        'order': order,
        'settings': settings,
        'items': items,
        'delivery_date': order.delivery_date.strftime('%Y年%m月%d日'),
        'customer_name': order.customer.display_name(),
        'sum_large': sum_large,
        'sum_regular': sum_regular,
        'sum_small': sum_small,
        'sum_quantity': sum_quantity,
        'total_formatted': f'{int(total):,}',
        'tax_rate': tax_rate,
        'tax_amount_formatted': f'{tax_amount:,}',
        'font_dir': FONTS_DIR,
    }

    # HTMLレンダリング → PDF変換
    html = render_to_string('orders/delivery_slip.html', context)
    pdf_bytes = weasyprint.HTML(string=html).write_pdf()
    buffer = io.BytesIO(pdf_bytes)
    return buffer
