# Generated by Django 5.1.1 on 2024-09-15 08:05

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('sales', '0018_alter_dailyreport_paypay_and_more'),
    ]

    operations = [
        migrations.AddField(
            model_name='dailyreport',
            name='total_quantity',
            field=models.DecimalField(decimal_places=0, default=0, max_digits=10),
        ),
        migrations.AddField(
            model_name='dailyreport',
            name='total_sales_quantity',
            field=models.DecimalField(decimal_places=0, default=0, max_digits=10),
        ),
    ]
