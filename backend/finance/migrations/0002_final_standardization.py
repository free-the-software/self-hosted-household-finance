from django.db import migrations

class Migration(migrations.Migration):

    dependencies = [
        ('finance', '0001_initial'),
    ]

    operations = [
        migrations.RenameField(
            model_name='transactionrule',
            old_name='name',
            new_name='rule_name',
        ),
        migrations.RenameField(
            model_name='transactionrule',
            old_name='new_payee',
            new_name='clean_merchant_name',
        ),
    ]
