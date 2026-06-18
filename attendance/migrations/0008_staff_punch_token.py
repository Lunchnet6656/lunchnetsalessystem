"""Staff.punch_token 追加（QR打刻用の個人トークン）。

既存スタッフ全員に新しい UUID4 を発行する。UUIDField を unique=True で
直接追加すると既存行が同じデフォルト値で衝突するため、3段階で行う：
  1) nullable で追加
  2) 各行に固有の UUID を埋める
  3) unique 制約を付ける
"""
import uuid

from django.db import migrations, models


def populate_tokens(apps, schema_editor):
    Staff = apps.get_model("attendance", "Staff")
    for staff in Staff.objects.all():
        staff.punch_token = uuid.uuid4()
        staff.save(update_fields=["punch_token"])


def clear_tokens(apps, schema_editor):
    Staff = apps.get_model("attendance", "Staff")
    Staff.objects.update(punch_token=None)


class Migration(migrations.Migration):

    dependencies = [
        ("attendance", "0007_payslipadjustment"),
    ]

    operations = [
        migrations.AddField(
            model_name="staff",
            name="punch_token",
            field=models.UUIDField(
                default=uuid.uuid4,
                editable=False,
                null=True,
                verbose_name="打刻トークン",
            ),
        ),
        migrations.RunPython(populate_tokens, reverse_code=clear_tokens),
        migrations.AlterField(
            model_name="staff",
            name="punch_token",
            field=models.UUIDField(
                default=uuid.uuid4,
                editable=False,
                unique=True,
                verbose_name="打刻トークン",
            ),
        ),
    ]
