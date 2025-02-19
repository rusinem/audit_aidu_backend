# Generated by Django 3.2.3 on 2021-10-08 09:35

from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('clients', '0011_clientstore_city'),
        ('orders', '0022_ordercustomfield_archive'),
    ]

    def insertData(apps, schema_editor):
        Order = apps.get_model('orders', 'Order')

        for order in Order.objects.all():
            dep = order.department
            if dep:
                order.departments.add(dep)

    def reverse_func(apps, schema_editor):
        pass

    operations = [
        migrations.AddField(
            model_name='order',
            name='departments',
            field=models.ManyToManyField(blank=True, to='clients.ClientStoreDepartment', verbose_name='Отделы Торговой точки'),
        ),
        migrations.AlterField(
            model_name='order',
            name='department',
            field=models.ForeignKey(null=True, on_delete=django.db.models.deletion.CASCADE, related_name='order_department', to='clients.clientstoredepartment', verbose_name='Отдел Торговой точки'),
        ),
        migrations.RunPython(insertData, reverse_func)
    ]
