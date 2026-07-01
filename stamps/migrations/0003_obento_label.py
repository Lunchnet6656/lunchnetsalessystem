"""特典名の「弁当」→「お弁当」表記をDB既存行に反映する。

既に投入済みの RewardTier.label と、発行済み Reward.label（発行時スナップショット）を更新。
「お弁当」を二重化しないよう、直前が「お」でない「弁当」だけ置換する。
"""
import re

from django.db import migrations


_PAT = re.compile(r"(?<!お)弁当")


def _fix(apps, schema_editor):
    for model_name in ("RewardTier", "Reward"):
        Model = apps.get_model("stamps", model_name)
        for obj in Model.objects.filter(label__contains="弁当"):
            new = _PAT.sub("お弁当", obj.label)
            if new != obj.label:
                obj.label = new
                obj.save(update_fields=["label"])


def _noop(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ("stamps", "0002_seed_reward_tiers"),
    ]

    operations = [
        migrations.RunPython(_fix, _noop),
    ]
