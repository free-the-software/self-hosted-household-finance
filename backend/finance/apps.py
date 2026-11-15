# Copyright (C) 2026 free-the-software
# SPDX-License-Identifier: AGPL-3.0-or-later
# This file is part of Save More Money, under the GNU AGPL v3 or later: see LICENSE.
from django.apps import AppConfig
from django.db.models.signals import post_migrate


class FinanceConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'finance'

    def ready(self):
        # A brand-new ledger gets starter categories (see finance/starter.py)
        from .starter import add_starter_categories
        post_migrate.connect(add_starter_categories, sender=self)
