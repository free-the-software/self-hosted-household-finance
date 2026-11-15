# Copyright (C) 2026 free-the-software
# SPDX-License-Identifier: AGPL-3.0-or-later
# This file is part of Save More Money, under the GNU AGPL v3 or later: see LICENSE.
from django.conf import settings
from django.db import models
from django.db.models import Sum
from django.utils.translation import gettext_lazy as _

# ==========================================
# 1. Audit & Import Logs
# ==========================================
class BankImport(models.Model):
    """
    Logs every QFX/CSV file import attempt.
    Allows for 'Undo' functionality by deleting all transactions linked to a specific batch.
    """
    filename = models.CharField(max_length=255)
    imported_at = models.DateTimeField(auto_now_add=True)
    status = models.CharField(max_length=50)  # "Success", "Partial", "Failed"
    
    # Staging Flag
    is_committed = models.BooleanField(default=False)  # False = In Review, True = Done
    
    # ForeignKeys can keep null=True
    account = models.ForeignKey('Account', on_delete=models.CASCADE, null=True, blank=True) 
    
    # Statistics
    transactions_created = models.IntegerField(default=0)
    transactions_skipped = models.IntegerField(default=0)
    
    # Troubleshooting: for a file that couldn't be imported, why (shown in the Import Inbox)
    error_log = models.TextField(blank=True, default="")
    # The problem has been seen in the Import Inbox and put away
    dismissed = models.BooleanField(default=False)
    # The account balance this file set, if it set one: every row in the file
    # is in it, whatever the row's date (finance/balances.py)
    balance_as_of = models.DateField(null=True, blank=True)

    # Which way the file's amounts go (finance/csv_import.py decide_signs):
    # what the file showed, whether its amounts were reversed when staged,
    # and whether the Import Inbox needs to ask
    sign_facts = models.JSONField(null=True, blank=True)
    amounts_reversed = models.BooleanField(default=False)
    SIGN_SETTLED, SIGN_ASK, SIGN_WAITING = '', 'ask', 'waiting'     # waiting: for its account to be picked
    sign_state = models.CharField(max_length=10, blank=True, default='')

    def __str__(self):
        return f"{self.filename} ({self.imported_at.strftime('%Y-%m-%d %H:%M')})"

# ==========================================
# 2. Core Structure (Accounts & Categories)
# ==========================================
class Account(models.Model):
    # Primary Key matches legacy account_id
    account_id = models.AutoField(primary_key=True)
    name = models.CharField(max_length=255)
    
    # Watcher uses these to link QFX files to this account
    fid = models.CharField(max_length=50, blank=True, default="")
    account_number = models.CharField(max_length=100, blank=True, default="")
    institution = models.CharField(max_length=255, blank=True, default="")
    account_type = models.CharField(max_length=50, blank=True, default="")
    tax_status = models.CharField(max_length=50, blank=True, default="")
    owner = models.CharField(max_length=100, blank=True, default="")
    
    is_liability = models.BooleanField(default=False)
    invert_sign = models.BooleanField(default=False)
    current_balance = models.DecimalField(max_digits=15, decimal_places=2, default=0.00)
    
    # Numbers/Dates keep null=True
    interest_rate = models.DecimalField(max_digits=5, decimal_places=2, null=True, blank=True)
    credit_limit = models.DecimalField(max_digits=15, decimal_places=2, null=True, blank=True)
    due_day = models.IntegerField(null=True, blank=True)
    closed_date = models.DateField(null=True, blank=True)
    last_reconciled = models.DateTimeField(null=True, blank=True)
    total_tx_count = models.IntegerField(default=0)
    last_tx_date = models.DateField(null=True, blank=True)
    last_balance = models.DecimalField(max_digits=15, decimal_places=2, null=True, blank=True)
    balance_date = models.DateField(null=True, blank=True)
    
    show_forecast = models.BooleanField(
        default=False, 
        verbose_name="Show on Dashboard Forecast",
        help_text="If checked, this account will appear in the top section with future balance projections."
    )
    active = models.BooleanField(default=True)
    is_balance_calculated = models.BooleanField(default=False)
    # This bank's files write money out as positive (some card issuers' CSVs),
    # so imports reverse them; confirmed once the user has said which way it is
    import_amounts_reversed = models.BooleanField(default=False)
    import_sign_confirmed = models.BooleanField(default=False)

    def __str__(self):
        return self.name

    @classmethod
    def for_import(cls, identifier):
        """The account a bank file is for: by its account number, or a name remembered for it."""
        if not identifier:
            return None
        return cls.objects.filter(account_number=identifier).first() or \
            cls.objects.filter(aliases__identifier=identifier).first()


