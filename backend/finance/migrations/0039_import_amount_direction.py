from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('finance', '0038_accountalias_bankimport_dismissed'),
    ]

    operations = [
        migrations.AddField(model_name='account', name='import_amounts_reversed', field=models.BooleanField(default=False)),
        migrations.AddField(model_name='account', name='import_sign_confirmed', field=models.BooleanField(default=False)),
        migrations.AddField(model_name='bankimport', name='sign_facts', field=models.JSONField(blank=True, null=True)),
        migrations.AddField(model_name='bankimport', name='amounts_reversed', field=models.BooleanField(default=False)),
        migrations.AddField(model_name='bankimport', name='sign_state', field=models.CharField(blank=True, default='', max_length=10)),
    ]
