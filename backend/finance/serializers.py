# Copyright (C) 2026 free-the-software
# SPDX-License-Identifier: AGPL-3.0-or-later
# This file is part of Save More Money, under the GNU AGPL v3 or later: see LICENSE.
from decimal import Decimal

from django.db import transaction as db_transaction
from rest_framework import serializers
from .models import (
    Transaction, Account, Category, SubCategory,
    TransactionRule, Budget, RecurringTransaction,
    StagedTransaction, TransactionSplit, Receipt,
    AssetDetail, LoanDetail, BalanceEntry,
)

# --- 1. CHILD SERIALIZERS ---
# MOVED TO TOP so other serializers can use it
class ReceiptSerializer(serializers.ModelSerializer):
    # The linked transaction, and whether its tax could be split out, so the
    # user can see exactly what approving a tax split would change
    linked_transaction = serializers.SerializerMethodField()

    class Meta:
        model = Receipt
        fields = ['id', 'image', 'total_amount', 'date', 'status', 'tax_amount', 'raw_text', 'image_expired_at', 'linked_transaction']
        read_only_fields = ['image_expired_at']

    def get_linked_transaction(self, obj):
        from .services import tax_split_problem
        tx = obj.transaction
        if tx is None:
            return None
        return {
            'id': tx.id,
            'date': tx.dtposted,
            'description': tx.name,
            'amount': str(tx.trnamt),
            'account_name': tx.account.name if tx.account else None,
            'subcategory_name': tx.subcategory.name if tx.subcategory else None,
            'already_split': len(tx.splits.all()) > 0,
            # None when a split is possible; otherwise the reason it is not
            'split_blocked_by': tax_split_problem(tx, obj),
        }

class TransactionSplitSerializer(serializers.ModelSerializer):
    subcategory_name = serializers.CharField(source='subcategory.name', read_only=True)
    # REMOVED 'receipts' from here - splits don't have receipts, Transactions do.
    class Meta:
        model = TransactionSplit
        fields = ['id', 'subcategory', 'subcategory_name', 'amount', 'memo']

def check_account_name(name, instance=None):
    """
    Account names are unique, ignoring capitals and spaces at the ends, so
    every list, report and import can tell accounts apart. A name that isn't
    changing is let through, so an older pair of twins can still be edited.
    """
    name = (name or '').strip()
    if not name:
        raise serializers.ValidationError('Give the account a name.')
    if instance is not None and (instance.name or '').strip().lower() == name.lower():
        return name
    other = Account.objects.filter(name__iexact=name).exclude(pk=getattr(instance, 'pk', None)).first()
    if other:
        closed = ' (closed)' if other.closed_date else ''
        raise serializers.ValidationError(
            f'You already have an account called "{other.name}"{closed}. Give this one a different name, '
            'for example with the bank or the last digits of the account number.')
    return name


def unused_account_name(base):
    """base, or base with a number after it if that name is taken: "House escrow 2"."""
    name, number = base, 2
    while Account.objects.filter(name__iexact=name).exists():
        name, number = f'{base} {number}', number + 1
    return name


class AccountSerializer(serializers.ModelSerializer):
    class Meta:
        model = Account
        fields = [
            'account_id', 
            'name', 
            'fid', 
            'account_number', 
            'institution', 
            'account_type', 
            'tax_status', 
            'owner', 
            'is_liability', 
            'invert_sign', 
            'current_balance', 
            'interest_rate', 
            'credit_limit', 
            'due_day', 
            'closed_date', 
            'last_reconciled', 
            'total_tx_count', 
            'last_tx_date', 
            'last_balance', 
            'balance_date', 
            'show_forecast', 
            'active',
            'is_balance_calculated',
            # This bank's files write money out as positive (finance/csv_import.py)
            'import_amounts_reversed',
            'import_sign_confirmed',
        ]
        # Ensure the primary key is read-only so the user can't manually change it
        read_only_fields = ['account_id', 'last_balance', 'balance_date', 'current_balance', 'import_sign_confirmed']

    def validate_name(self, value):
        return check_account_name(value, self.instance)

    def validate(self, data):
        # Set by hand in Settings: that's the answer, so imports stop asking
        if 'import_amounts_reversed' in data:
            data['import_sign_confirmed'] = True
        return data