class AccountAlias(models.Model):
    """
    Another name a bank file uses for an account, remembered when the user
    picks the account for a file that wasn't recognised: most CSV downloads
    carry no account number, so their file name stands in for it
    (finance/csv_import.py).
    """
    account = models.ForeignKey(Account, on_delete=models.CASCADE, related_name='aliases')
    identifier = models.CharField(max_length=255, unique=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.identifier} -> {self.account.name}"


class Category(models.Model):
    CATEGORY_TYPES = [('INC', 'Income'), ('EXP', 'Expense'), ('XFR', 'Transfer')]
    name = models.CharField(max_length=100)
    type = models.CharField(max_length=3, choices=CATEGORY_TYPES)
    sort_order = models.IntegerField(default=0)

    class Meta:
        verbose_name_plural = "Categories"
        unique_together = ('name', 'type')
        ordering = ['sort_order', 'name']

    def __str__(self):
        return f"{self.get_type_display()}: {self.name}"

class SubCategory(models.Model):
    # PROTECT here and below: a category or subcategory is only removed through
    # finance/categories.py, which first moves what uses it somewhere else
    category = models.ForeignKey(Category, on_delete=models.PROTECT, related_name='subcategories')
    name = models.CharField(max_length=100)
    # The job the app itself gives it (finance/starter.py ROLES), e.g. a loan
    # payment's principal; found by this, so it can be renamed or moved freely
    role = models.CharField(max_length=30, unique=True, null=True, blank=True)

    class Meta:
        verbose_name_plural = "SubCategories"
        unique_together = ('category', 'name')

    def __str__(self):
        return f"{self.category.name} -> {self.name}"


class TransactionRule(models.Model):
    """A renaming rule. Matching and placement live in finance/rules.py."""
    rule_name = models.CharField(max_length=255)
    clean_merchant_name = models.CharField(max_length=255, blank=True, default="")
    subcategory = models.ForeignKey('SubCategory', on_delete=models.PROTECT)
    # Where it sits in the list rules are tried in, top first, like an access
    # list: the first rule that matches wins. Moved in Settings → Rules.
    position = models.IntegerField(default=0)
    is_active = models.BooleanField(default=True)
    hit_count = models.IntegerField(default=0)
    last_matched = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    conditions = models.JSONField(default=list) 
    logic_operator = models.CharField(max_length=3, choices=[('AND', 'AND'), ('OR', 'OR')], default='AND')

    class Meta:
        ordering = ['position', 'id']

    def __str__(self):
        return self.rule_name


