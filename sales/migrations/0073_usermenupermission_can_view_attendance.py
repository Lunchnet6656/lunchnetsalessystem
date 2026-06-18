from django.db import migrations, models


def enable_attendance_for_staff(apps, schema_editor):
    """管理者権限（is_staff）のユーザーは勤怠アプリをデフォルトで表示する。"""
    UserMenuPermission = apps.get_model("sales", "UserMenuPermission")
    UserMenuPermission.objects.filter(user__is_staff=True).update(
        can_view_attendance=True
    )


def disable_attendance(apps, schema_editor):
    UserMenuPermission = apps.get_model("sales", "UserMenuPermission")
    UserMenuPermission.objects.update(can_view_attendance=False)


class Migration(migrations.Migration):

    dependencies = [
        ("sales", "0072_reservation_fields"),
    ]

    operations = [
        migrations.AddField(
            model_name="usermenupermission",
            name="can_view_attendance",
            field=models.BooleanField(default=False, verbose_name="勤怠アプリ"),
        ),
        migrations.RunPython(
            enable_attendance_for_staff, reverse_code=disable_attendance
        ),
    ]
