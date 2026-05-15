"""売場貼付用QRコードのPDFを生成する。

仕様: .company/engineering/harness/specs/w001-本日の出店状況ページ.md「QR拡張（Sprint 10-12）」

各拠点ごとに2ページ（完売QR・急休みQR の順）。A4縦・QRは中央に約5cm四方。
QRに埋め込むURLは現リクエストのホストから絶対URL化する（dev/prod 自動切替のため）。
日本語フォントは reportlab 内蔵のCID `HeiseiKakuGo-W5` を使う（フォントバンドル不要）。
"""
import io

import qrcode
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.lib.utils import ImageReader
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.cidfonts import UnicodeCIDFont
from reportlab.pdfgen import canvas


JP_FONT_NAME = "HeiseiKakuGo-W5"

_FONT_REGISTERED = False


def _ensure_jp_font():
    global _FONT_REGISTERED
    if not _FONT_REGISTERED:
        pdfmetrics.registerFont(UnicodeCIDFont(JP_FONT_NAME))
        _FONT_REGISTERED = True


def _make_qr_image(url):
    qr = qrcode.QRCode(
        version=None,  # 自動で最小サイズ
        error_correction=qrcode.constants.ERROR_CORRECT_M,  # 中（〜15%復元・印刷の汚れに強い）
        box_size=10,
        border=2,
    )
    qr.add_data(url)
    qr.make(fit=True)
    return qr.make_image(fill_color="black", back_color="white").convert("RGB")


def _draw_page(c, location_name, kind_label, accent_color, url):
    """1ページぶん描画する。

    kind_label: "完売" or "急休み"（タイトル下のラベル）
    accent_color: (r,g,b) 0-1 範囲のタプル
    """
    page_w, page_h = A4

    # 拠点名（最大）— 中央寄せ
    c.setFont(JP_FONT_NAME, 36)
    c.setFillColorRGB(0.18, 0.16, 0.15)  # ink
    c.drawCentredString(page_w / 2, page_h - 35 * mm, location_name)

    # 用途タイトル — アクセント色
    c.setFont(JP_FONT_NAME, 28)
    c.setFillColorRGB(*accent_color)
    c.drawCentredString(page_w / 2, page_h - 55 * mm, f"{kind_label} QR")

    # QR画像（5cm四方・中央）
    qr_size = 50 * mm
    qr_x = (page_w - qr_size) / 2
    qr_y = (page_h - qr_size) / 2 - 10 * mm
    img = _make_qr_image(url)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    buf.seek(0)
    c.drawImage(ImageReader(buf), qr_x, qr_y, qr_size, qr_size)

    # 使い方説明
    c.setFont(JP_FONT_NAME, 14)
    c.setFillColorRGB(0.30, 0.27, 0.25)
    msg_lines = [
        f"このQRをスマートフォンで読み込むと、",
        f"出店状況ページで「{kind_label}」表示に切り替わります。",
        f"1回読み込むだけでOKです。",
    ]
    line_y = qr_y - 16 * mm
    for line in msg_lines:
        c.drawCentredString(page_w / 2, line_y, line)
        line_y -= 7 * mm

    # 注意書き（小さく）
    c.setFont(JP_FONT_NAME, 10)
    c.setFillColorRGB(0.45, 0.42, 0.40)
    notes = [
        "※毎朝8:00 にリセットされます（前日表示は引き継ぎません）。",
        "※平日のみ・8:00以降に有効です。",
    ]
    note_y = line_y - 4 * mm
    for note in notes:
        c.drawCentredString(page_w / 2, note_y, note)
        note_y -= 5 * mm

    # フッター — 貼付後の判別用「拠点名 × 用途」
    c.setFont(JP_FONT_NAME, 12)
    c.setFillColorRGB(*accent_color)
    c.drawCentredString(page_w / 2, 20 * mm, f"{location_name}　／　{kind_label}")


def build_qr_pdf(locations, build_url):
    """QR PDF を生成して bytes を返す。

    locations: SalesLocation のイテラブル
    build_url(token: str, kind: str) -> str: 完全URLを返す callable（dev/prod 切替のため）
    """
    _ensure_jp_font()

    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=A4)
    c.setTitle("ランチネット QRコード")

    # 色定義（style.css の --soldout / --closed と揃える）
    SOLDOUT = (0.76, 0.35, 0.12)  # #c25a1f
    CLOSED = (0.55, 0.52, 0.49)  # #8c857c

    for loc in locations:
        _draw_page(
            c, loc.name, "完売", SOLDOUT,
            build_url(loc.qr_sold_out_token, "sold-out"),
        )
        c.showPage()
        _draw_page(
            c, loc.name, "急休み", CLOSED,
            build_url(loc.qr_closed_token, "closed"),
        )
        c.showPage()

    c.save()
    return buf.getvalue()