# ==========================================
# 3. Transactions & Splits
# ==========================================
class Transaction(models.Model):
    # --- Status Choices ---
    STATUS_PENDING = 'P'
    STATUS_CLEARED = 'C'
    STATUS_RECONCILED = 'R'
    STATUS_CHOICES = [
        (STATUS_PENDING, 'Pending'),
        (STATUS_CLEARED, 'Cleared'),
        (STATUS_RECONCILED, 'Reconciled'),
    ]
    
    TYPE_CHOICES = [
        ('INC', 'Income'), 
        ('EXP', 'Expense'), 
        ('XFR', 'Transfer'),
        ('Uncategorized', 'Uncategorized')
    ]

    # --- Relationships ---
    account = models.ForeignKey(Account, on_delete=models.CASCADE, related_name='transactions')
    linked_transaction = models.ForeignKey(
        'self', 
        null=True, 
        blank=True, 
        on_delete=models.SET_NULL, 
        help_text="The other half of this transfer"
    )
    import_batch = models.ForeignKey(BankImport, on_delete=models.SET_NULL, null=True, blank=True, related_name='transactions')
    # For the principal and escrow parts of a loan payment (in the loan and
    # escrow accounts): the payment they came from. Deleting the payment
    # deletes its parts, so the loan's balance can never keep a payment that
    # no longer exists.
    source_transaction = models.ForeignKey(
        'self', on_delete=models.CASCADE, null=True, blank=True, related_name='payment_parts'
    )
    subcategory = models.ForeignKey(SubCategory, on_delete=models.SET_NULL, null=True, blank=True)
    
    # --- QFX File Context ---
    fid = models.CharField(max_length=50, blank=True, default="")
    account_number = models.CharField(max_length=100, blank=True, default="")
    
    # --- Core Banking Data ---
    dtposted = models.DateField(db_index=True)
    dtuser = models.DateField(null=True, blank=True)
    imported_at = models.DateTimeField(auto_now_add=True)
    
    trnamt = models.DecimalField(max_digits=12, decimal_places=2)
    name = models.CharField(max_length=255)         
    bank_memo = models.TextField(blank=True, default="")         
    personal_memo = models.TextField(blank=True, default="")         
    
    # --- Reference Data ---
    check_number = models.CharField(max_length=50, blank=True, default="")
    ref_number = models.CharField(max_length=255, blank=True, default="")
    fitid = models.CharField(max_length=255, blank=True, default="")
    
    # --- Merchant & Banking Metadata ---
    sic = models.CharField(max_length=255, blank=True, default="")
    mcc = models.IntegerField(null=True, blank=True) # Int keeps null=True
    payee_id = models.CharField(max_length=255, blank=True, default="")
    trntype = models.CharField(max_length=50, blank=True, default="")
    
    # --- Audit & Correction ---
    file_source = models.CharField(max_length=255, blank=True, default="")
    raw_data = models.TextField(blank=True, default="") 
    correct_fitid = models.CharField(max_length=255, blank=True, default="")
    correct_action = models.TextField(blank=True, default="")
    related_id = models.CharField(max_length=255, blank=True, default="")
    
    # --- Tracking ---
    is_transfer = models.BooleanField(default=False)
    is_manual = models.BooleanField(default=False)
    status = models.CharField(max_length=1, choices=STATUS_CHOICES, default=STATUS_PENDING)
    type = models.CharField(max_length=20, choices=TYPE_CHOICES, default='Uncategorized')
    # The date of the account's bank balance that first included this
    # transaction; empty while no balance has counted it (finance/balances.py)
    in_balance_as_of = models.DateField(null=True, blank=True)

    class Meta:
        ordering = ['-dtposted']
        # One bank transaction once. Only real bank ids count: transactions with
        # none (entered by hand) can share every other detail.
        constraints = [models.UniqueConstraint(
            fields=['account', 'fitid', 'trnamt', 'dtposted'], condition=~models.Q(fitid=''),
            name='one_transaction_per_bank_id')]

    def __str__(self):
        return f"{self.dtposted} - {self.name} (${self.trnamt})"

    def save(self, *args, **kwargs):
        # Cleared and dated up to the account's bank balance: in that balance,
        # unless something more exact was already set (an imported row)
        if self.in_balance_as_of is None and self.status != self.STATUS_PENDING and self.account_id:
            anchor = Account.objects.filter(pk=self.account_id).values_list('balance_date', flat=True).first()
            if anchor and self.dtposted and (self.dtposted if not hasattr(self.dtposted, 'date') else self.dtposted.date()) <= anchor:
                self.in_balance_as_of = anchor
                if kwargs.get('update_fields') is not None:
                    kwargs['update_fields'] = list(kwargs['update_fields']) + ['in_balance_as_of']
        super().save(*args, **kwargs)

    # --- Helper Properties for Splits ---
    @property
    def is_split(self):
        return self.splits.exists()

    @property
    def assigned_amount(self):
        """Returns sum of splits if they exist, otherwise returns the main amount"""
        if self.splits.exists():
            return self.splits.aggregate(Sum('amount'))['amount__sum'] or 0
        return self.trnamt


