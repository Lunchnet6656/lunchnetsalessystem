# myapp/templatetags/custom_filters.py

from django import template
from django.utils.dateformat import format
import decimal

register = template.Library()

@register.filter
def japanese_date(value):

    if not value:
        return value
    # valueが文字列の場合、日付オブジェクトに変換
    if isinstance(value, str):
        from datetime import datetime
        value = datetime.strptime(value, '%Y-%m-%d').date()

    day_of_week = {
        0: '月',
        1: '火',
        2: '水',
        3: '木',
        4: '金',
        5: '土',
        6: '日',
    }
    formatted_date = format(value, 'Y/m/d')
    weekday = day_of_week[value.weekday()]
    return f"{formatted_date} ({weekday})"

@register.filter
def japanese_weekday(value):
    """ 日付から日本語の曜日1文字を返す """
    weekday_map = ['月', '火', '水', '木', '金', '土', '日']
    return weekday_map[value.weekday()] if hasattr(value, 'weekday') else ''

@register.filter
def yen_format(value):
    """日本円形式でカンマ区切りと￥記号を追加します"""
    try:
        # Decimalでのフォーマットを適用し、￥記号を追加
        return '￥{:,}'.format(decimal.Decimal(value))
    except (ValueError, decimal.InvalidOperation):
        return value

@register.filter
def dict_key(dictionary, key):
    return dictionary.get(key, None)

@register.filter
def get_item(dictionary, key):
    return dictionary.get(key)

@register.filter
def add(value, arg):
    return value + arg

@register.filter
def get_item(dictionary, key):
    return dictionary.get(key)