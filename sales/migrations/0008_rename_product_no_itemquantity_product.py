# Generated by Django 5.1.1 on 2024-09-07 05:53

from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ('sales', '0007_alter_itemquantity_product_no'),
    ]

    operations = [
        migrations.RenameField(
            model_name='itemquantity',
            old_name='product_no',
            new_name='product',
        ),
    ]
