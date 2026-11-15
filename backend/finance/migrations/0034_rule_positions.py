"""
Rules move from a priority number (with a hidden tie-break) to an explicit
position, like an access list. Each rule is numbered in the order it was
tried in before, so nothing changes until the user moves a rule.
"""
import html
import re

from django.db import migrations


def _normalize(text):
    return re.sub(r'\s+', ' ', html.unescape(str(text or ''))).strip().lower()


def previous_order_key(rule):
    """The old order: priority, then more conditions, then longer text, then name."""
    conditions = [cond for cond in (rule.conditions or []) if isinstance(cond, dict)]
    length = sum(len(_normalize(cond.get('value'))) for cond in conditions)
    return (rule.position, -len(conditions), -length, _normalize(rule.rule_name), rule.pk)


def number_in_previous_order(apps, schema_editor):
    TransactionRule = apps.get_model('finance', 'TransactionRule')
    rules = sorted(TransactionRule.objects.all(), key=previous_order_key)
    for position, rule in enumerate(rules, start=1):
        rule.position = position
    TransactionRule.objects.bulk_update(rules, ['position'])


class Migration(migrations.Migration):

    dependencies = [
        ('finance', '0033_stagedtransaction_splits'),
    ]

    operations = [
        # The old priority becomes the first sort key, then is replaced by the position
        migrations.RenameField(model_name='transactionrule', old_name='priority', new_name='position'),
        migrations.AlterModelOptions(name='transactionrule', options={'ordering': ['position', 'id']}),
        migrations.RunPython(number_in_previous_order, migrations.RunPython.noop),
    ]
