# Generated by Django 3.2.3 on 2022-05-04 05:14

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('orders', '0026_auto_20220330_1327'),
    ]

    operations = [
        migrations.AddField(
            model_name='ordercustomfield',
            name='show_in_xls',
            field=models.BooleanField(default=True, verbose_name='Отображать в выгрузке для клиента'),
        ),
    ]
