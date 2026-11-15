# Copyright (C) 2026 free-the-software
# SPDX-License-Identifier: AGPL-3.0-or-later
# This file is part of Save More Money, under the GNU AGPL v3 or later: see LICENSE.
"""
Starter categories for a brand-new install, so budgets, rules and the
Import Inbox have somewhere to put things from the first day. The list
follows the familiar shape of personal-finance programs' default
categories. Added only to a database with no categories and no
transactions, so an existing ledger is never touched. Rename, move, delete
or add to them freely in Settings.

Some are also used by the app itself, each for a job (ROLES below): loan
payments (Mortgage Interest, Property Tax, Home Insurance, PMI, and Loan
Principal and Escrow under Transfers) and the receipt tax split (Sales
Tax). They're found by that job, not their name, so they can be renamed or
moved; deleting one asks where its job should go (finance/categories.py).

Turn off with STARTER_CATEGORIES=False in .env.
"""
STARTER_CATEGORIES = [
    ('INC', 'Income', [
        'Salary', 'Bonus', 'Commission', 'Interest Income', 'Dividend Income', 'Capital Gains', 'Rental Income',
        'Retirement Income', 'Social Security', 'Gifts Received', 'Refunds & Reimbursements', 'Other Income',
    ]),
    ('EXP', 'Auto & Transport', [
        'Gas & Fuel', 'Auto Insurance', 'Auto Loan Interest', 'Service & Parts', 'Registration & Fees',
        'Parking & Tolls', 'Public Transportation', 'Ride Share & Taxi',
    ]),
    ('EXP', 'Bills & Utilities', [
        'Electricity', 'Natural Gas', 'Water & Sewer', 'Trash & Recycling', 'Internet', 'Mobile Phone', 'Home Phone',
        'TV & Streaming',
    ]),
    ('EXP', 'Business', ['Office Supplies', 'Business Services', 'Business Travel']),
    ('EXP', 'Education', ['Tuition', 'Books & Supplies', 'Student Loan Interest']),
    ('EXP', 'Entertainment', ['Movies & Events', 'Music', 'Books & Magazines', 'Games']),
    ('EXP', 'Fees & Charges', ['Bank Fees', 'ATM Fees', 'Finance Charges', 'Late Fees', 'Loan Interest']),
    ('EXP', 'Food & Dining', ['Groceries', 'Restaurants', 'Fast Food', 'Coffee Shops', 'Alcohol & Bars']),
    ('EXP', 'Gifts & Donations', ['Charity', 'Gifts Given']),
    ('EXP', 'Health & Fitness', ['Doctor', 'Dentist', 'Eye Care', 'Pharmacy', 'Health Insurance', 'Gym & Fitness']),
    ('EXP', 'Home', [
        'Rent', 'Mortgage Interest', 'Home Insurance', 'PMI', 'Escrow Payments', 'HOA Dues', 'Maintenance & Repairs',
        'Home Improvement', 'Furnishings', 'Home Supplies', 'Lawn & Garden', 'Home Services',
    ]),
    ('EXP', 'Insurance', ['Life Insurance', 'Disability Insurance']),
    ('EXP', 'Kids', ['Childcare', 'Activities', 'Allowance', 'Baby Supplies', 'Child Support', 'Toys']),
    ('EXP', 'Personal Care', ['Hair', 'Laundry & Dry Cleaning', 'Spa & Massage', 'Toiletries']),
    ('EXP', 'Pets', ['Pet Food & Supplies', 'Veterinary', 'Pet Grooming']),
    ('EXP', 'Shopping', ['Clothing', 'Electronics & Software', 'Books', 'Hobbies', 'Sporting Goods']),
    ('EXP', 'Taxes', [
        'Federal Income Tax', 'State Income Tax', 'Local Tax', 'Property Tax', 'Sales Tax', 'Social Security Tax',
        'Medicare Tax',
    ]),
    ('EXP', 'Travel', ['Airfare', 'Hotel', 'Rental Car', 'Vacation']),
    ('EXP', 'Misc Expenses', ['Miscellaneous']),
    ('XFR', 'Transfers', ['Credit Card Payment', 'Between Accounts', 'To Savings', 'To Investments', 'Loan Principal', 'Escrow']),
]


# The jobs the app gives subcategories: the name and group one is made with
# if there isn't one, and the kind of category it must stay in (a loan's
# principal is a transfer, not spending)
ROLES = {
    'mortgage_interest': ('Mortgage Interest', 'Home', 'EXP'),
    'auto_loan_interest': ('Auto Loan Interest', 'Auto & Transport', 'EXP'),
    'loan_interest': ('Loan Interest', 'Fees & Charges', 'EXP'),
    'property_tax': ('Property Tax', 'Taxes', 'EXP'),
    'home_insurance': ('Home Insurance', 'Home', 'EXP'),
    'pmi': ('PMI', 'Home', 'EXP'),
    'escrow_payments': ('Escrow Payments', 'Home', 'EXP'),
    'loan_principal': ('Loan Principal', 'Transfers', 'XFR'),
    'escrow': ('Escrow', 'Transfers', 'XFR'),
    'sales_tax': ('Sales Tax', 'Taxes', 'EXP'),
}
# Shown on the subcategory in Settings
ROLE_LABELS = {
    'mortgage_interest': 'mortgage interest', 'auto_loan_interest': 'car loan interest', 'loan_interest': 'loan interest',
    'property_tax': 'property tax escrow', 'home_insurance': 'home insurance escrow', 'pmi': 'PMI',
    'escrow_payments': 'escrow bills', 'loan_principal': 'loan principal', 'escrow': 'escrow deposits',
    'sales_tax': 'receipt sales tax',
}


def subcategory_for(role):
    """
    The subcategory that does this job: the one holding the role, or (for a
    ledger from before roles) one with the job's usual name and kind, which
    then takes the role; failing both, a new one.
    """
    from .models import Category, SubCategory
    holder = SubCategory.objects.filter(role=role).first()
    if holder:
        return holder
    name, group_name, kind = ROLES[role]
    holder = SubCategory.objects.filter(name__iexact=name, category__type=kind, role__isnull=True).order_by('id').first()
    if holder is None:
        group = Category.objects.filter(name__iexact=group_name, type=kind).order_by('id').first() \
            or Category.objects.create(name=group_name, type=kind)
        holder = SubCategory.objects.filter(category=group, name__iexact=name).first() \
            or SubCategory(category=group, name=name)
    holder.role = role
    holder.save()
    return holder


def add_starter_categories(**_kwargs):
    """Runs after migrations; does nothing unless the ledger is completely new."""
    from django.conf import settings
    from django.db import transaction
    from .models import Category, SubCategory, Transaction

    if not getattr(settings, 'STARTER_CATEGORIES', True):
        return
    if Category.objects.exists() or Transaction.objects.exists():
        return
    with transaction.atomic():
        for sort_order, (kind, name, subcategories) in enumerate(STARTER_CATEGORIES, start=1):
            category = Category.objects.create(name=name, type=kind, sort_order=sort_order)
            SubCategory.objects.bulk_create(SubCategory(category=category, name=sub) for sub in subcategories)
        for role in ROLES:
            subcategory_for(role)
