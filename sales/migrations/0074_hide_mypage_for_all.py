from django.db import migrations, models


def hide_mypage_for_all(apps, schema_editor):
    """マイページは未使用のため全員非表示にする。"""
    UserMenuPermission = apps.get_model("sales", "UserMenuPermission")
    UserMenuPermission.objects.update(can_view_mypage=False)


def noop_reverse(apps, schema_editor):
    """逆操作は行わない（個別に再付与する想定）。"""
    pass


class Migration(migrations.Migration):

    dependencies = [
        ("sales", "0073_usermenupermission_can_view_attendance"),
    ]

    operations = [
        migrations.AlterField(
            model_name="usermenupermission",
            name="can_view_mypage",
            field=models.BooleanField(default=False, verbose_name="マイページ"),
        ),
        migrations.RunPython(hide_mypage_for_all, reverse_code=noop_reverse),
    ]