class TransactionSplit(models.Model):
    transaction = models.ForeignKey(Transaction, on_delete=models.CASCADE, related_name='splits')
    subcategory = models.ForeignKey(SubCategory, on_delete=models.PROTECT)
    amount = models.DecimalField(max_digits=12, decimal_places=2)
    memo = models.CharField(max_length=255, blank=True, default="") 

    def __str__(self):
        return f"Split: {self.transaction.name} -> {self.subcategory.name} (${self.amount})"


#============================================
# 4. Budgeting
#============================================
class Budget(models.Model):
    subcategory = models.ForeignKey(SubCategory, on_delete=models.PROTECT, related_name="budgets")
    amount = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    year = models.PositiveIntegerField()
    month = models.PositiveIntegerField()

    class Meta:
        db_table = 'finance_budget'
        unique_together = ('subcategory', 'year', 'month')
        ordering = ['-year', '-month', 'subcategory']

#============================================
# 5. Recurring Transactions
#============================================
class RecurringTransaction(models.Model):
    class Frequency(models.TextChoices):
        WEEKLY = 'WEEKLY', _('Weekly')
        BIWEEKLY = 'BIWEEKLY', _('Bi-Weekly')
        SEMI_MONTHLY = 'SEMI_MONTHLY', _('Semi-Monthly')
        MONTHLY = 'MONTHLY', _('Monthly')
        SEMI_ANNUALLY = 'SEMI_ANNUALLY', _('Semi-Annually')
        ANNUALLY = 'ANNUALLY', _('Annually')

    name = models.CharField(max_length=200)
    amount = models.DecimalField(max_digits=12, decimal_places=2)
    account = models.ForeignKey('Account', on_delete=models.CASCADE, related_name='recurring_transactions')
    
    frequency = models.CharField(
        max_length=20,
        choices=Frequency.choices,
        default=Frequency.MONTHLY,
    )
    
    start_date = models.DateField()
    end_date = models.DateField(null=True, blank=True)
    is_active = models.BooleanField(default=True)
    
    create_days_before = models.PositiveIntegerField(
        default=0, 
        help_text="Days before the due date to create the transaction in the register."
    )
    
    subcategory = models.ForeignKey(
        SubCategory, 
        on_delete=models.SET_NULL, 
        null=True, 
        blank=True
    )

    def __str__(self):
        return f"{self.name} - ${self.amount}"

