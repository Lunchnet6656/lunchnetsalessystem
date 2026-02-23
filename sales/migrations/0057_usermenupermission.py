from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('sales', '0056_saleslocation_excluded_from_shift'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name='UserMenuPermission',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('can_view_upload', models.BooleanField(default=False, verbose_name='データアップロード')),
                ('can_view_register', models.BooleanField(default=False, verbose_name='新規ユーザー登録')),
                ('can_view_mypage', models.BooleanField(default=True, verbose_name='マイページ')),
                ('can_view_user_list', models.BooleanField(default=False, verbose_name='ユーザー管理')),
                ('can_view_location_list', models.BooleanField(default=False, verbose_name='販売場所データ確認')),
                ('can_view_product_list', models.BooleanField(default=False, verbose_name='メニューデータ確認')),
                ('can_view_item_quantity', models.BooleanField(default=False, verbose_name='持参数データ確認')),
                ('can_view_others_item', models.BooleanField(default=False, verbose_name='その他の項目設定')),
                ('can_view_daily_report_list', models.BooleanField(default=False, verbose_name='日計表 送信結果（管理者）')),
                ('can_view_performance_data', models.BooleanField(default=False, verbose_name='日別実績集計データ')),
                ('can_view_performance_by_location', models.BooleanField(default=False, verbose_name='販売場所別実績（管理者）')),
                ('can_view_menu_history', models.BooleanField(default=False, verbose_name='メニュー別販売履歴')),
                ('can_view_daily_report_rol', models.BooleanField(default=True, verbose_name='日計表 送信結果（一般）')),
                ('can_view_performance_by_location_rol', models.BooleanField(default=True, verbose_name='販売場所別実績データ（一般）')),
                ('can_view_daily_report_form', models.BooleanField(default=True, verbose_name='日計表入力フォーム')),
                ('can_view_shift_app', models.BooleanField(default=True, verbose_name='シフトアプリ')),
                ('user', models.OneToOneField(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name='menu_permission',
                    to=settings.AUTH_USER_MODEL,
                )),
            ],
        ),
    ]
