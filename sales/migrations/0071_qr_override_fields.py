"""QRコード由来の本日上書き＋拠点別QRトークンを追加する。

仕様書: .company/engineering/harness/specs/w001-本日の出店状況ページ.md「QR拡張（Sprint 10-12）」
タスク: #80

トークンは unique=True だが、既存行に対して同じ初期値が入ると一意制約違反になるため、
1) null=True, unique=False で追加 → 2) RunPython で既存行ごとに固有トークンを生成 →
3) AlterField で null=False, unique=True に締める の3段構えで適用する。
"""
import secrets

from django.db import migrations, models

# 締めの AlterField の default を models 側と完全一致させるため、関数を import する。
# RunPython では historical model（apps.get_model）越しなのでこれは安全（モデルの実体に依存しない）。
from sales.models import _generate_qr_token


def _new_token():
    return secrets.token_urlsafe(16)


def _populate_tokens(apps, schema_editor):
    SalesLocation = apps.get_model("sales", "SalesLocation")
    for loc in SalesLocation.objects.all():
        loc.qr_sold_out_token = _new_token()
        loc.qr_closed_token = _new_token()
        loc.save(update_fields=["qr_sold_out_token", "qr_closed_token"])


def _noop_reverse(apps, schema_editor):
    # トークンは戻せない（カラムごと削除されるため）
    pass


class Migration(migrations.Migration):

    dependencies = [
        ("sales", "0070_saleslocation_excluded_from_public_status"),
    ]

    operations = [
        migrations.AddField(
            model_name="saleslocation",
            name="today_override",
            field=models.CharField(
                blank=True, default="", max_length=10,
                choices=[("", "なし"), ("sold_out", "完売"), ("closed", "お休み")],
                verbose_name="本日の上書き",
                help_text="QRコードから設定される今日のステータス上書き。空＝通常表示。",
            ),
        ),
        migrations.AddField(
            model_name="saleslocation",
            name="today_override_date",
            field=models.DateField(
                blank=True, null=True,
                verbose_name="本日の上書き設定日",
                help_text="today_override を設定したJST日付。今日と一致する場合のみ有効。",
            ),
        ),
        migrations.AddField(
            model_name="saleslocation",
            name="qr_enabled",
            field=models.BooleanField(
                default=False,
                verbose_name="QR有効",
                help_text="OFF のときは QR スキャンしても上書きを受け付けない（パイロット運用用のキルスイッチ）。",
            ),
        ),
        migrations.AddField(
            model_name="saleslocation",
            name="last_qr_publish_at",
            field=models.DateTimeField(
                blank=True, null=True,
                verbose_name="最終QR起因publish時刻",
                help_text="QR起因で Cloudflare publish した最終時刻。60秒スロットルの判定に使う。",
            ),
        ),
        # tokens: いったん null許容＋非ユニークで追加（既存行はNULL）
        migrations.AddField(
            model_name="saleslocation",
            name="qr_sold_out_token",
            field=models.CharField(
                max_length=32, null=True,
                verbose_name="完売QRトークン",
            ),
        ),
        migrations.AddField(
            model_name="saleslocation",
            name="qr_closed_token",
            field=models.CharField(
                max_length=32, null=True,
                verbose_name="急休みQRトークン",
            ),
        ),
        # 既存行に対して固有トークンを生成
        migrations.RunPython(_populate_tokens, _noop_reverse),
        # 締める：null=False, unique=True, default=callable, db_index=True
        migrations.AlterField(
            model_name="saleslocation",
            name="qr_sold_out_token",
            field=models.CharField(
                max_length=32, unique=True, db_index=True, default=_generate_qr_token,
                verbose_name="完売QRトークン",
            ),
        ),
        migrations.AlterField(
            model_name="saleslocation",
            name="qr_closed_token",
            field=models.CharField(
                max_length=32, unique=True, db_index=True, default=_generate_qr_token,
                verbose_name="急休みQRトークン",
            ),
        ),
    ]