# ==========================================
# 6. Staging for Transaction Review & Import
# ==========================================
class StagedTransaction(models.Model):
    """
    A temporary holding pen for transactions from a BankImport.
    These rows are displayed in the "Resolution Center" UI.
    Once approved, they are converted into real Transaction records and deleted from here.
    """
    batch = models.ForeignKey(BankImport, on_delete=models.CASCADE, related_name='staged_transactions', null=True, blank=True)
    account = models.ForeignKey(Account, on_delete=models.CASCADE, null=True, blank=True)
    
    unregistered_account_id = models.CharField(max_length=100, blank=True, default="")
    fitid = models.CharField(max_length=255)
    subcategory = models.ForeignKey('SubCategory', on_delete=models.SET_NULL, null=True, blank=True)
    date = models.DateField()
    amount = models.DecimalField(max_digits=12, decimal_places=2)
    name = models.CharField(max_length=255)
    bank_memo = models.TextField(blank=True, default="")
    personal_memo = models.TextField(blank=True, default="")
    
    match_proposal = models.ForeignKey('Transaction', on_delete=models.SET_NULL, null=True, blank=True)
    
    # Status Flags
    STATUS_NEW = 'NEW'       # not in the ledger yet
    STATUS_MATCH = 'MATCH'   # the same as a pending transaction entered by hand
    STATUS_NEAR = 'NEAR'     # probably the same as one, but something differs; the user decides
    STATUS_DUPE = 'DUPE'     # already imported
    
    match_type = models.CharField(max_length=10, default=STATUS_NEW)
    # Why it was matched, in words for the Import Inbox ("same amount, 12 days apart")
    match_reason = models.CharField(max_length=200, blank=True, default="")
    # Split lines set in the Import Inbox before approving, as
    # [{"subcategory": id, "amount": "-12.34", "memo": ""}]. They become the
    # transaction's split lines when it is added as a new one.
    splits = models.JSONField(default=list, blank=True)
    # Set once the user has edited this row in the Import Inbox. A matched
    # entry then takes the edited payee, category, memo and split lines.
    edited = models.BooleanField(default=False)

    def __str__(self):
        return f"[Staged] {self.name} (${self.amount})"


#=========================================
# 7. Receipt Images & OCR Data
#==========================================
class Receipt(models.Model):

    STATUS_CHOICES = [
        ('PROCESSING', 'Processing'),
        ('REVIEW', 'Needs Review'),
        ('MATCHED', 'Matched'),
        ('ORPHANED', 'No Match Found')
    ]

    # Empty once the image has been removed by a retention policy
    image = models.ImageField(upload_to='receipts/%Y/%m/', blank=True)
    original_filename = models.CharField(max_length=255)
    created_at = models.DateTimeField(auto_now_add=True)
    # Set when a retention policy deleted the image file. The record itself
    # (date, amounts, text, transaction link) is always kept.
    image_expired_at = models.DateTimeField(null=True, blank=True)
    
    # Metadata
    vendor_name = models.CharField(max_length=255, blank=True, default="")
    date = models.DateField(blank=True, null=True)
    
    # Financials
    total_amount = models.DecimalField(max_digits=10, decimal_places=2, null=True)
    tax_amount = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    
    raw_text = models.TextField(blank=True, default="")
    
    transaction = models.ForeignKey(
        'Transaction', 
        on_delete=models.SET_NULL, 
        null=True, 
        blank=True, 
        related_name='receipt'
    )
    
    status = models.CharField(
        max_length=20, 
        choices=STATUS_CHOICES, 
        default='PROCESSING'
    )
    approved = models.BooleanField(default=False)

    def __str__(self):
        return f"Receipt: {self.original_filename} (${self.total_amount})"

#==========================================
# 8. Storage Retention Policies
#==========================================
class RetentionPolicy(models.Model):
    """
    How long one kind of stored file is kept. The kinds are defined in
    finance/retention.py. No row, or retention_days = NULL, means keep forever.
    """
    category = models.CharField(max_length=30, unique=True)
    retention_days = models.PositiveIntegerField(null=True, blank=True)
    updated_at = models.DateTimeField(auto_now=True)
    updated_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True)

    def __str__(self):
        period = f"{self.retention_days} days" if self.retention_days else "keep forever"
        return f"{self.category}: {period}"


class RetentionRun(models.Model):
    """A record of each clean-up that actually deleted files."""
    ran_at = models.DateTimeField(auto_now_add=True)
    trigger = models.CharField(max_length=20)  # "manual" or "scheduled"
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True)
    files_deleted = models.IntegerField(default=0)
    bytes_freed = models.BigIntegerField(default=0)
    details = models.JSONField(default=dict)

    class Meta:
        ordering = ['-ran_at']

    def __str__(self):
        return f"{self.ran_at:%Y-%m-%d %H:%M} {self.trigger}: {self.files_deleted} files"


