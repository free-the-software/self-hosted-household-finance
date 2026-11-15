# Copyright (C) 2026 free-the-software
# SPDX-License-Identifier: AGPL-3.0-or-later
# This file is part of Save More Money, under the GNU AGPL v3 or later: see LICENSE.
from django.urls import path, include
from rest_framework.routers import DefaultRouter
from . import views, auth

# 1. Create a Router for the ViewSets
router = DefaultRouter()
router.register(r'transactions', views.TransactionViewSet, basename='transaction')
router.register(r'accounts', views.AccountViewSet, basename='account')
router.register(r'subcategories', views.SubCategoryViewSet, basename='subcategory')
router.register(r'budgets', views.BudgetViewSet, basename='budget')
router.register(r'categories', views.CategoryViewSet)
router.register(r'recurring', views.RecurringTransactionViewSet)
router.register(r'import/inbox', views.StagedTransactionInboxViewSet, basename='import-inbox')
router.register(r'receipts', views.ReceiptViewSet, basename='receipt')
router.register(r'transaction-rules', views.TransactionRuleViewSet, basename='transaction-rule')
router.register(r'holdings', views.HoldingViewSet, basename='holding')
router.register(r'users', auth.UserViewSet, basename='user')

urlpatterns = [
    # 2. Add Custom Endpoints FIRST
    # (Always put custom paths before the router to prevent URL collisions)
    path('me/', auth.me, name='me'),
    path('logout/', auth.logout, name='logout'),
    path('sign-in/', auth.sign_in, name='sign-in'),
    path('account/password/', auth.change_password, name='change-password'),
    path('account/preferences/', auth.preferences, name='preferences'),
    path('import/parked/', views.parked_accounts_summary, name='parked-summary'),
    path('import/resolve/', views.resolve_parked_account, name='resolve-parked'),
    path('import/problems/', views.import_problems, name='import-problems'),
    path('system/status/', views.system_status, name='system-status'),
    path('system/settings/', views.system_settings, name='system-settings'),
    path('import/files/', views.import_files, name='import-files'),
    path('import/files/<int:pk>/signs/', views.import_file_signs, name='import-file-signs'),
    path('import/problems/<int:pk>/dismiss/', views.dismiss_import_problem, name='dismiss-import-problem'),
    path('upload/', views.FileUploadView.as_view(), name='file-upload'), 
    path('retention/', views.retention_settings, name='retention-settings'),
    path('retention/preview/', views.retention_preview, name='retention-preview'),
    path('retention/run/', views.retention_run, name='retention-run'),
    path('backups/', views.backup_overview, name='backup-overview'),
    path('backups/health/', views.backup_health, name='backup-health'),
    path('backups/schedule/', views.backup_schedule, name='backup-schedule'),
    path('backups/encryption/', views.backup_encryption, name='backup-encryption'),
    path('backups/run/', views.backup_run, name='backup-run'),
    path('backups/<int:pk>/', views.backup_file, name='backup-file'),
    path('net-worth/', views.net_worth, name='net-worth'),
    
    # 3. Add the Router URLs LAST
    path('', include(router.urls)),
]