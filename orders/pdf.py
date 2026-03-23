import io
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.pdfgen import canvas
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.cidfonts import UnicodeCIDFont


def generate_delivery_slip(order):
    """納品書PDFを生成してBytesIOバッファを返す"""
    buffer = io.BytesIO()
    p = canvas.Canvas(buffer, pagesize=A4)
    width, height = A4

    # 日本語フォント登録
    pdfmetrics.registerFont(UnicodeCIDFont('HeiseiKakuGo-W5'))
    pdfmetrics.registerFont(UnicodeCIDFont('HeiseiMin-W3'))

    font_gothic = 'HeiseiKakuGo-W5'
    font_mincho = 'HeiseiMin-W3'

    # --- タイトル ---
    p.setFont(font_gothic, 24)
    p.drawCentredString(width / 2, height - 50 * mm, '納 品 書')

    # --- 受注番号・日付 ---
    p.setFont(font_gothic, 9)
    p.drawRightString(width - 20 * mm, height - 30 * mm, f'No. {order.order_number}')
    p.drawRightString(width - 20 * mm, height - 35 * mm,
                      f'納品日: {order.delivery_date.strftime("%Y年%m月%d日")}')
    p.drawRightString(width - 20 * mm, height - 40 * mm,
                      f'受注日: {order.order_date.strftime("%Y年%m月%d日")}')

    # --- 宛先 ---
    customer_name = order.customer.display_name()
    p.setFont(font_gothic, 14)
    p.drawString(20 * mm, height - 65 * mm, f'{customer_name}  御中')

    # 宛先下に線
    p.setLineWidth(0.5)
    p.line(20 * mm, height - 67 * mm, 120 * mm, height - 67 * mm)

    # 担当者
    if order.customer.contact_person:
        p.setFont(font_gothic, 9)
        p.drawString(20 * mm, height - 73 * mm, f'ご担当: {order.customer.contact_person} 様')

    # --- 合計金額 ---
    total = order.total
    p.setFont(font_gothic, 14)
    p.drawString(20 * mm, height - 85 * mm, '合計金額')
    p.setFont(font_gothic, 18)
    p.drawString(70 * mm, height - 85 * mm, f'¥{int(total):,}')
    p.setLineWidth(1)
    p.line(20 * mm, height - 88 * mm, 130 * mm, height - 88 * mm)

    # --- 明細テーブル ---
    table_top = height - 100 * mm
    col_x = [20 * mm, 30 * mm, 110 * mm, 135 * mm, 160 * mm]
    col_labels = ['No', '商品名', '数量', '単価', '金額']

    # ヘッダー
    p.setFont(font_gothic, 9)
    p.setFillColorRGB(0.95, 0.95, 0.95)
    p.rect(20 * mm, table_top - 5 * mm, 170 * mm, 7 * mm, fill=1, stroke=0)
    p.setFillColorRGB(0, 0, 0)

    for i, label in enumerate(col_labels):
        if i >= 2:
            p.drawRightString(col_x[i] + 25 * mm, table_top - 3 * mm, label)
        else:
            p.drawString(col_x[i], table_top - 3 * mm, label)

    # ヘッダー線
    p.setLineWidth(0.5)
    p.line(20 * mm, table_top - 5 * mm, 190 * mm, table_top - 5 * mm)

    # 明細行
    items = order.items.all()
    y = table_top - 12 * mm
    p.setFont(font_mincho, 9)

    for idx, item in enumerate(items, 1):
        if y < 40 * mm:
            p.showPage()
            p.setFont(font_mincho, 9)
            y = height - 30 * mm

        p.drawString(col_x[0], y, str(idx))
        p.drawString(col_x[1], y, item.product_name)
        p.drawRightString(col_x[2] + 25 * mm, y, str(item.quantity))
        p.drawRightString(col_x[3] + 25 * mm, y, f'¥{int(item.unit_price):,}')
        p.drawRightString(col_x[4] + 25 * mm, y, f'¥{int(item.subtotal):,}')

        # 行区切り線
        y -= 2 * mm
        p.setLineWidth(0.2)
        p.line(20 * mm, y, 190 * mm, y)
        y -= 5 * mm

    # 合計行
    y -= 3 * mm
    p.setLineWidth(0.8)
    p.line(135 * mm, y + 7 * mm, 190 * mm, y + 7 * mm)
    p.setFont(font_gothic, 10)
    p.drawRightString(col_x[3] + 25 * mm, y, '合計')
    p.drawRightString(col_x[4] + 25 * mm, y, f'¥{int(total):,}')
    p.setLineWidth(0.8)
    p.line(135 * mm, y - 2 * mm, 190 * mm, y - 2 * mm)

    # --- 備考 ---
    if order.notes:
        y -= 15 * mm
        p.setFont(font_gothic, 9)
        p.drawString(20 * mm, y, '備考:')
        p.setFont(font_mincho, 9)
        y -= 5 * mm
        for line in order.notes.split('\n'):
            p.drawString(25 * mm, y, line)
            y -= 4 * mm

    p.showPage()
    p.save()
    buffer.seek(0)
    return buffer