#==========================================
# 9. Backups
#==========================================
class BackupSettings(models.Model):
    """Backup schedule and encryption setup. A single row, loaded with load()."""
    OFF, DAILY, WEEKLY, MONTHLY = 'off', 'daily', 'weekly', 'monthly'
    FREQUENCY_CHOICES = [(OFF, 'Off'), (DAILY, 'Daily'), (WEEKLY, 'Weekly'), (MONTHLY, 'Monthly')]

    # Only the public half of the key pair is stored; it can encrypt, not
    # decrypt. The private half is shown once and never saved.
    encryption_public_key = models.TextField(blank=True, default="")
    # A deliberate choice to write backups unencrypted
    encryption_disabled = models.BooleanField(default=False)

    # Database-only backups
    db_frequency = models.CharField(max_length=10, choices=FREQUENCY_CHOICES, default=DAILY)
    db_hour = models.PositiveSmallIntegerField(default=2)       # 0-23, server time
    db_weekday = models.PositiveSmallIntegerField(default=6)    # 0 = Monday ... 6 = Sunday
    db_monthday = models.PositiveSmallIntegerField(default=1)   # 1-28

    # Full backups: database + receipt images
    full_frequency = models.CharField(max_length=10, choices=FREQUENCY_CHOICES, default=WEEKLY)
    full_hour = models.PositiveSmallIntegerField(default=3)
    full_weekday = models.PositiveSmallIntegerField(default=6)
    full_monthday = models.PositiveSmallIntegerField(default=1)

    updated_at = models.DateTimeField(auto_now=True)
    updated_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True)

    @classmethod
    def load(cls):
        obj, _ = cls.objects.get_or_create(pk=1)
        return obj

    @property
    def encryption_ready(self):
        """Backups may run once a key exists or encryption was turned off on purpose."""
        return bool(self.encryption_public_key) or self.encryption_disabled


class Backup(models.Model):
    """One backup attempt and, when it succeeded, the file it produced."""
    KIND_DB, KIND_FULL = 'db', 'full'
    KIND_CHOICES = [(KIND_DB, 'Database'), (KIND_FULL, 'Full (database + receipt images)')]
    RUNNING, OK, FAILED = 'running', 'ok', 'failed'
    STATUS_CHOICES = [(RUNNING, 'Running'), (OK, 'Completed'), (FAILED, 'Failed')]

    kind = models.CharField(max_length=10, choices=KIND_CHOICES)
    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default=RUNNING)
    trigger = models.CharField(max_length=20, default='manual')   # "manual" or "scheduled"
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True)
    # The schedule slot this run was for, so a slot is not backed up twice
    scheduled_for = models.DateTimeField(null=True, blank=True)
    # "hostname:pid" of the process doing the work, so a run whose process
    # has died (a restart mid-backup) can be recognised at once
    worker = models.CharField(max_length=100, blank=True, default="")

    started_at = models.DateTimeField(auto_now_add=True)
    finished_at = models.DateTimeField(null=True, blank=True)

    filename = models.CharField(max_length=255, blank=True, default="")
    size = models.BigIntegerField(default=0)
    sha256 = models.CharField(max_length=64, blank=True, default="")
    encrypted = models.BooleanField(default=False)
    error = models.TextField(blank=True, default="")

    class Meta:
        ordering = ['-started_at', '-id']

    def __str__(self):
        return f"{self.get_kind_display()} backup {self.started_at:%Y-%m-%d %H:%M} ({self.status})"