class SubCategorySerializer(serializers.ModelSerializer):
    # The job the app gives it, if any (finance/starter.py ROLES), in words
    role_label = serializers.SerializerMethodField()

    class Meta:
        model = SubCategory
        fields = ['id', 'name', 'category', 'role', 'role_label']
        read_only_fields = ['role']
        validators = []         # the clash is checked below, with a readable message

    def get_role_label(self, sub):
        from .starter import ROLE_LABELS
        return ROLE_LABELS.get(sub.role)

    def validate(self, data):
        from .categories import CategoryError, check_kind
        name = (data.get('name', getattr(self.instance, 'name', '')) or '').strip()
        category = data.get('category', getattr(self.instance, 'category', None))
        if not name:
            raise serializers.ValidationError({'name': 'Give it a name.'})
        clash = SubCategory.objects.filter(category=category, name__iexact=name)
        if self.instance:
            clash = clash.exclude(pk=self.instance.pk)
        if clash.exists():
            raise serializers.ValidationError({'name': f'{category.name} already has a subcategory called "{name}". '
                                                       'To combine the two, delete this one and move everything to that one.'})
        if self.instance and category:
            try:
                check_kind(self.instance.role, category)
            except CategoryError as error:
                raise serializers.ValidationError({'category': str(error)})
        data['name'] = name
        return data


class CategorySerializer(serializers.ModelSerializer):
    subcategories = SubCategorySerializer(many=True, read_only=True)
    class Meta:
        model = Category
        fields = ['id', 'name', 'type', 'sort_order','subcategories']
        validators = []         # the clash is checked below, with a readable message

    def validate(self, data):
        from .categories import KIND_NAMES
        from .starter import ROLE_LABELS, ROLES
        name = (data.get('name', getattr(self.instance, 'name', '')) or '').strip()
        kind = data.get('type', getattr(self.instance, 'type', 'EXP'))
        if not name:
            raise serializers.ValidationError({'name': 'Give it a name.'})
        clash = Category.objects.filter(name__iexact=name, type=kind)
        if self.instance:
            clash = clash.exclude(pk=self.instance.pk)
        if clash.exists():
            raise serializers.ValidationError({'name': f'There is already {KIND_NAMES[kind].lower()} category called "{name}".'})
        if self.instance and kind != self.instance.type:
            for sub in self.instance.subcategories.exclude(role__isnull=True):
                if ROLES[sub.role][2] != kind:
                    raise serializers.ValidationError({'type': (
                        f'{sub.name} is used for {ROLE_LABELS[sub.role]}, which must stay in {KIND_NAMES[ROLES[sub.role][2]]} '
                        'category. Move it to another category first.')})
        data['name'] = name
        return data

