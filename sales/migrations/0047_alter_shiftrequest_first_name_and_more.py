# Generated by Django 5.1.1 on 2025-02-09 08:56

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('sales', '0046_alter_shiftrequest_unique_together_and_more'),
    ]

    operations = [
        migrations.AlterField(
            model_name='shiftrequest',
            name='first_name',
            field=models.CharField(blank=True, max_length=50),
        ),
        migrations.AlterField(
            model_name='shiftrequest',
            name='last_name',
            field=models.CharField(blank=True, max_length=50),
        ),
    ]
