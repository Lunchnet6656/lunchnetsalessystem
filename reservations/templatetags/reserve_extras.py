"""予約画面用テンプレートフィルタ。

`LANGUAGE_CODE='en-us'` のため Django の `date:"D"` は曜日が英語（Thu）になる。
グローバルのロケールを変えると全画面に波及するので、予約画面だけ
ロケール非依存で日本語曜日を出す薄いフィルタを用意する。
"""
from django import template

register = template.Library()

_DOW = "月火水木金土日"  # Python の weekday()（月=0）に対応


@register.filter
def jp_date(d):
    """日付を「6月11日（木）」形式で返す（曜日は常に日本語）。"""
    if not d:
        return ""
    return f"{d.month}月{d.day}日（{_DOW[d.weekday()]}）"


@register.filter
def jp_weekday(d):
    """曜日一文字（木）だけを返す。"""
    if not d:
        return ""
    return _DOW[d.weekday()]