#==========================================
# 10. Loan Details (Linking Loans to Assets)
#==========================================
class AssetDetail(models.Model):
    """
    What is known about something you own that isn't a bank account: a home,
    a vehicle, or anything else of value. Its worth over time is recorded in
    BalanceEntry.
    """
    class Kind(models.TextChoices):
        HOME = 'HOME', 'Home'
        VEHICLE = 'VEHICLE', 'Vehicle'
        INVESTMENT = 'INVESTMENT', 'Investment account'
        OTHER = 'OTHER', 'Other asset'

    account = models.OneToOneField(Account, on_delete=models.CASCADE, related_name='asset_details')
    kind = models.CharField(max_length=10, choices=Kind.choices, default=Kind.OTHER)
    purchase_date = models.DateField(null=True, blank=True)
    purchase_price = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    # Your share, for something owned jointly with someone outside this ledger
    ownership_percent = models.DecimalField(max_digits=5, decimal_places=2, default=100)

    # Home
    address = models.CharField(max_length=255, blank=True, default="")
    property_type = models.CharField(max_length=50, blank=True, default="")      # single family, condo...
    year_built = models.IntegerField(null=True, blank=True)
    square_feet = models.IntegerField(null=True, blank=True)

    # Vehicle
    vehicle_year = models.IntegerField(null=True, blank=True)
    make = models.CharField(max_length=50, blank=True, default="")
    model = models.CharField(max_length=50, blank=True, default="")
    trim = models.CharField(max_length=50, blank=True, default="")
    vin = models.CharField(max_length=17, blank=True, default="")
    mileage = models.IntegerField(null=True, blank=True)

    notes = models.TextField(blank=True, default="")

    def __str__(self):
        return f"{self.get_kind_display()}: {self.account.name}"


class LoanDetail(models.Model):
    class CompoundingPeriod(models.TextChoices):
        DAILY = 'DAILY', 'Daily'
        MONTHLY = 'MONTHLY', 'Monthly'
        SEMI_ANNUALLY = 'SEMI_ANNUALLY', 'Semi-Annually'
        ANNUALLY = 'ANNUALLY', 'Annually'

    class PaymentFrequency(models.TextChoices):
        WEEKLY = 'WEEKLY', 'Weekly'
        BIWEEKLY = 'BIWEEKLY', 'Bi-Weekly'
        MONTHLY = 'MONTHLY', 'Monthly'

    class InterestType(models.TextChoices):
        FIXED = 'FIXED', 'Fixed'
        VARIABLE = 'VARIABLE', 'Variable'

    class EscrowMode(models.TextChoices):
        # The escrow part of each payment is spent at once (tax and insurance categories)
        EXPENSE = 'EXPENSE', 'Expense it with each payment'
        # It goes into an escrow account; the lender's tax and insurance
        # payments are then recorded as withdrawals from it
        ACCOUNT = 'ACCOUNT', 'Hold it in an escrow account'

    # --- Core Account Links ---
    liability_account = models.OneToOneField(Account, on_delete=models.CASCADE, related_name='loan_details')
    # The home or vehicle the loan is secured on, if any
    asset_account = models.OneToOneField(Account, on_delete=models.SET_NULL, null=True, blank=True, related_name='linked_loan')

    # --- Loan Details ---
    loan_type = models.CharField(max_length=50, default='Mortgage') # Mortgage, Auto, Personal
    lender = models.CharField(max_length=255, blank=True, default="")
    # Only the last digits, so a statement can be matched without storing the number
    account_last4 = models.CharField(max_length=4, blank=True, default="")
    original_balance = models.DecimalField(max_digits=12, decimal_places=2)
    term_months = models.IntegerField(default=360)
    interest_rate = models.DecimalField(max_digits=5, decimal_places=3, help_text="e.g., 6.500")
    interest_type = models.CharField(max_length=15, choices=InterestType.choices, default=InterestType.FIXED)
    
    origination_date = models.DateField(null=True, blank=True)
    first_payment_date = models.DateField()
    compounding_period = models.CharField(max_length=20, choices=CompoundingPeriod.choices, default=CompoundingPeriod.MONTHLY)
    payment_schedule = models.CharField(max_length=20, choices=PaymentFrequency.choices, default=PaymentFrequency.MONTHLY)
    # Principal and interest per payment, as on the loan papers. Worked out
    # from the amount, rate and term when left empty.
    payment_amount = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)

    # --- Payment & Routing ---
    # Used for auto-categorizing transactions and building reminders
    interest_category = models.ForeignKey('SubCategory', on_delete=models.SET_NULL, null=True, blank=True, related_name='loan_interest')
    default_extra_principal = models.DecimalField(max_digits=10, decimal_places=2, default=0.00)
    
    # Escrow and other monthly costs paid with the loan (mortgages)
    property_tax_category = models.ForeignKey('SubCategory', on_delete=models.SET_NULL, null=True, blank=True, related_name='loan_property_tax')
    property_tax_amount = models.DecimalField(max_digits=10, decimal_places=2, default=0.00)
    
    homeowners_insurance_category = models.ForeignKey('SubCategory', on_delete=models.SET_NULL, null=True, blank=True, related_name='loan_insurance')
    homeowners_insurance_amount = models.DecimalField(max_digits=10, decimal_places=2, default=0.00)
    pmi_amount = models.DecimalField(max_digits=10, decimal_places=2, default=0.00)
    pmi_category = models.ForeignKey('SubCategory', on_delete=models.SET_NULL, null=True, blank=True, related_name='loan_pmi')
    escrow_mode = models.CharField(max_length=10, choices=EscrowMode.choices, default=EscrowMode.EXPENSE)
    escrow_account = models.OneToOneField(Account, on_delete=models.SET_NULL, null=True, blank=True, related_name='escrow_for_loan')
    # e.g. "Remove PMI at 78% loan-to-value"
    notes = models.TextField(blank=True, default="")

    def __str__(self):
        return f"{self.loan_type} for {self.liability_account.name}"


