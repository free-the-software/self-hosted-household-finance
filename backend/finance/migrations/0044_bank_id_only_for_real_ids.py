from django.db import migrations, models


def clear_placeholder_ids(apps, schema_editor):
    """
    Transactions from files without bank ids were all given "UNKNOWN", so a
    second identical charge was refused as a duplicate of the first. They
    now have no id (only real ids must be unique); rows waiting in the Import
    Inbox are looked at again.
    """
    apps.get_model('finance', 'Transaction').objects.filter(fitid='UNKNOWN').update(fitid='')
    staged = apps.get_model('finance', 'StagedTransaction').objects.filter(fitid='UNKNOWN')
    staged.filter(match_type='DUPE').update(match_type='NEW', match_proposal=None, match_reason='')
    staged.update(fitid='')


class Migration(migrations.Migration):

    dependencies = [
        ('finance', '0043_transaction_in_balance_as_of'),
    ]

    operations = [
        migrations.RunPython(clear_placeholder_ids, migrations.RunPython.noop),
        migrations.AlterUniqueTogether(
            name='transaction',
            unique_together=set(),
        ),
        migrations.AddConstraint(
            model_name='transaction',
            constraint=models.UniqueConstraint(condition=models.Q(('fitid', ''), _negated=True), fields=('account', 'fitid', 'trnamt', 'dtposted'), name='one_transaction_per_bank_id'),
        ),
    ]
