# Generated by Django 5.1.1 on 2024-09-23 06:25

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('sales', '0029_alter_saleslocation_price_type_and_more'),
    ]

    operations = [
        migrations.AddField(
            model_name='dailyreport',
            name='location_no',
            field=models.IntegerField(default=0),
        ),
    ]