# --- 2. MAIN TRANSACTION SERIALIZER ---
class TransactionSerializer(serializers.ModelSerializer):
    # Read-only / Mapped Fields
    account_name = serializers.CharField(source='account.name', read_only=True)
    subcategory_name = serializers.CharField(source='subcategory.name', read_only=True)
    type = serializers.SerializerMethodField()
    date = serializers.DateField(source='dtposted')
    amount = serializers.DecimalField(source='trnamt', max_digits=19, decimal_places=2)
    description = serializers.CharField(source='name', allow_blank=True, allow_null=True, required=False)
    
    #Transfer Linking Fields
    linked_transaction_account = serializers.CharField(source='linked_transaction.account.name', read_only=True)

    # This field fetches the URL of the first attached receipt
    receipt_image = serializers.SerializerMethodField()
    # Set when that receipt's image was removed by a retention policy; the
    # receipt's details are still on record
    receipt_expired = serializers.SerializerMethodField()

    # Optional Fields
    bank_memo = serializers.CharField(allow_blank=True, allow_null=True, required=False)
    personal_memo = serializers.CharField(required=False, allow_null=True, allow_blank=True)
    status = serializers.ChoiceField(choices=Transaction.STATUS_CHOICES, default=Transaction.STATUS_PENDING, required=False)

    # Nested Relations
    splits = TransactionSplitSerializer(many=True, required=False)

    # Only when the list is filtered by subcategory: the part of this
    # transaction that counts toward it (the whole amount unless it is split)
    subcategory_amount = serializers.SerializerMethodField()

    class Meta:
        model = Transaction
        fields = [
            'id', 'date', 'amount', 'description', 'bank_memo', 'personal_memo',
            'fitid', 'status', 'account', 'account_name',
            'subcategory', 'subcategory_name', 'is_manual',
            'type', 'splits', 'receipt_image', 'receipt_expired', 'linked_transaction', 'linked_transaction_account',
            'subcategory_amount',
        ]
        extra_kwargs = {
            'fitid': {'validators': []}
        }

    def get_subcategory_amount(self, obj):
        amount = getattr(obj, 'subcategory_amount', None)
        return None if amount is None else f'{amount:.2f}'

    def get_type(self, obj):
        if obj.linked_transaction:
            return "Transfer"
        if obj.subcategory and obj.subcategory.category:
            return obj.subcategory.category.get_type_display()
        return "Uncategorized"

    def _first_receipt(self, obj):
        # 'obj.receipt' is a Manager (a list), so we must use .first().
        # Cached on the object because two fields below need it.
        if not hasattr(obj, '_first_receipt_cache'):
            # .all() reuses receipts the view fetched for the whole page;
            # .first() would ask the database again for every row.
            receipts = sorted(obj.receipt.all(), key=lambda receipt: receipt.id) if hasattr(obj, 'receipt') else []
            obj._first_receipt_cache = receipts[0] if receipts else None
        return obj._first_receipt_cache

    def get_receipt_image(self, obj):
        try:
            first_receipt = self._first_receipt(obj)
            # If we found a receipt and it has an image, return the URL
            if first_receipt and first_receipt.image:
                return first_receipt.image.url
            return None
        except Exception as e:
            # Log the error but DO NOT crash the app
            print(f"Error getting receipt for {obj.id}: {e}")
            return None

    def get_receipt_expired(self, obj):
        try:
            first_receipt = self._first_receipt(obj)
            if first_receipt and first_receipt.image_expired_at:
                # Amounts as strings, like every other money field in the API
                as_text = lambda amount: None if amount is None else str(amount)
                return {
                    'expired_at': first_receipt.image_expired_at,
                    'date': first_receipt.date,
                    'total_amount': as_text(first_receipt.total_amount),
                    'tax_amount': as_text(first_receipt.tax_amount),
                }
            return None
        except Exception as e:
            print(f"Error getting receipt for {obj.id}: {e}")
            return None

    def create(self, validated_data):
        splits_data = validated_data.pop('splits', [])
        transaction = Transaction.objects.create(**validated_data)
        for split in splits_data:
            TransactionSplit.objects.create(transaction=transaction, **split)
        return transaction

    def update(self, instance, validated_data):
        splits_data = validated_data.pop('splits', None)
        # Update standard fields
        for attr, value in validated_data.items():
            setattr(instance, attr, value)
        instance.save()
        # Update Splits if provided (Wipe and Recreate strategy)
        if splits_data is not None:
            instance.splits.all().delete()
            for split in splits_data:
                TransactionSplit.objects.create(transaction=instance, **split)
        return instance

# --- 3. HELPER SERIALIZERS ---
class TransactionRuleSerializer(serializers.ModelSerializer):
    # Read-only field to display the name in the table
    subcategory_name = serializers.CharField(source='subcategory.name', read_only=True)
    # Read-only field to display the parent Category name (helpful for the dropdown)
    category_name = serializers.CharField(source='subcategory.category.name', read_only=True)
    # Writeable field for the dropdown selection
    subcategory = serializers.PrimaryKeyRelatedField(queryset=SubCategory.objects.all())

    class Meta:
        model = TransactionRule
        fields = [
            'id', 
            'rule_name', 
            'clean_merchant_name',
            'subcategory',
            'subcategory_name',
            'category_name',  
            'is_active', 
            'position',
            'conditions',
            'logic_operator',
            'hit_count',
            'last_matched',
        ]
        # Moved with the "move" action, never by editing the number
        read_only_fields = ['hit_count', 'last_matched', 'position']

    def validate_conditions(self, conditions):
        from .rules import conditions_problem
        problem = conditions_problem(conditions)
        if problem:
            raise serializers.ValidationError(problem)
        return conditions

class BudgetSerializer(serializers.ModelSerializer):
    class Meta:
        model = Budget
        fields = '__all__'

