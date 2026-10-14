"""直行直帰の画面で時間を表示するフィルター（画面設計 §0.2）。"""
from django import template
from django.utils.html import format_html

from sales.direct_return import format_hours

register = template.Library()


@register.filter
def hours(value):
    """4 → 4.00、None → 空。"""
    return format_hours(value)


@register.filter
def plus_hours(value):
    """早出・残業：0 は「—」、あれば「+0.50」。"""
    if not value:
        return format_html('<span class="dr-zero">—</span>')
    return f"+{format_hours(value)}"


@register.filter
def minus_hours(value):
    """遅刻・早退：0 は「—」、あれば赤字の「−0.25」。"""
    if not value:
        return format_html('<span class="dr-zero">—</span>')
    return format_html('<span class="dr-minus">−{}</span>', format_hours(value))
