"""#206 発行元情報を受注設定から切り出す＋販売フィー計算書の権限。

既存の受注設定（会社名・住所・TEL/FAX・登録番号・社印）をそのまま発行元へ写す。
請求書・納品書・領収書のPDFは発行元から読むようになるが、写した値なので出力は変わらない。
"""
from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion

COPY_FIELDS = [
    ("company_name", "company_name"),
    ("postal_code", "postal_code"),
    ("address", "address"),
    ("tel", "tel"),
    ("fax", "fax"),
    ("invoice_number", "registration_number"),
    ("seal_image_data", "seal_image_data"),
]


def copy_from_order_settings(apps, schema_editor):
    OrderSettings = apps.get_model("orders", "OrderSettings")
    IssuerSettings = apps.get_model("sales", "IssuerSettings")
    src = OrderSettings.objects.filter(pk=1).first()
    if src is None:
        return  # 受注設定を一度も保存していない環境は既定値（同じ初期値）のまま
    IssuerSettings.objects.update_or_create(
        pk=1, defaults={dst: getattr(src, name) for name, dst in COPY_FIELDS})


class Migration(migrations.Migration):

    dependencies = [
        ("sales", "0102_item_quantity_edit"),
        ("orders", "0029_customer_price_type_free"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="IssuerSettings",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("company_name", models.CharField(default="有限会社ランチサービスネットワーク", max_length=200, verbose_name="会社名")),
                ("postal_code", models.CharField(blank=True, default="〒224-0021", max_length=10, verbose_name="郵便番号")),
                ("address", models.CharField(blank=True, default="横浜市都筑区北山田1-1-18", max_length=300, verbose_name="住所")),
                ("tel", models.CharField(blank=True, default="045-593-6656", max_length=20, verbose_name="TEL")),
                ("fax", models.CharField(blank=True, default="045-592-8424", max_length=20, verbose_name="FAX")),
                ("registration_number", models.CharField(blank=True, default="T4020002059715", max_length=20, verbose_name="登録番号")),
                ("show_registration_on_fee", models.BooleanField(default=False, verbose_name="計算書に登録番号を載せる")),
                ("seal_image_data", models.TextField(blank=True, default="", verbose_name="社印画像")),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("updated_by", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL,
                                                 related_name="+", to=settings.AUTH_USER_MODEL)),
            ],
            options={"verbose_name": "発行元", "verbose_name_plural": "発行元"},
        ),
        migrations.RunPython(copy_from_order_settings, migrations.RunPython.noop),
        migrations.AddField(
            model_name="usermenupermission",
            name="can_view_fees",
            field=models.BooleanField(default=False, verbose_name="販売フィー計算書（閲覧）"),
        ),
        migrations.AddField(
            model_name="usermenupermission",
            name="can_issue_fees",
            field=models.BooleanField(default=False, verbose_name="販売フィー計算書（発行）"),
        ),
    ]
