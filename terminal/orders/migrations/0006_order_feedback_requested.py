# Generated by Django 3.2.3 on 2021-06-07 09:07

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('orders', '0005_order_completed_time'),
    ]

    operations = [
        migrations.AddField(
            model_name='order',
            name='feedback_requested',
            field=models.BooleanField(default=False),
        ),
    ]
