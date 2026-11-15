from django.db import migrations, models
import django.db.models.deletion

# Which subcategory does each job, matched once by name for existing ledgers
# (later, finance/starter.py finds them by role); the kind it must stay
ROLE_NAMES = {
    'mortgage_interest': ('Mortgage Interest', 'EXP'),
    'auto_loan_interest': ('Auto Loan Interest', 'EXP'),
    'loan_interest': ('Loan Interest', 'EXP'),
    'property_tax': ('Property Tax', 'EXP'),
    'home_insurance': ('Home Insurance', 'EXP'),
    'pmi': ('PMI', 'EXP'),
    'escrow_payments': ('Escrow Payments', 'EXP'),
    'loan_principal': ('Loan Principal', 'XFR'),
    'escrow': ('Escrow', 'XFR'),
    'sales_tax': ('Sales Tax', 'EXP'),
}


def assign_roles(apps, schema_editor):
    SubCategory = apps.get_model('finance', 'SubCategory')
    for role, (name, kind) in ROLE_NAMES.items():
        match = SubCategory.objects.filter(name__iexact=name, category__type=kind, role__isnull=True).order_by('id').first()
        if match:
            match.role = role
            match.save(update_fields=['role'])


class Migration(migrations.Migration):

    dependencies = [
        ('finance', '0040_dismiss_old_import_failures'),
    ]

    operations = [
        migrations.AddField(
            model_name='subcategory',
            name='role',
            field=models.CharField(blank=True, max_length=30, null=True, unique=True),
        ),
        migrations.AlterField(
            model_name='subcategory',
            name='category',
            field=models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='subcategories', to='finance.category'),
        ),
        migrations.AlterField(
            model_name='transactionrule',
            name='subcategory',
            field=models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, to='finance.subcategory'),
        ),
        migrations.AlterField(
            model_name='transactionsplit',
            name='subcategory',
            field=models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, to='finance.subcategory'),
        ),
        migrations.AlterField(
            model_name='budget',
            name='subcategory',
            field=models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='budgets', to='finance.subcategory'),
        ),
        migrations.RunPython(assign_roles, migrations.RunPython.noop),
    ]
