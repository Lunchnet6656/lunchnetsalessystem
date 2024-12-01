# Generated by Django 5.1.1 on 2024-09-16 09:39

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('sales', '0024_alter_dailyreport_cash_and_more'),
    ]

    operations = [
        migrations.AddField(
            model_name='dailyreport',
            name='updated_at',
            field=models.DateTimeField(auto_now=True),
        ),
        migrations.AlterField(
            model_name='dailyreport',
            name='cash',
            field=models.DecimalField(decimal_places=0, default=0, max_digits=10),
        ),
        migrations.AlterField(
            model_name='dailyreport',
            name='digital_payment',
            field=models.DecimalField(decimal_places=0, default=0, max_digits=10),
        ),
        migrations.AlterField(
            model_name='dailyreport',
            name='paypay',
            field=models.DecimalField(decimal_places=0, default=0, max_digits=10),
        ),
        migrations.AlterField(
            model_name='dailyreport',
            name='temp',
            field=models.CharField(blank=True, max_length=50),
        ),
        migrations.AlterField(
            model_name='dailyreport',
            name='weather',
            field=models.CharField(blank=True, max_length=50),
        ),
    ]
