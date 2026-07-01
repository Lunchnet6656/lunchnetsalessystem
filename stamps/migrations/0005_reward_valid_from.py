"""特典（クーポン）に利用開始日 valid_from を追加する。

仕様: .company/engineering/harness/specs/lunchnetsale-スタンプカード来店計測-MVP.md
獲得した来店ではクーポンを使えないようにするため（10回購入で1個無料の「10個目」を
精算前に無料化されない）、利用開始日＝獲得の翌日を持たせる。

既存行（あれば）は発行日(issued_at)の翌日で埋める。3段構え（null許容→backfill→非null）。
"""
from datetime import timedelta

from django.db import migrations, models
from django.utils import timezone


def _backfill(apps, schema_editor):
    Reward = apps.get_model("stamps", "Reward")
    for r in Reward.objects.filter(valid_from__isnull=True):
        issued = timezone.localtime(r.issued_at).date() if r.issued_at else r.expires_on
        r.valid_from = issued + timedelta(days=1)
        r.save(update_fields=["valid_from"])


def _noop(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ("stamps", "0004_alter_rewardtier_benefit_cost_and_more"),
    ]

    operations = [
        migrations.AddField(
            model_name="reward",
            name="valid_from",
            field=models.DateField(
                null=True,
                verbose_name="利用開始日",
                help_text="この日から使える。獲得の翌日＝次回来店から（獲得した来店では使えない）。",
            ),
        ),
        migrations.RunPython(_backfill, _noop),
        migrations.AlterField(
            model_name="reward",
            name="valid_from",
            field=models.DateField(
                verbose_name="利用開始日",
                help_text="この日から使える。獲得の翌日＝次回来店から（獲得した来店では使えない）。",
            ),
        ),
    ]