class RecurringTransactionSerializer(serializers.ModelSerializer):
    subcategory_name = serializers.CharField(source='subcategory.name', read_only=True)
    category_name = serializers.CharField(source='subcategory.category.name', read_only=True)
    class Meta:
        model = RecurringTransaction
        fields = [
            'id', 'name', 'amount', 'account', 'frequency',
            'start_date', 'end_date', 'create_days_before',
            'subcategory', 'subcategory_name', 'category_name'
        ]

class StagedSplitSerializer(serializers.Serializer):
    """One split line of an imported transaction, before it is approved."""
    subcategory = serializers.PrimaryKeyRelatedField(queryset=SubCategory.objects.all())
    amount = serializers.DecimalField(max_digits=12, decimal_places=2)
    memo = serializers.CharField(required=False, allow_blank=True, default='')


class StagedTransactionSerializer(serializers.ModelSerializer):
    account_name = serializers.CharField(source='account.name', read_only=True)
    subcategory_name = serializers.CharField(source='subcategory.name', read_only=True, allow_null=True)
    # Written as split lines; shown with their category names (see to_representation)
    splits = StagedSplitSerializer(many=True, required=False, write_only=True)
    match_proposal_name = serializers.CharField(source='match_proposal.name', read_only=True, allow_null=True)
    # The transaction entered by hand that this one matches, so the two can be
    # shown side by side before the user decides
    match_proposal_details = serializers.SerializerMethodField()

    class Meta:
        model = StagedTransaction
        fields = '__all__'
        # Only what the user may change before approving: the bank's own
        # details and the matching results stay as imported
        read_only_fields = [
            'batch', 'account', 'unregistered_account_id', 'fitid', 'date', 'amount', 'bank_memo',
            'match_proposal', 'match_type', 'match_reason', 'edited',
        ]

    def validate(self, data):
        splits = data.get('splits')
        if splits:
            if len(splits) < 2:
                raise serializers.ValidationError({'splits': 'A split needs at least two lines.'})
            total = sum(line['amount'] for line in splits)
            if self.instance is not None and total != self.instance.amount:
                raise serializers.ValidationError(
                    {'splits': f'The split lines add up to {total}, not the transaction\'s {self.instance.amount}.'}
                )
        return data

    def to_representation(self, instance):
        data = super().to_representation(instance)
        names = dict(SubCategory.objects.filter(id__in=[line.get('subcategory') for line in instance.splits or []]).values_list('id', 'name')) if instance.splits else {}
        data['splits'] = [
            {'subcategory': line.get('subcategory'), 'subcategory_name': names.get(line.get('subcategory')),
             'amount': line.get('amount'), 'memo': line.get('memo', '')}
            for line in instance.splits or []
        ]
        return data

    def update(self, instance, validated_data):
        # The bank's own description is kept when the payee is renamed
        if validated_data.get('name') not in (None, instance.name) and not instance.bank_memo:
            instance.bank_memo = instance.name
        instance.edited = True
        splits = validated_data.pop('splits', None)
        if splits is not None:
            # Stored as plain data until the transaction is approved
            instance.splits = [
                {'subcategory': line['subcategory'].id, 'amount': str(line['amount']), 'memo': line.get('memo', '')}
                for line in splits
            ]
        return super().update(instance, validated_data)

    def get_match_proposal_details(self, obj):
        tx = obj.match_proposal
        if tx is None:
            return None
        return {
            'id': tx.id,
            'date': tx.dtposted,
            'description': tx.name,
            'amount': str(tx.trnamt),
            'subcategory': tx.subcategory_id,
            'subcategory_name': tx.subcategory.name if tx.subcategory else None,
            'personal_memo': tx.personal_memo,
            'is_split': len(tx.splits.all()) > 0,
            # So the Import Inbox's edit window can start from the entry as it is
            'splits': [
                {'subcategory': line.subcategory_id, 'amount': str(line.amount), 'memo': line.memo or ''}
                for line in tx.splits.all()
            ],
        }



# --- ASSETS AND LOANS (Net Worth page) ---
class AssetDetailSerializer(serializers.ModelSerializer):
    class Meta:
        model = AssetDetail
        exclude = ['id', 'account']


