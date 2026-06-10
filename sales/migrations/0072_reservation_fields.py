"""予約システム（個人セルフ取り置き）用フィールドを SalesLocation に追加する。

仕様: .company/engineering/harness/specs/w001-予約システム-要件定義.md
タスク: #143

qr_reserve_token は unique=True だが、既存行に同一初期値が入ると一意制約違反になるため、
0071 と同じ3段構え（null許容で追加 → RunPython で固有トークン生成 → unique で締める）で適用する。
"""
import secrets

from django.db import migrations, models

from sales.models import _generate_qr_token


def _new_token():
    return secrets.token_urlsafe(16)


def _populate_reserve_tokens(apps, schema_editor):
    SalesLocation = apps.get_model("sales", "SalesLocation")
    for loc in SalesLocation.objects.all():
        loc.qr_reserve_token = _new_token()
        loc.save(update_fields=["qr_reserve_token"])


def _noop_reverse(apps, schema_editor):
    # トークンは戻せない（カラムごと削除されるため）
    pass


class Migration(migrations.Migration):

    dependencies = [
        ("sales", "0071_qr_override_fields"),
    ]

    operations = [
        migrations.AddField(
            model_name="saleslocation",
            name="reservation_enabled",
            field=models.BooleanField(
                default=False,
                verbose_name="予約受付",
                help_text="ON の拠点だけセルフ予約を受け付ける。テスト店舗だけ ON にするキルスイッチ。",
            ),
        ),
        migrations.AddField(
            model_name="saleslocation",
            name="default_product_cap",
            field=models.IntegerField(
                default=10,
                verbose_name="1メニューあたりの予約上限/日",
                help_text="1メニューを1日に何個まで予約で確保できるか。既定10。",
            ),
        ),
        # token: いったん null許容＋非ユニークで追加（既存行はNULL）
        migrations.AddField(
            model_name="saleslocation",
            name="qr_reserve_token",
            field=models.CharField(
                max_length=32, null=True,
                verbose_name="予約QRトークン",
            ),
        ),
        # 既存行に固有トークンを生成
        migrations.RunPython(_populate_reserve_tokens, _noop_reverse),
        # 締める：null=False, unique=True, db_index=True, default=callable
        migrations.AlterField(
            model_name="saleslocation",
            name="qr_reserve_token",
            field=models.CharField(
                max_length=32, unique=True, db_index=True, default=_generate_qr_token,
                verbose_name="予約QRトークン",
            ),
        ),
    ]
