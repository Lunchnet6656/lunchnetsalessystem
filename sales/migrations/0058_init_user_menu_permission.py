from django.db import migrations


def create_permissions_for_existing_users(apps, schema_editor):
    User = apps.get_model('auth', 'User')
    UserMenuPermission = apps.get_model('sales', 'UserMenuPermission')

    boolean_fields = [
        'can_view_upload', 'can_view_register', 'can_view_mypage',
        'can_view_user_list', 'can_view_location_list', 'can_view_product_list',
        'can_view_item_quantity', 'can_view_others_item', 'can_view_daily_report_list',
        'can_view_performance_data', 'can_view_performance_by_location', 'can_view_menu_history',
        'can_view_daily_report_rol', 'can_view_performance_by_location_rol',
        'can_view_daily_report_form', 'can_view_shift_app',
    ]

    for user in User.objects.all():
        perm = UserMenuPermission(user=user)
        if user.is_staff:
            for field in boolean_fields:
                setattr(perm, field, True)
        # non-staff users get model defaults (already set by __init__)
        perm.save()


class Migration(migrations.Migration):

    dependencies = [
        ('sales', '0057_usermenupermission'),
    ]

    operations = [
        migrations.RunPython(
            create_permissions_for_existing_users,
            reverse_code=migrations.RunPython.noop,
        ),
    ]
