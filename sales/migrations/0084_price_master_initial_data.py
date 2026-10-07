"""価格と割引のマスタの初期データ。

- 値段の種類：★特選／通常／お手頃（チャーハン・オムライスなど）／大盛り
- 価格表：2026-10-01 の値上げ後の値段（本番 2026-10-07週のメニューの値段から作成）
- 割引項目：ご飯（なし▲100／追加＋100）、割引・返金（▲50／▲100）。日付はリポジトリ最初のコミット日から

メニュー（Product.rank）にはまだ種類を入れないので、この時点では値段の決まり方は変わらない。
"""
import datetime

from django.db import migrations

RANKS = [
    # (name, is_bento, sort_order)
    ("特選", True, 1),
    ("通常", True, 2),
    ("お手頃", True, 3),
    ("大盛り", False, 4),
]

PRICE_TABLE_2026_10 = {
    # rank: (A, B, C)
    "特選": (750, 700, 700),
    "通常": (700, 650, 600),
    "お手頃": (650, 650, 600),
    "大盛り": (50, 50, 50),
}

SINCE = datetime.date(2024, 12, 1)
DISCOUNT_ITEMS = [
    # (group, label, direction, amount, sort_order, legacy_field, csv_label)
    ("rice", "なし", "minus", 100, 1, "no_rice_quantity", "ご飯なし"),
    ("rice", "追加", "plus", 100, 2, "extra_rice_quantity", "ご飯追加"),
    ("refund", "▲50円", "minus", 50, 1, "discount_50", "割引・返金50円"),
    ("refund", "▲100円", "minus", 100, 2, "discount_100", "割引・返金100円"),
]


def forwards(apps, schema_editor):
    PriceRank = apps.get_model("sales", "PriceRank")
    PriceTable = apps.get_model("sales", "PriceTable")
    PriceTableCell = apps.get_model("sales", "PriceTableCell")
    DiscountItem = apps.get_model("sales", "DiscountItem")

    ranks = {}
    for name, is_bento, order in RANKS:
        ranks[name], _ = PriceRank.objects.get_or_create(
            name=name, defaults={"is_bento": is_bento, "sort_order": order},
        )

    table, _ = PriceTable.objects.get_or_create(
        valid_from=datetime.date(2026, 10, 1), defaults={"note": "2026年10月 値上げ"},
    )
    for rank_name, prices in PRICE_TABLE_2026_10.items():
        for pattern, price in zip(("A", "B", "C"), prices):
            PriceTableCell.objects.get_or_create(
                table=table, rank=ranks[rank_name], pattern=pattern, defaults={"price": price},
            )

    for group, label, direction, amount, order, legacy, csv_label in DISCOUNT_ITEMS:
        DiscountItem.objects.get_or_create(
            group=group, label=label, valid_from=SINCE,
            defaults={
                "direction": direction, "amount": amount, "sort_order": order,
                "legacy_field": legacy, "csv_label": csv_label,
            },
        )


def backwards(apps, schema_editor):
    apps.get_model("sales", "DiscountItem").objects.filter(valid_from=SINCE).delete()
    apps.get_model("sales", "PriceTable").objects.filter(valid_from=datetime.date(2026, 10, 1)).delete()
    apps.get_model("sales", "PriceRank").objects.filter(name__in=[r[0] for r in RANKS]).delete()


class Migration(migrations.Migration):

    dependencies = [
        ("sales", "0083_price_master"),
    ]

    operations = [
        migrations.RunPython(forwards, backwards),
    ]
