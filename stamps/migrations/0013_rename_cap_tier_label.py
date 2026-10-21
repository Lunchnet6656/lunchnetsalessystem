"""20個目の特典名から「・打ち止め」を外す（お客様向けの表示では不要なため）。

管理画面で名前を変えている場合は触らない（旧名のままのものだけ置き換える）。
発行済みでまだ使っていないクーポンも同じ名前に揃える。使用済み・期限切れは履歴なのでそのまま。
"""
from django.db import migrations

OLD = "お弁当1個無料（2個目・打ち止め）"
NEW = "お弁当1個無料（2個目）"


def forward(apps, schema_editor):
    apps.get_model("stamps", "RewardTier").objects.filter(label=OLD).update(label=NEW)
    apps.get_model("stamps", "Reward").objects.filter(label=OLD, status="issued").update(label=NEW)


def backward(apps, schema_editor):
    apps.get_model("stamps", "RewardTier").objects.filter(label=NEW).update(label=OLD)
    apps.get_model("stamps", "Reward").objects.filter(label=NEW, status="issued").update(label=OLD)


class Migration(migrations.Migration):
    dependencies = [
        ("stamps", "0012_messagescenario_stampconfig_menu_url_followupsendlog"),
    ]
    operations = [migrations.RunPython(forward, backward)]
