from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('finance', '0031_backup_worker'),
    ]

    operations = [
        migrations.AddField(
            model_name='stagedtransaction',
            name='match_reason',
            field=models.CharField(blank=True, default='', max_length=200),
        ),
    ]
