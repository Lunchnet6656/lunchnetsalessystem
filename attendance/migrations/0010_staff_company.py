"""Staff.company を追加し、既存スタッフを業務区分から会社へ自動割当する。

ランチネットは帳簿上3社に分かれる：
  LSN：(有)ランチサービスネットワーク（社員・食堂）
  LN ：(合)ランチネット（販売）
  LF ：(合)ランチファクトリー（製造）

既存スタッフは business_unit から既定割当：
  cafeteria → LSN
  sales     → LN
  その他    → LSN（最も多い母体に倒す）
"""
from django.db import migrations, models


def assign_company(apps, schema_editor):
    Staff = apps.get_model("attendance", "Staff")
    mapping = {"cafeteria": "LSN", "sales": "LN"}
    for staff in Staff.objects.all():
        staff.company = mapping.get(staff.business_unit, "LSN")
        staff.save(update_fields=["company"])


def revert_company(apps, schema_editor):
    Staff = apps.get_model("attendance", "Staff")
    Staff.objects.update(company="LSN")


class Migration(migrations.Migration):

    dependencies = [
        ("attendance", "0009_manualworkhours_box_wash_count_and_more"),
    ]

    operations = [
        migrations.AddField(
            model_name="staff",
            name="company",
            field=models.CharField(
                choices=[
                    ("LSN", "(有)ランチサービスネットワーク"),
                    ("LN", "(合)ランチネット"),
                    ("LF", "(合)ランチファクトリー"),
                ],
                default="LSN",
                help_text="(有)ランチサービスネットワーク=社員/食堂、(合)ランチネット=販売、(合)ランチファクトリー=製造",
                max_length=8,
                verbose_name="会社",
            ),
        ),
        migrations.RunPython(assign_company, reverse_code=revert_company),
    ]
