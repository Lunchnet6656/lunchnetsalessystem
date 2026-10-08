"""判定ルールの初期データ（初めて見るメニューの仮の決め方）。

値段の種類と容器は別々に判定する（2026-10-07 しょうへい回答：一体型でもガパオライス・タコライスは通常の値段）。
どのルールにも当たらなければ「通常・黒容器」。
"""
from django.db import migrations

RULES = [
    # (keyword, rank name or None, container)
    ("★", "特選", "赤容器"),
    ("大盛りごはん", "大盛り", "ご飯容器"),
    ("チャーハン", "お手頃", "一体型"),
    ("オムライス", "お手頃", "一体型"),
    ("ガパオ", None, "一体型"),
    ("タコライス", None, "一体型"),
]


def forwards(apps, schema_editor):
    PriceRank = apps.get_model("sales", "PriceRank")
    ClassifyRule = apps.get_model("sales", "ClassifyRule")
    ranks = {r.name: r for r in PriceRank.objects.all()}
    for order, (keyword, rank_name, container) in enumerate(RULES, start=1):
        ClassifyRule.objects.get_or_create(
            keyword=keyword,
            defaults={"rank": ranks.get(rank_name) if rank_name else None, "container": container,
                      "sort_order": order},
        )


def backwards(apps, schema_editor):
    apps.get_model("sales", "ClassifyRule").objects.filter(keyword__in=[r[0] for r in RULES]).delete()


class Migration(migrations.Migration):

    dependencies = [
        ("sales", "0087_menu_dictionary"),
    ]

    operations = [
        migrations.RunPython(forwards, backwards),
    ]
