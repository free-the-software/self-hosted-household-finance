from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('finance', '0032_stagedtransaction_match_reason'),
    ]

    operations = [
        migrations.AddField(
            model_name='stagedtransaction',
            name='splits',
            field=models.JSONField(blank=True, default=list),
        ),
        migrations.AddField(
            model_name='stagedtransaction',
            name='edited',
            field=models.BooleanField(default=False),
        ),
    ]
