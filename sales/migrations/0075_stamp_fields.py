"""スタンプカード（来店計測）用フィールドを SalesLocation に追加する。

仕様: .company/engineering/harness/specs/lunchnetsale-スタンプカード来店計測-MVP.md

qr_stamp_token は unique=True なので、0072 と同じ3段構え
（null許容で追加 → RunPython で固有トークン生成 → unique で締める）で適用する。
"""
import secrets

from django.db import migrations, models

from sales.models import _generate_qr_token


def _new_token():
    return secrets.token_urlsafe(16)


def _populate_stamp_tokens(apps, schema_editor):
    SalesLocation = apps.get_model("sales", "SalesLocation")
    for loc in SalesLocation.objects.all():
        loc.qr_stamp_token = _new_token()
        loc.save(update_fields=["qr_stamp_token"])


def _noop_reverse(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ("sales", "0074_hide_mypage_for_all"),
    ]

    operations = [
        migrations.AddField(
            model_name="saleslocation",
            name="stamp_enabled",
            field=models.BooleanField(
                default=False,
                verbose_name="スタンプ受付",
                help_text="ON の店舗だけスタンプを受け付ける。パイロット店舗だけ ON にするキルスイッチ。",
            ),
        ),
        migrations.AddField(
            model_name="saleslocation",
            name="stamp_open_time",
            field=models.TimeField(
                null=True, blank=True,
                verbose_name="スタンプ出店開始時刻",
                help_text="空＝全店共通の既定（11:00）。本店など製造中にも客が来る店舗は個別に早めに設定。",
            ),
        ),
        migrations.AddField(
            model_name="saleslocation",
            name="stamp_close_time",
            field=models.TimeField(
                null=True, blank=True,
                verbose_name="スタンプ出店終了時刻",
                help_text="空＝全店共通の既定（13:30）。",
            ),
        ),
        # token: いったん null許容＋非ユニークで追加（既存行はNULL）
        migrations.AddField(
            model_name="saleslocation",
            name="qr_stamp_token",
            field=models.CharField(
                max_length=32, null=True,
                verbose_name="スタンプQRトークン",
            ),
        ),
        # 既存行に固有トークンを生成
        migrations.RunPython(_populate_stamp_tokens, _noop_reverse),
        # 締める：null=False, unique=True, db_index=True, default=callable
        migrations.AlterField(
            model_name="saleslocation",
            name="qr_stamp_token",
            field=models.CharField(
                max_length=32, unique=True, db_index=True, default=_generate_qr_token,
                verbose_name="スタンプQRトークン",
            ),
        ),
    ]
