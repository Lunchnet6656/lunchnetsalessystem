# Generated by Django 5.1.1 on 2024-09-15 05:05

from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ('sales', '0015_alter_dailyreport_total_discount_and_more'),
    ]

    operations = [
        migrations.RenameField(
            model_name='dailyreport',
            old_name='total_sales',
            new_name='total_revenue',
        ),
    ]
