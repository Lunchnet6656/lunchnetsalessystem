# Generated by Django 5.1.1 on 2024-10-05 04:48

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('sales', '0030_dailyreport_location_no'),
    ]

    operations = [
        migrations.AddField(
            model_name='dailyreport',
            name='coupon_type_600',
            field=models.DecimalField(decimal_places=0, default=0, max_digits=10),
        ),
        migrations.AddField(
            model_name='dailyreport',
            name='coupon_type_700',
            field=models.DecimalField(decimal_places=0, default=0, max_digits=10),
        ),
        migrations.AddField(
            model_name='dailyreport',
            name='discount_100',
            field=models.DecimalField(decimal_places=0, default=0, max_digits=10),
        ),
        migrations.AddField(
            model_name='dailyreport',
            name='discount_50',
            field=models.DecimalField(decimal_places=0, default=0, max_digits=10),
        ),
        migrations.AddField(
            model_name='dailyreport',
            name='extra_rice_quantity',
            field=models.DecimalField(decimal_places=0, default=0, max_digits=10),
        ),
        migrations.AddField(
            model_name='dailyreport',
            name='no_rice_quantity',
            field=models.DecimalField(decimal_places=0, default=0, max_digits=10),
        ),
    ]
