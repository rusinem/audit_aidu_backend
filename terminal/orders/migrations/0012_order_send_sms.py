# Generated by Django 3.2.3 on 2021-07-05 07:22

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('orders', '0011_alter_orderstatuslog_created'),
    ]

    operations = [
        migrations.AddField(
            model_name='order',
            name='send_sms',
            field=models.BooleanField(default=False),
        ),
    ]