class LoanDetailSerializer(serializers.ModelSerializer):
    # Worked out from the terms, shown so the user can compare with their papers
    scheduled_payment = serializers.SerializerMethodField()
    payoff_date = serializers.SerializerMethodField()

    class Meta:
        model = LoanDetail
        # The escrow account is made and kept by the app (escrow_mode decides it)
        exclude = ['id', 'liability_account', 'interest_category', 'property_tax_category', 'homeowners_insurance_category',
                   'pmi_category', 'escrow_account']
        extra_kwargs = {'asset_account': {'required': False, 'allow_null': True}}

    def get_scheduled_payment(self, obj):
        from .loans import scheduled_payment
        return str(scheduled_payment(obj))

    def get_payoff_date(self, obj):
        from .loans import payoff_date
        return payoff_date(obj)

    def validate(self, data):
        for field in ('original_balance', 'interest_rate'):
            if field in data and data[field] < 0:
                raise serializers.ValidationError({field: 'Must be zero or more.'})
        if 'term_months' in data and not 1 <= data['term_months'] <= 600:
            raise serializers.ValidationError({'term_months': 'Must be between 1 and 600 months.'})
        return data


class BalanceEntrySerializer(serializers.ModelSerializer):
    class Meta:
        model = BalanceEntry
        fields = ['id', 'date', 'amount', 'source']

    def validate_amount(self, value):
        if value < 0:
            raise serializers.ValidationError('Enter the value or the amount owed as a positive number.')
        return value


