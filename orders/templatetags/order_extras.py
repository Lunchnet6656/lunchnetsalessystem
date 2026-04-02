from django import template

register = template.Library()

_PAYMENT_COLORS = [
    'bg-indigo-100 text-indigo-700',
    'bg-emerald-100 text-emerald-700',
    'bg-amber-100 text-amber-700',
    'bg-rose-100 text-rose-700',
    'bg-sky-100 text-sky-700',
    'bg-violet-100 text-violet-700',
    'bg-teal-100 text-teal-700',
    'bg-pink-100 text-pink-700',
]


@register.filter
def payment_badge_color(payment_method):
    """支払い方法のpkに基づいてTailwindバッジカラークラスを返す"""
    if not payment_method:
        return 'bg-gray-100 text-gray-400'
    return _PAYMENT_COLORS[payment_method.pk % len(_PAYMENT_COLORS)]
