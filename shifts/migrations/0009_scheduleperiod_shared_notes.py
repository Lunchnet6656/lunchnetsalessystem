from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ('shifts', '0008_remove_shiftassignment_assignment_user_or_external_staff_and_more'),
    ]
    operations = [
        migrations.AddField(
            model_name='scheduleperiod',
            name='shared_notes',
            field=models.TextField(blank=True, verbose_name='共有事項'),
        ),
    ]
