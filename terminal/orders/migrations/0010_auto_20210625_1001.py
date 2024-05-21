# Generated by Django 3.2.3 on 2021-06-25 10:01

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('orders', '0009_ordercustomfield_size'),
    ]

    operations = [
        migrations.AddField(
            model_name='ordercustomfield',
            name='label',
            field=models.CharField(blank=True, max_length=255, null=True),
        ),
        migrations.AddField(
            model_name='ordercustomfield',
            name='required',
            field=models.BooleanField(default=True),
        ),
        migrations.AlterField(
            model_name='ordercustomfield',
            name='size',
            field=models.CharField(blank=True, max_length=255, null=True, verbose_name='Размер'),
        ),
    ]
