from django.db import migrations, models


def mark_by_date(apps, schema_editor):
    """
    Existing ledgers: cleared transactions dated up to each account's bank
    balance are taken to be in it (the rule until now). New imports mark
    them exactly (finance/balances.py).
    """
    Account = apps.get_model('finance', 'Account')
    Transaction = apps.get_model('finance', 'Transaction')
    for account in Account.objects.exclude(balance_date=None):
        Transaction.objects.filter(account=account, dtposted__lte=account.balance_date).exclude(status='P') \
            .update(in_balance_as_of=account.balance_date)


class Migration(migrations.Migration):

    dependencies = [
        ('finance', '0042_userprofile_menu_order'),
    ]

    operations = [
        migrations.AddField(
            model_name='transaction',
            name='in_balance_as_of',
            field=models.DateField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='bankimport',
            name='balance_as_of',
            field=models.DateField(blank=True, null=True),
        ),
        migrations.RunPython(mark_by_date, migrations.RunPython.noop),
    ]