class BalanceEntry(models.Model):
    """
    What an account was worth, or owed, on a date: a home's estimated value,
    a loan's balance from a statement. Amounts are positive; whether it is an
    asset or a debt comes from the account. Net worth uses the latest entry
    on or before each date.
    """
    account = models.ForeignKey(Account, on_delete=models.CASCADE, related_name='balance_entries')
    date = models.DateField()
    amount = models.DecimalField(max_digits=14, decimal_places=2)
    # Where the figure came from, e.g. "Zillow estimate", "March statement"
    source = models.CharField(max_length=100, blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['account', 'date']
        constraints = [models.UniqueConstraint(fields=['account', 'date'], name='one_balance_entry_per_account_per_day')]

    def __str__(self):
        return f"{self.account.name} {self.date}: {self.amount}"

class UserProfile(models.Model):
    """What the app keeps about a user beyond Django's own fields."""
    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='profile')
    # Signed in with a password someone else chose (the first admin's, or one
    # an admin set): nothing else works until they choose their own
    must_change_password = models.BooleanField(default=False)
    # Their order for the menu, as page addresses ("/net-worth"...); the first
    # is the page the app opens on. Empty: the usual order.
    menu_order = models.JSONField(default=list, blank=True)

    def __str__(self):
        return f"{self.user} profile"


def must_change_password(user):
    profile = getattr(user, 'profile', None) if user.is_authenticated else None
    return bool(profile and profile.must_change_password)


def require_password_change(user, required=True):
    UserProfile.objects.update_or_create(user=user, defaults={'must_change_password': required})


class SystemSettings(models.Model):
    """
    Everyday settings an admin can change in Settings (finance/system.py).
    A single row, loaded with load(). Empty means the default: the value
    from .env, or the app's own.
    """
    update_checks = models.BooleanField(null=True, blank=True)
    disk_warn_gb = models.FloatField(null=True, blank=True)
    idle_minutes = models.PositiveIntegerField(null=True, blank=True)
    upload_max_mb = models.PositiveIntegerField(null=True, blank=True)
    updated_at = models.DateTimeField(auto_now=True)
    updated_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True)

    @classmethod
    def load(cls):
        obj, _ = cls.objects.get_or_create(pk=1)
        return obj
