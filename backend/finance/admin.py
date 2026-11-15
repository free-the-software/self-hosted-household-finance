# Copyright (C) 2026 free-the-software
# SPDX-License-Identifier: AGPL-3.0-or-later
# This file is part of Save More Money, under the GNU AGPL v3 or later: see LICENSE.
from django.contrib import admin
from .models import (
    Account, 
    Category, 
    SubCategory, 
    Transaction, 
    TransactionSplit,
    TransactionRule, 
    BankImport, 
    Budget
)

@admin.register(Account)
class AccountAdmin(admin.ModelAdmin):
    list_display = ('name', 'account_id', 'current_balance', 'fid', 'account_number', 'last_tx_date')
    search_fields = ('name', 'institution', 'account_number')

@admin.register(Category)
class CategoryAdmin(admin.ModelAdmin):
    list_display = ('name', 'type')
    list_filter = ('type',)

@admin.register(SubCategory)
class SubCategoryAdmin(admin.ModelAdmin):
    list_display = ('name', 'category')
    list_filter = ('category__type', 'category__name')
    search_fields = ('name', 'category__name')

class TransactionSplitInline(admin.TabularInline):
    model = TransactionSplit
    extra = 1

@admin.register(Transaction)
class TransactionAdmin(admin.ModelAdmin):
    # UPDATED: Using new field names (dtposted, name, trnamt)
    list_display = ('dtposted', 'name', 'trnamt', 'subcategory', 'status', 'account')
    list_filter = ('status', 'dtposted', 'account', 'subcategory', 'is_manual')
    search_fields = ('name', 'trnamt', 'memo', 'fitid')
    date_hierarchy = 'dtposted'
    
    # Allows adding splits directly inside the Transaction page
    inlines = [TransactionSplitInline]

@admin.register(TransactionRule)
class TransactionRuleAdmin(admin.ModelAdmin):
    list_display = ('rule_name', 'logic_operator', 'clean_merchant_name', 'subcategory', 'position', 'is_active')
    list_filter = ('is_active', 'logic_operator', 'subcategory')
    search_fields = ('rule_name', 'subcategory__name')
    ordering = ('position', 'id')

@admin.register(BankImport)
class BankImportAdmin(admin.ModelAdmin):
    list_display = ('filename', 'imported_at', 'status', 'transactions_created', 'transactions_skipped')
    list_filter = ('status', 'imported_at')
    readonly_fields = ('imported_at', 'error_log')

@admin.register(Budget)
class BudgetAdmin(admin.ModelAdmin):
    list_display = ('year', 'month', 'subcategory', 'amount')
    list_filter = ('year', 'month', 'subcategory__category')
    search_fields = ('subcategory__name',)
