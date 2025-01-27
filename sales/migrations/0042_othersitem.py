# Generated by Django 5.1.1 on 2025-01-22 04:08

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('sales', '0041_dailyreportentry_product_no'),
    ]

    operations = [
        migrations.CreateModel(
            name='OthersItem',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('no', models.IntegerField(default=0)),
                ('name', models.CharField(max_length=255)),
                ('price', models.DecimalField(decimal_places=0, default=0, max_digits=10)),
            ],
        ),
    ]
