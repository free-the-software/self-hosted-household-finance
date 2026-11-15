from django.db import migrations


def put_away_old_failures(apps, schema_editor):
    """
    Failures recorded before the Import Inbox listed them (0038) were from
    since-fixed bugs, with raw error text; they're marked as already seen.
    """
    apps.get_model('finance', 'BankImport').objects.filter(status='Failed').update(dismissed=True)


class Migration(migrations.Migration):

    dependencies = [
        ('finance', '0039_import_amount_direction'),
    ]

    operations = [
        migrations.RunPython(put_away_old_failures, migrations.RunPython.noop),
    ]
