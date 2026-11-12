"""販売フィー計算書のPDF（見本.xlsx 202610 準拠・A4縦1ページ）。WeasyPrint とフォントは請求書と同じ。"""
import weasyprint
from django.template.loader import render_to_string

from orders.pdf import FONTS_DIR


def yen(value):
    value = int(value)
    return f"−¥{-value:,}" if value < 0 else f"¥{value:,}"


def jdate(d):
    return f"{d.year}年{d.month}月{d.day}日" if d else ""


def rate_label(rate):
    """10.0 → 10、10.5 → 10.5"""
    text = f"{rate:.1f}"
    return text[:-2] if text.endswith(".0") else text


def addressee_font_pt(name):
    # 宛名欄は約100mm。収まらないときは折り返さずに文字を小さくする
    n = len(name) + 3  # 「　御中」
    if n <= 17:
        return 14
    if n <= 21:
        return 12
    if n <= 26:
        return 10
    return 8.5


def table_context(detail):
    """日別の表（PDF・詳細画面で共通）。セルは表示用の文字にしておく。"""
    prices, large_prices = detail["prices"], detail["large_prices"]
    services = detail["service_names"]
    has_others = detail["has_others"]

    def cells(row):
        if not row.get("has_report"):
            n = len(prices) + len(large_prices) + (1 if has_others else 0) + len(services) + 3
            return [""] * n
        out = [str(c) for c in row["counts"]]
        out += [str(c) for c in row["large"]]
        if has_others:
            out.append(yen(row["others"]) if row["others"] else "")
        out += [yen(s) if s else "" for s in row["services"]]
        out += [str(row["sold"]), yen(row["amount"]), str(row["remaining"])]
        return out

    t = detail["totals"]
    total_cells = [str(c) for c in t["counts"]] + [str(c) for c in t["large"]]
    if has_others:
        total_cells.append(yen(t["others"]))
    total_cells += [yen(s) for s in t["services"]]
    total_cells += [str(t["sold"]), yen(t["amount"]), str(t["remaining"])]

    others_line = "　".join(f"{o['name']} ×{o['qty']} {yen(o['amount'])}" for o in detail["others_breakdown"])
    service_lines = []
    for name in services:
        parts = []
        for s in detail["service_breakdown"]:
            if s["name"] != name:
                continue
            what = f"{yen(s['unit'])}/個" if s["flat"] else f"{yen(s['base'])}→{yen(s['price'])}"
            parts.append(f"{what} ×{s['qty']} {yen(s['amount'])}")
        service_lines.append(f"{name}の内訳：" + "　".join(parts))
    extra_cols = len(prices) + len(large_prices) + (1 if has_others else 0) + len(services)
    return {
        "price_heads": [yen(p) for p in prices],
        "large_heads": [f"+{yen(p)}" for p in large_prices],
        "has_others": has_others,
        "service_heads": services,
        "rows": [dict(r, cells=cells(r)) for r in detail["days"]],
        "total_cells": total_cells,
        "others_line": f"その他の内訳：{others_line}" if others_line else "",
        "service_lines": service_lines,
        "compact": extra_cols >= 6,
    }


def statement_context(st, preview=False):
    issuer = st.issuer or {}
    ctx = {
        "st": st,
        "font_dir": FONTS_DIR,
        "issue_date": jdate(st.issue_date),
        "pay_date": jdate(st.pay_date),
        "title_month": f"{st.month.year}年{st.month.month}月分",
        "addressee": st.addressee,
        "addressee_pt": addressee_font_pt(st.addressee),
        "number": st.number,
        "issuer": issuer,
        "seal": issuer.get("seal", ""),
        "rate": rate_label(st.rate),
        "amounts": {k: yen(getattr(st, k)) for k in
                    ("total_sales", "tax_excluded", "tax_amount", "fee_excluded", "fee_tax", "pay_amount")},
        "mark": "プレビュー" if preview else ("取消" if st.is_void else ""),
        "preview": preview,
    }
    ctx.update(table_context(st.detail))
    return ctx


def render_html(st, preview=False):
    return render_to_string("fees/statement.html", statement_context(st, preview))


def generate(st, preview=False):
    return weasyprint.HTML(string=render_html(st, preview)).write_pdf()


def generate_combined(statements):
    """1社1ページを1つのPDFにまとめる（請求書の generate_invoices_combined と同じ仕組み）。"""
    docs = [weasyprint.HTML(string=render_html(st)).render() for st in statements]
    if not docs:
        return b""
    pages = [p for doc in docs for p in doc.pages]
    return docs[0].copy(pages).write_pdf()


def page_count(st, preview=False):
    """1ページに収まっているかの確認用。"""
    return len(weasyprint.HTML(string=render_html(st, preview)).render().pages)
