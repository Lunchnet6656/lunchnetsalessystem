"""持参数の修正の紙（58mm幅のレシートプリンター用）を作る。

保存ごとに PrintJob を1件残し、その日の分を1枚の画像にまとめる（持参数の画面の［印刷内容］で見る）。
安いプリンターは漢字を持っていない機種が多いので、文字は画像にしてから送る（escpos_bytes・GS v 0 のラスター）。
プリンターをつないだら、事務所PCの中継がこのバイト列をそのまま流すだけでよい（#208・プリンターは保留中）。
"""
from pathlib import Path

from django.conf import settings
from PIL import Image, ImageDraw, ImageFont

from sales.models import ItemQuantityUpload, PrintJob

PAPER_DOTS = 384  # 58mm紙の印字幅（203dpi）
MARGIN = 8
WEEKDAYS = "月火水木金土日"
FONT_PATH = Path(settings.BASE_DIR) / "orders" / "static" / "orders" / "fonts" / "ipaexg.ttf"
BAND = 128  # ラスターを分けて送る高さ（バッファの小さい機種のため）


def _font(size):
    return ImageFont.truetype(str(FONT_PATH), size)


def _wrap(draw, text, font, width):
    """幅に収まるよう1文字ずつ折り返す（日本語は単語の区切りが無いので）。"""
    lines, cur = [], ""
    for ch in text:
        if draw.textlength(cur + ch, font=font) > width and cur:
            lines.append(cur)
            cur = ch
        else:
            cur += ch
    return lines + ([cur] if cur else [])


def _diff(before, after):
    d = after - before
    return "±0" if d == 0 else (f"+{d}" if d > 0 else f"−{-d}")


def create_job(target_date, changes):
    """changes = [(売り場名, メニュー名, 前の数, 今の数)]。1回の保存で1件。"""
    if not changes:
        return None
    return PrintJob.objects.create(target_date=target_date, lines=[
        {"location": loc, "product": product, "before": before, "after": after}
        for loc, product, before, after in changes
    ])


def day_jobs(d):
    """その日の紙に載せる保存。振分表から送り直す前の修正は上書きされて消えているので入れない。"""
    jobs = PrintJob.objects.filter(target_date=d)
    upload = ItemQuantityUpload.objects.filter(target_date=d, ok=True).first()
    if upload:
        jobs = jobs.filter(created_at__gte=upload.received_at)
    return list(jobs)


def render_image(d, groups):
    """1枚分の画像（白黒・幅384ドット）。groups＝保存ごとの行のリスト。保存の区切りは太線、行の区切りは細線。"""
    width = PAPER_DOTS - MARGIN * 2
    head, loc_f, menu_f, num_f = _font(20), _font(28), _font(22), _font(32)
    scratch = ImageDraw.Draw(Image.new("1", (1, 1)))
    blocks = [("text", f"{d.month}/{d.day}({WEEKDAYS[d.weekday()]})分 持参数の修正", head, 0)]
    if not any(groups):
        blocks += [("rule", 2), ("text", "修正なし", menu_f, 0)]
    for lines in groups:
        blocks.append(("rule", 3))
        for i, line in enumerate(lines):
            if i:
                blocks.append(("dots", 1))
            blocks += [("text", t, loc_f, 1) for t in _wrap(scratch, line["location"], loc_f, width)]
            blocks += [("text", t, menu_f, 0) for t in _wrap(scratch, line["product"], menu_f, width)]
            blocks.append(("text", f"{line['before']} → {line['after']}（{_diff(line['before'], line['after'])}）",
                           num_f, 1))

    def height(b):
        if b[0] != "text":
            return 14
        box = b[2].getbbox("国")
        return box[3] - box[1] + 12

    img = Image.new("1", (PAPER_DOTS, MARGIN * 2 + sum(height(b) for b in blocks)), 1)
    draw = ImageDraw.Draw(img)
    y = MARGIN
    for b in blocks:
        h = height(b)
        mid = y + h // 2
        if b[0] == "rule":
            draw.line([(MARGIN, mid), (PAPER_DOTS - MARGIN, mid)], fill=0, width=b[1])
        elif b[0] == "dots":
            for x in range(MARGIN, PAPER_DOTS - MARGIN, 8):
                draw.line([(x, mid), (x + 3, mid)], fill=0, width=1)
        else:
            _, text, font, bold = b
            draw.text((MARGIN, y - font.getbbox("国")[1] + 6), text, font=font, fill=0,
                      stroke_width=bold, stroke_fill=0)
        y += h
    return img


def day_image(d):
    return render_image(d, [job.lines for job in day_jobs(d)])


def escpos_bytes(img):
    """ESC/POS：初期化 → 画像（GS v 0 を BAND ドットずつ）→ 紙送り → カット（カッターの無い機種は無視される）。"""
    width_bytes = PAPER_DOTS // 8
    out = bytearray(b"\x1b@")
    for top in range(0, img.height, BAND):
        band = img.crop((0, top, PAPER_DOTS, min(top + BAND, img.height)))
        h = band.height
        # PIL の "1" は 1=白。ESC/POS は 1=黒なので反転する
        data = bytes(b ^ 0xFF for b in band.tobytes())
        out += b"\x1dv0\x00" + bytes([width_bytes & 0xFF, width_bytes >> 8, h & 0xFF, h >> 8]) + data
    out += b"\n" * 4 + b"\x1dV\x42\x00"
    return bytes(out)
