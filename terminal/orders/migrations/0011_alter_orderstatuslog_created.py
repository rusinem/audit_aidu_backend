# Generated by Django 3.2.3 on 2021-07-01 13:56

from django.db import migrations, models
import django.utils.timezone


class Migration(migrations.Migration):

    dependencies = [
        ('orders', '0010_auto_20210625_1001'),
    ]

    def insertData(apps, schema_editor):
        OrderStatus = apps.get_model('orders', 'OrderStatus')
        OrderStatus.objects.filter(id=4).update(id=5)
        OrderStatus.objects.filter(id=3).update(id=4)
        OrderStatus.objects.filter(id=2).update(id=3)
        OrderStatus.objects.get_or_create(id=2, title='Опубликован')

    def reverse_func(apps, schema_editor):
        pass

    operations = [
        migrations.AlterField(
            model_name='orderstatuslog',
            name='created',
            field=models.DateTimeField(default=django.utils.timezone.now, verbose_name='Дата создания'),
        ),

        migrations.RunPython(insertData, reverse_func),
    ]
