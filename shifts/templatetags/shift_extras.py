from django import template

register = template.Library()

WEEKDAY_JA = ['月', '火', '水', '木', '金', '土', '日']


@register.filter
def lookup(d, key):
    """Dict lookup by key: {{ mydict|lookup:key }}"""
    if isinstance(d, dict):
        return d.get(key)
    return None


@register.filter
def weekday_ja(date):
    """Return Japanese weekday name for a date object."""
    if date is None:
        return ''
    return WEEKDAY_JA[date.weekday()]