class HoldingSerializer(serializers.ModelSerializer):
    """
    An asset or a loan: an account with its details. kind is one of home,
    vehicle, other (assets), or mortgage, auto_loan, loan (loans).
    """
    KINDS = {
        'home': ('HOME', 'HOME', False), 'vehicle': ('VEHICLE', 'VEHICLE', False), 'other': ('ASSET', 'OTHER', False),
        'investment': ('INVESTMENT', 'INVESTMENT', False),
        'mortgage': ('MORTGAGE', 'Mortgage', True), 'auto_loan': ('AUTO LOAN', 'Auto', True), 'loan': ('LOAN', 'Personal', True),
    }
    id = serializers.IntegerField(source='account_id', read_only=True)
    # Given when saving; worked out from the details when reading (to_representation)
    kind = serializers.ChoiceField(choices=list(KINDS), write_only=True)
    asset = AssetDetailSerializer(source='asset_details', required=False)
    loan = LoanDetailSerializer(source='loan_details', required=False)
    values = BalanceEntrySerializer(source='balance_entries', many=True, read_only=True)
    # A loan starts with what was owed on a date (from then on, real payments
    # change it); in escrow-account mode, also the escrow balance on that date
    starting_balance = serializers.DecimalField(max_digits=14, decimal_places=2, write_only=True, required=False, allow_null=True)
    starting_date = serializers.DateField(write_only=True, required=False, allow_null=True)
    escrow_starting_balance = serializers.DecimalField(max_digits=14, decimal_places=2, write_only=True, required=False, allow_null=True)

    class Meta:
        model = Account
        fields = ['id', 'name', 'kind', 'active', 'closed_date', 'asset', 'loan', 'values',
                  'starting_balance', 'starting_date', 'escrow_starting_balance']

    def to_representation(self, instance):
        data = super().to_representation(instance)
        data['kind'] = self._kind_of(instance)
        loan = getattr(instance, 'loan_details', None)
        if loan:
            from datetime import date as _date
            from . import loans
            today = _date.today()
            owed = loans.owed_on(instance, today)
            ahead = loans.projection(loan, owed, today)
            data['loan_status'] = {
                'owed': str(owed),
                'starting_balance': str(-(instance.last_balance or 0)),
                'balance_date': instance.balance_date,
                'payoff_date': ahead['payoff_date'],
                'payments_left': ahead['payments_left'],
                'escrow_account': loan.escrow_account_id,
                'escrow_balance': str(loans.balance_on(loan.escrow_account, today)) if loan.escrow_account_id else None,
            }
        return data

    def _kind_of(self, account):
        loan = getattr(account, 'loan_details', None)
        if loan:
            return {'Mortgage': 'mortgage', 'Auto': 'auto_loan'}.get(loan.loan_type, 'loan')
        asset = getattr(account, 'asset_details', None)
        return {'HOME': 'home', 'VEHICLE': 'vehicle', 'INVESTMENT': 'investment'}.get(asset.kind, 'other') if asset else 'other'

    def validate_name(self, value):
        return check_account_name(value, self.instance)

    def validate(self, data):
        kind = data.get('kind') or (self._kind_of(self.instance) if self.instance else None)
        is_loan = self.KINDS[kind][2]
        if self.instance is None:
            if is_loan and not data.get('loan_details'):
                raise serializers.ValidationError({'loan': 'A loan needs its terms: amount, rate, term and first payment date.'})
        if self.instance is not None and 'kind' in data and self.KINDS[data['kind']][2] != self.KINDS[self._kind_of(self.instance)][2]:
            raise serializers.ValidationError({'kind': 'An asset cannot become a loan, or the other way round.'})
        return data

    def _save_details(self, account, kind, asset_data, loan_data):
        account_type, detail_kind, is_loan = self.KINDS[kind]
        if is_loan:
            if loan_data is not None:
                LoanDetail.objects.update_or_create(liability_account=account, defaults={**loan_data, 'loan_type': detail_kind})
        else:
            AssetDetail.objects.update_or_create(account=account, defaults={**(asset_data or {}), 'kind': detail_kind})

    def create(self, validated_data):
        # All or nothing: an account must never be left without its details
        with db_transaction.atomic():
            return self._create(validated_data)

    def update(self, instance, validated_data):
        with db_transaction.atomic():
            return self._update(instance, validated_data)

    def _starting(self, validated_data):
        return (validated_data.pop('starting_balance', None), validated_data.pop('starting_date', None),
                validated_data.pop('escrow_starting_balance', None))

    def _ensure_escrow_account(self, account, start_date, escrow_balance):
        """In escrow-account mode, a loan gets its own escrow account (an asset)."""
        from datetime import date as _date
        loan = LoanDetail.objects.get(liability_account=account)
        if loan.escrow_mode != LoanDetail.EscrowMode.ACCOUNT or loan.escrow_account_id:
            return
        loan.escrow_account = Account.objects.create(
            name=unused_account_name(f'{account.name} escrow'), account_type='ESCROW',
            last_balance=escrow_balance or Decimal('0'), balance_date=start_date or _date.today(),
        )
        loan.save(update_fields=['escrow_account'])

    def _create(self, validated_data):
        kind = validated_data.pop('kind')
        asset_data = validated_data.pop('asset_details', None)
        loan_data = validated_data.pop('loan_details', None)
        starting_balance, starting_date, escrow_balance = self._starting(validated_data)
        account_type, _detail, is_loan = self.KINDS[kind]
        account = Account.objects.create(account_type=account_type, is_liability=is_loan, **validated_data)
        self._save_details(account, kind, asset_data, loan_data)
        if is_loan:
            # What was owed, and when: the amount borrowed at the start, unless said otherwise
            owed = starting_balance if starting_balance is not None else loan_data['original_balance']
            start = starting_date or loan_data.get('origination_date') or loan_data['first_payment_date']
            account.last_balance, account.balance_date = -owed, start
            account.save(update_fields=['last_balance', 'balance_date'])
            from .balances import mark_included
            mark_included(account)
            self._ensure_escrow_account(account, start, escrow_balance)
        return Account.objects.select_related('asset_details', 'loan_details').get(pk=account.pk)

    def _update(self, instance, validated_data):
        kind = validated_data.pop('kind', None) or self._kind_of(instance)
        asset_data = validated_data.pop('asset_details', None)
        loan_data = validated_data.pop('loan_details', None)
        _starting, _date, escrow_balance = self._starting(validated_data)   # the start is changed via a statement balance
        for field, value in validated_data.items():
            setattr(instance, field, value)
        instance.account_type = self.KINDS[kind][0]
        instance.save()
        if self.KINDS[kind][2]:
            if loan_data is not None:
                existing = LoanDetail.objects.filter(liability_account=instance).first()
                if existing:
                    for field, value in loan_data.items():
                        setattr(existing, field, value)
                    existing.loan_type = self.KINDS[kind][1]
                    existing.save()
                else:
                    self._save_details(instance, kind, None, loan_data)
                # Switched to escrow-account mode: the escrow account starts today
                self._ensure_escrow_account(instance, None, escrow_balance)
        elif asset_data is not None or self._kind_of(instance) != kind:
            self._save_details(instance, kind, asset_data, None)
        return Account.objects.select_related('asset_details', 'loan_details').get(pk=instance.pk)
