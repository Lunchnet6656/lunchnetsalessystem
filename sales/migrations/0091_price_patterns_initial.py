"""価格の一元化（S5）の初期データ。
- 価格パターン A/B/C を作る（これまでコードに固定していたもの）
- 値段の種類に役割を付ける（名前を変えても古いメニューの判定が壊れないように）
- 独自価格の顧客の3つの金額（★なし・★あり・大盛り）を、顧客別の価格表に移す。
  お手頃は★なしと同じ金額にする（請求額が勝手に変わらないように・2026-10-13 決定 D3）
顧客の旧3欄は消さない（1か月様子を見てから削除を判断する）。3欄とも空の顧客は価格表を作らない。
"""
import datetime

from django.db import migrations

MIGRATED_FROM = datetime.date(2020, 1, 1)
ROLE_BY_NAME = (("特選", "star"), ("通常", "normal"), ("大盛り", "large"))


def forward(apps, schema_editor):
    PricePattern = apps.get_model("sales", "PricePattern")
    PriceRank = apps.get_model("sales", "PriceRank")
    PriceTable = apps.get_model("sales", "PriceTable")
    PriceTableCell = apps.get_model("sales", "PriceTableCell")
    Customer = apps.get_model("orders", "Customer")

    for order, code in enumerate("ABC", start=1):
        PricePattern.objects.get_or_create(code=code, defaults={"name": f"価格{code}", "sort_order": order})

    for rank in PriceRank.objects.all():
        for word, role in ROLE_BY_NAME:
            if word in rank.name and not PriceRank.objects.filter(role=role).exists():
                rank.role = role
                rank.save(update_fields=["role"])
                break

    ranks = list(PriceRank.objects.all())
    for customer in Customer.objects.filter(price_type="CUSTOM"):
        if PriceTable.objects.filter(customer=customer).exists():
            continue
        values = {"star": customer.custom_price_star, "normal": customer.custom_price_normal,
                  "large": customer.custom_price_large}
        # 3欄とも空（主に仕出しの顧客＝注文ごとに値段を入れる）は作らない。顧客の画面で最初の値段を入れられるようにするため
        if all(v is None for v in values.values()):
            continue
        table = PriceTable.objects.create(customer=customer, valid_from=MIGRATED_FROM,
                                          note="移行（顧客画面の独自価格）")
        cells = []
        for rank in ranks:
            role = rank.role or ("normal" if rank.is_bento else "")
            value = values.get(role)
            if value is not None and int(value) >= 1:
                cells.append(PriceTableCell(table=table, rank=rank, pattern="", price=int(value)))
        PriceTableCell.objects.bulk_create(cells)


def backward(apps, schema_editor):
    apps.get_model("sales", "PriceTable").objects.filter(customer__isnull=False).delete()
    apps.get_model("sales", "PricePattern").objects.all().delete()


class Migration(migrations.Migration):

    dependencies = [
        ("sales", "0090_price_patterns_customer_tables"),
    ]

    operations = [migrations.RunPython(forward, backward)]
