"""特典段階（5pt/10pt/20pt）の既定を投入する。

仕様: .company/engineering/harness/specs/lunchnetsale-スタンプカード来店計測-MVP.md（§4.3）
5pt=50円引き／10pt=お弁当無料（1個目）／20pt=お弁当無料（2個目・打ち止め）。
benefit_cost は損益・コスト集計用の想定値（実装には影響しない／管理画面で変更可）。
"""
from django.db import migrations


TIERS = [
    dict(threshold_pt=5, kind="discount", label="50円引き",
         discount_yen=50, benefit_cost=50, is_cap=False, active=True),
    dict(threshold_pt=10, kind="free", label="お弁当1個無料",
         discount_yen=0, benefit_cost=293, is_cap=False, active=True),
    dict(threshold_pt=20, kind="free", label="お弁当1個無料（2個目・打ち止め）",
         discount_yen=0, benefit_cost=293, is_cap=True, active=True),
]


def _seed(apps, schema_editor):
    RewardTier = apps.get_model("stamps", "RewardTier")
    for t in TIERS:
        RewardTier.objects.get_or_create(threshold_pt=t["threshold_pt"], defaults=t)


def _unseed(apps, schema_editor):
    RewardTier = apps.get_model("stamps", "RewardTier")
    RewardTier.objects.filter(threshold_pt__in=[t["threshold_pt"] for t in TIERS]).delete()


class Migration(migrations.Migration):

    dependencies = [
        ("stamps", "0001_initial"),
    ]

    operations = [
        migrations.RunPython(_seed, _unseed),
    ]
