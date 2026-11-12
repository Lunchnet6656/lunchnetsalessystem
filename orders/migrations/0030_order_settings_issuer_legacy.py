"""#206 受注設定の発行元の欄を「旧欄」に改名する（モデル上だけ。DBの列はそのまま残す）。

値は sales.0103 で発行元（IssuerSettings）へ写し済み。列を消すと切り戻したときに
受注の画面が開けなくなるので、次のリリースまで列は残す。
"""
from django.db import migrations, models

FIELDS = [
    ("company_name", models.CharField(default="有限会社ランチサービスネットワーク", max_length=200, db_column="company_name")),
    ("postal_code", models.CharField(default="〒224-0021", max_length=10, db_column="postal_code")),
    ("address", models.CharField(default="横浜市都筑区北山田1-1-18", max_length=300, db_column="address")),
    ("tel", models.CharField(default="045-593-6656", max_length=20, db_column="tel")),
    ("fax", models.CharField(default="045-592-8424", max_length=20, db_column="fax")),
    ("invoice_number", models.CharField(default="T4020002059715", max_length=20, db_column="invoice_number")),
    ("seal_image_data", models.TextField(blank=True, default="", db_column="seal_image_data")),
]


class Migration(migrations.Migration):

    dependencies = [
        ("orders", "0029_customer_price_type_free"),
        ("sales", "0103_issuer_settings_fee_permissions"),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(
            database_operations=[],
            state_operations=[op for name, field in FIELDS for op in (
                migrations.RenameField("ordersettings", name, f"legacy_{name}"),
                migrations.AlterField("ordersettings", f"legacy_{name}", field),
            )],
        ),
    ]
