from django import template

from fees.pdf import rate_label, yen as _yen

register = template.Library()


@register.filter
def yen(value):
    if value is None or value == "":
        return "—"
    return _yen(value)


@register.filter
def rate(value):
    return "—" if value is None else f"{rate_label(value)}%"


@register.filter
def jday(d):
    """10/5（月）"""
    return f"{d.month}/{d.day}（{'月火水木金土日'[d.weekday()]}）"
