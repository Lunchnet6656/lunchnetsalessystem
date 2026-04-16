from django.db import migrations, models


def cleanup_duplicate_entries(apps, schema_editor):
    """
    DailyReportEntry の (report_id, product_no) の重複を解消する。
    同一ペアが複数ある場合、最大 id （最新）のみ残して他を削除する。
    """
    DailyReportEntry = apps.get_model('sales', 'DailyReportEntry')
    from django.db.models import Max

    keep_ids = (
        DailyReportEntry.objects
        .values('report_id', 'product_no')
        .annotate(max_id=Max('id'))
        .values_list('max_id', flat=True)
    )
    deleted_count, _ = DailyReportEntry.objects.exclude(id__in=list(keep_ids)).delete()
    if deleted_count:
        print(f"DailyReportEntry: {deleted_count} 件の重複エントリを削除しました。")


class Migration(migrations.Migration):

    dependencies = [
        ('sales', '0065_usermenupermission_can_view_quest'),
    ]

    operations = [
        migrations.RunPython(cleanup_duplicate_entries, migrations.RunPython.noop),
        migrations.AlterModelOptions(
            name='dailyreportentry',
            options={'ordering': ['product_no']},
        ),
        migrations.AlterUniqueTogether(
            name='dailyreportentry',
            unique_together={('report', 'product_no')},
        ),
    ]
