# Copyright (C) 2026 free-the-software
# SPDX-License-Identifier: AGPL-3.0-or-later
# This file is part of Save More Money, under the GNU AGPL v3 or later: see LICENSE.
import os
import logging
from rest_framework import viewsets, status
from rest_framework.views import APIView
from rest_framework.decorators import action, api_view, throttle_classes
from rest_framework.response import Response
from rest_framework.pagination import PageNumberPagination
from rest_framework.parsers import MultiPartParser, FormParser
from rest_framework.exceptions import ValidationError
from collections import defaultdict
from django.db.models import Max, Min, Q, Sum, Count, CharField, Case, DecimalField, Exists, F, OuterRef, Subquery, When
from django.db.models.functions import Cast
from django.db import transaction, IntegrityError
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation
import re

from .models import (
    Transaction, Account, SubCategory, 
    TransactionRule, Budget, Category, 
    RecurringTransaction, StagedTransaction, TransactionSplit, Receipt,
    BalanceEntry, LoanDetail, AccountAlias, BankImport,
)
from .serializers import (
    TransactionSerializer, AccountSerializer, 
    SubCategorySerializer, BudgetSerializer, 
    RecurringTransactionSerializer, CategorySerializer, 
    StagedTransactionSerializer, ReceiptSerializer, TransactionRuleSerializer,
    HoldingSerializer, BalanceEntrySerializer,
)
from .services import apply_tax_split, reclassify_staged, suggest_receipt_matches
from .csv_import import decide_signs, mostly_money_in
from . import account_order, budget_planning, budget_suggestions, categories, system, forecast, loans, networth, receipt_reader, retention, backups, rules
from .throttles import VisitorLimit, UserLimit, UploadLimit, HeavyLimit
from .models import RetentionRun, Backup, BackupSettings
from django.conf import settings
from django.http import FileResponse, Http404
from django.core.exceptions import SuspiciousFileOperation
from django.utils.text import get_valid_filename
import uuid
from django.utils import timezone

logger = logging.getLogger(__name__)

# ==========================================
# 1. CORE FINANCE VIEWSETS
# ==========================================

def transactions_with_details():
    """
    Transactions together with everything the serializer shows for each one,
    fetched in a handful of queries for the whole list. Without this, each row
    costs several extra queries (its splits, receipt and linked transfer).
    """
    return Transaction.objects.select_related(
        'account',
        'subcategory',
        'subcategory__category',
        'linked_transaction',
        'linked_transaction__account',
    ).prefetch_related('splits__subcategory', 'receipt')


def whole_number(value, name, low, high):
    """Reads a number from the request, or answers 400 instead of crashing."""
    try:
        number = int(value)
    except (TypeError, ValueError):
        raise ValidationError({name: f'{name} must be a whole number.'})
    if not low <= number <= high:
        raise ValidationError({name: f'{name} must be between {low} and {high}.'})
    return number


def month_bounds(year, month):
    """First day of the month and first day of the next: a range the date index can use."""
    start = date(year, month, 1)
    return start, date(year + (month == 12), month % 12 + 1, 1)


def budget_years(today):
    """
    The years to offer on the Budget page: from the first year with
    transactions or budgets to next year (or a later one already budgeted).
    """
    years = [today.year, today.year + 1]
    first_tx = Transaction.objects.order_by('dtposted').values_list('dtposted', flat=True).first()
    if first_tx:
        years.append(first_tx.year)
    budgeted = Budget.objects.aggregate(first=Min('year'), last=Max('year'))
    years += [year for year in budgeted.values() if year]
    return list(range(min(years), max(years) + 1))


def budget_summary(year, first_month, last_month):
    """
    Budget against actual for each subcategory over the months first..last
    of a year (none when last is 0), and the totals: income, expenses, and
    savings (income less expenses). A split transaction counts through its
    split lines; transfers count in neither total.
    """
    zero = Decimal('0.00')
    months = range(first_month, last_month + 1)
    budget_map, actual_map = defaultdict(Decimal), defaultdict(Decimal)
    if months:
        start, _ = month_bounds(year, first_month)
        _, end = month_bounds(year, last_month)
        for row in Budget.objects.filter(year=year, month__in=months).values('subcategory_id').annotate(total=Sum('amount')):
            budget_map[row['subcategory_id']] += row['total']
        for row in Transaction.objects.filter(dtposted__gte=start, dtposted__lt=end, subcategory__isnull=False, splits__isnull=True) \
                .values('subcategory_id').annotate(total=Sum('trnamt')):
            actual_map[row['subcategory_id']] += row['total']
        for row in TransactionSplit.objects.filter(transaction__dtposted__gte=start, transaction__dtposted__lt=end) \
                .values('subcategory_id').annotate(total=Sum('amount')):
            actual_map[row['subcategory_id']] += row['total']

    summary = {key: zero for key in ('income_budget', 'income_actual', 'expense_budget', 'expense_actual',
                                     'savings_budget', 'savings_actual')}
    rows = []
    for sub in SubCategory.objects.select_related('category').order_by('category__sort_order', 'category__name', 'name'):
        kind = sub.category.type
        budget, actual = budget_map.get(sub.id, zero), actual_map.get(sub.id, zero)
        rows.append({'id': sub.id, 'name': sub.name, 'category_name': sub.category.name, 'type': kind,
                     'is_income': kind == 'INC', 'budget': budget, 'actual': actual})
        if kind == 'INC':
            summary['income_budget'] += budget
            summary['income_actual'] += actual
        elif kind == 'EXP':
            summary['expense_budget'] += budget
            summary['expense_actual'] += actual
    # Expenses are negative, so income plus expenses is what's left over
    summary['savings_budget'] = summary['income_budget'] - summary['expense_budget']
    summary['savings_actual'] = summary['income_actual'] + summary['expense_actual']
    return {'summary': summary, 'rows': rows}


class StandardResultsSetPagination(PageNumberPagination):
    page_size = 50
    page_size_query_param = 'page_size'
    max_page_size = 1000

    def paginate_queryset(self, queryset, request, view=None):
        if 'page' not in request.query_params:
            return None
        return super().paginate_queryset(queryset, request, view)

def in_subcategory(queryset, subcategory_id):
    """
    The transactions that count toward a subcategory, the same way the budget
    adds them up: an unsplit transaction by its own category, a split one by
    its parts. Each gets `subcategory_amount`, the part that counts: the whole
    amount, or the sum of its split lines in this subcategory.
    """
    parts = TransactionSplit.objects.filter(transaction=OuterRef('pk'))
    parts_here = parts.filter(subcategory_id=subcategory_id)
    share = parts_here.order_by().values('transaction').annotate(total=Sum('amount')).values('total')[:1]
    return queryset.annotate(has_split_lines=Exists(parts)).filter(
        Q(has_split_lines=False, subcategory_id=subcategory_id) | Q(Exists(parts_here))
    ).annotate(subcategory_amount=Case(
        When(has_split_lines=True, then=Subquery(share)),
        default=F('trnamt'),
        output_field=DecimalField(max_digits=19, decimal_places=2),
    ))


class TransactionViewSet(viewsets.ModelViewSet):
    pagination_class = StandardResultsSetPagination
    serializer_class = TransactionSerializer

    def get_queryset(self):
        queryset = transactions_with_details().order_by('-dtposted', '-id')
        params = self.request.query_params

        account_id = params.get('account')
        if account_id:
            queryset = queryset.filter(account_id=whole_number(account_id, 'account', 1, 2**31 - 1))

        status = params.get('status')
        if status:
            queryset = queryset.filter(status=status)

        search = (params.get('search') or '').strip()
        if search:
            matches = Q(name__icontains=search) | Q(personal_memo__icontains=search)
            # A search that looks like a number also matches amounts ("107" finds -107.00)
            if re.fullmatch(r'-?[\d,]*\.?\d+', search):
                queryset = queryset.annotate(amount_text=Cast('trnamt', CharField()))
                matches |= Q(amount_text__icontains=search.replace(',', ''))
            queryset = queryset.filter(matches)

        subcat = params.get('subcategory')
        if subcat == 'none':
            # Uncategorized: no category, no split lines, and not a transfer
            queryset = queryset.filter(subcategory__isnull=True, linked_transaction__isnull=True, splits__isnull=True)
        elif subcat:
            queryset = in_subcategory(queryset, whole_number(subcat, 'subcategory', 1, 2**31 - 1))

        # ==========================================
        # TIMEFRAME FILTERS FOR URL PARAMETERS
        # ==========================================
        month, year = params.get('month'), params.get('year')
        if month:
            month = whole_number(month, 'month', 1, 12)
        if year:
            year = whole_number(year, 'year', 1900, 2200)
        if month and year:
            first, next_first = month_bounds(year, month)
            queryset = queryset.filter(dtposted__gte=first, dtposted__lt=next_first)
        elif year:
            # A year, or (until_month) the year so far, as on the Budget page
            until = params.get('until_month')
            if until:
                _, end = month_bounds(year, whole_number(until, 'until_month', 1, 12))
                queryset = queryset.filter(dtposted__gte=date(year, 1, 1), dtposted__lt=end)
            else:
                queryset = queryset.filter(dtposted__year=year)
        elif month:
            queryset = queryset.filter(dtposted__month=month)

        return queryset

    def perform_create(self, serializer):
        amount = serializer.validated_data.get('trnamt')
        if amount is None: 
             amount = serializer.validated_data.get('amount', Decimal('0.00'))
             
        subcategory = serializer.validated_data.get('subcategory')
        final_type = 'Uncategorized'
        
        if subcategory and subcategory.category:
            final_type = subcategory.category.type 
        else:
            final_type = 'INC' if amount >= 0 else 'EXP'
            
        serializer.save(type=final_type, trnamt=amount)

    def perform_update(self, serializer):
        if 'subcategory' in serializer.validated_data:
            subcategory = serializer.validated_data['subcategory']
        else:
            subcategory = serializer.instance.subcategory

        if 'trnamt' in serializer.validated_data:
            amount = serializer.validated_data['trnamt']
        elif 'amount' in serializer.validated_data:
            amount = serializer.validated_data['amount']
        else:
            amount = serializer.instance.trnamt

        final_type = 'Uncategorized'
        if subcategory and subcategory.category:
            final_type = subcategory.category.type
        else:
            final_type = 'INC' if amount >= 0 else 'EXP'
            
        serializer.save(type=final_type, trnamt=amount)

    @action(detail=False, methods=['post'], url_path='preview-rule')
    def preview_rule(self, request):
        """
        The transactions a rule would match, newest first, decided by the same
        code that applies rules on import (finance.rules).
        """
        data = request.data
        conditions = data.get('conditions', [])
        logic_operator = 'OR' if data.get('logic_operator') == 'OR' else 'AND'
        if not conditions:
            return Response([])
        problem = rules.conditions_problem(conditions)
        if problem:
            return Response({'error': f'This rule could not be previewed. {problem}'}, status=status.HTTP_400_BAD_REQUEST)

        try:
            matching = []
            for tx_id, name, amount, bank_memo, personal_memo, account_name in Transaction.objects.order_by('-dtposted', '-id').values_list(
                'id', 'name', 'trnamt', 'bank_memo', 'personal_memo', 'account__name'
            ).iterator():
                facts = rules.Facts(name, amount, f'{bank_memo or ""} {personal_memo or ""}', account_name or '')
                if rules.rule_matches(conditions, logic_operator, facts):
                    matching.append(tx_id)
                    if len(matching) >= self.PREVIEW_LIMIT:
                        break
            matches = transactions_with_details().filter(id__in=matching).order_by('-dtposted', '-id')
            serializer = TransactionSerializer(matches, many=True)
            return Response(serializer.data)
        except Exception:
            # The detail goes to the server log, never to the browser
            logger.exception("Preview rule failed")
            return Response({'error': 'The rule could not be previewed because of a server error.'}, status=500)

    # A preview lists at most this many transactions
    PREVIEW_LIMIT = 500

    @action(detail=False, methods=['post'], url_path='create-rule')
    def create_rule(self, request):
        data = request.data
        problem = rules.conditions_problem(data.get('conditions', []))
        if problem:
            return Response({'error': f'The rule could not be created. {problem}'}, status=status.HTTP_400_BAD_REQUEST)
        
        try:
            rule = TransactionRule.objects.create(
                rule_name=data.get('rule_name'),
                conditions=data.get('conditions', []),
                logic_operator='OR' if data.get('logic_operator') == 'OR' else 'AND',
                clean_merchant_name=data.get('clean_merchant_name') or '',
                subcategory_id=data.get('subcategory'),
                is_active=True,
            )
            # Into the list just above the first rule that would catch it first
            rules.place_new(rule)

            selected_ids = data.get('selected_ids', [])
            update_count = 0
            
            if selected_ids:
                txs = Transaction.objects.filter(id__in=selected_ids)
                
                for tx in txs:
                    updated = False
                    if rule.clean_merchant_name:
                        tx.name = rule.clean_merchant_name
                        updated = True
                    
                    if rule.subcategory:
                        tx.subcategory = rule.subcategory
                        updated = True
                    
                    if updated:
                        tx.save()
                        update_count += 1
                        
            return Response({
                'status': 'success', 
                'message': f"Rule '{rule.rule_name}' created. Updated {update_count} historical transactions."
            })
        except Exception:
            logger.exception("Create rule failed")
            return Response(
                {'error': 'The rule could not be created. Check that it has a name and a valid subcategory.'},
                status=status.HTTP_400_BAD_REQUEST
            )

    # ==========================================
    # Manual linking
    # ==========================================

    @action(detail=True, methods=['get'])
    def proposed_links(self, request, pk=None):
        source_tx = self.get_object()
        
        # 1. Exact inverse amount using proper DB field
        target_amount = source_tx.trnamt * -1
        
        # 2. Safely handle Date vs Datetime using proper DB field
        tx_date = source_tx.dtposted.date() if isinstance(source_tx.dtposted, datetime) else source_tx.dtposted
        
        date_min = tx_date - timedelta(days=5)
        date_max = tx_date + timedelta(days=5)
        
        # 3. Find matches using trnamt and dtposted!
        candidates = transactions_with_details().filter(
            trnamt=target_amount,
            dtposted__range=(date_min, date_max),
            linked_transaction__isnull=True
        ).exclude(pk=source_tx.pk).exclude(account=source_tx.account)
        

        serializer = self.get_serializer(candidates, many=True)
        return Response(serializer.data)

    @action(detail=False, methods=['post'], url_path='link')
    def link_transactions(self, request):
        """Manually links two transactions together."""
        source_id = request.data.get('source_id')
        target_id = request.data.get('target_id')

        if not source_id or not target_id:
            return Response({'error': 'Both source_id and target_id are required.'}, status=400)

        try:
            t1 = Transaction.objects.get(id=source_id)
            t2 = Transaction.objects.get(id=target_id)

            # Safely handle assignment whether the field is a ForeignKey or IntegerField
            try:
                t1.linked_transaction = t2
                t2.linked_transaction = t1
            except ValueError:
                # Fallback if the field expects an integer ID instead of a model instance
                t1.linked_transaction = t2.id
                t2.linked_transaction = t1.id

            t1.linked_transaction_account = t2.account.name if t2.account else 'Unknown'
            t2.linked_transaction_account = t1.account.name if t1.account else 'Unknown'

            t1.save()
            t2.save()

            return Response({'status': 'success', 'message': 'Transactions linked.'})
            
        except Transaction.DoesNotExist:
            return Response({'error': 'One or both transactions not found in the database.'}, status=404)
        except Exception:
            logger.exception("Linking transactions %s and %s failed", source_id, target_id)
            return Response({'error': 'The transactions could not be linked because of a server error.'}, status=500)

    @action(detail=True, methods=['post'], url_path='unlink')
    def unlink_transaction(self, request, pk=None):
        """Unlinks a transaction and removes the link from its partner."""
        try:
            t1 = self.get_object()

            if t1.linked_transaction:
                try:
                    # Handle both ID lookup (if integer) or object lookup (if ForeignKey)
                    partner_id = getattr(t1.linked_transaction, 'id', t1.linked_transaction)
                    t2 = Transaction.objects.get(id=partner_id)
                    t2.linked_transaction = None
                    t2.linked_transaction_account = None
                    t2.save()
                except (Transaction.DoesNotExist, ValueError):
                    pass # If partner is missing or deleted, just clean up this one

            t1.linked_transaction = None
            t1.linked_transaction_account = None
            t1.save()

            return Response({'status': 'success', 'message': 'Transaction unlinked.'})
        except Http404:
            raise  # a missing transaction is a 404, not a server error
        except Exception:
            logger.exception("Unlinking transaction %s failed", pk)
            return Response({'error': 'The transaction could not be unlinked because of a server error.'}, status=500)
        
class AccountViewSet(viewsets.ModelViewSet):
    queryset = Account.objects.all()
    serializer_class = AccountSerializer

    def get_queryset(self):
        queryset = super().get_queryset()
        if self.action == 'list':
            # Assets and loans are managed on the Net Worth page (/api/holdings/),
            # not in the bank-account lists and dropdowns
            queryset = queryset.filter(asset_details__isnull=True, loan_details__isnull=True, escrow_for_loan__isnull=True)
            # Everyday accounts first, the most used first (finance/account_order.py)
            queryset = account_order.ordered(queryset)
        return queryset

    def destroy(self, request, *args, **kwargs):
        account = self.get_object()
        if Transaction.objects.filter(account=account).exists():
            return Response(
                {'error': 'Cannot delete account with existing transactions. Please close it via settings instead.'}, 
                status=status.HTTP_400_BAD_REQUEST
            )
        return super().destroy(request, *args, **kwargs)

    @action(detail=True, methods=['post'], url_path='close')
    def close_account(self, request, pk=None):
        account = self.get_object()
        if hasattr(account, 'is_active'):
            account.is_active = False
            account.save()
            return Response({'status': 'Account closed'})
        return Response({'error': 'Model missing is_active field'}, status=500)

    @action(detail=False, methods=['get'], url_path='dashboard')
    def dashboard(self, request):
        accounts = Account.objects.filter(asset_details__isnull=True, loan_details__isnull=True, escrow_for_loan__isnull=True)
        
        checking_data = []
        other_data = []

        for acc in accounts:
            balance = acc.last_balance or Decimal('0.00')
            bal_date = acc.balance_date or date.today()

            is_forecast_account = getattr(acc, 'show_forecast', False) or \
                                  (acc.account_type or "").upper() in ['CHECKING', 'CASH', 'JOINT CHECKING']

            if is_forecast_account:
                # The same calculation as the account's ledger (finance/forecast.py)
                projection = forecast.project(acc)
                checking_data.append({
                    'id': acc.account_id,
                    'name': acc.name,
                    'last_bank_balance': balance,
                    'balance_date': bal_date,
                    'current_balance': projection['current_balance'],
                    'projected_balance': projection['lowest_balance'],
                    'will_decrement': projection['deficit_date'] is not None,
                    'deficit_date': projection['deficit_date'],
                    'is_balance_calculated': getattr(acc, 'is_balance_calculated', False),
                })
            else:
                other_data.append({
                    'id': acc.account_id,
                    'name': acc.name,
                    'type': (acc.account_type or "").upper(),
                    'last_bank_balance': balance,
                    'balance_date': bal_date,
                    'is_balance_calculated': getattr(acc, 'is_balance_calculated', False)
                })
                
        return Response({
            'checking': checking_data,
            'other': other_data
        })

    @action(detail=True, methods=['get'], url_path='ledger')
    def ledger(self, request, pk=None):
        account = self.get_object()
        
        projection = forecast.project(account)
        start_balance, start_date = projection['start_balance'], projection['start_date']

        ledger_data = []
        
        ledger_data.append({
            'id': 'start',
            'date': start_date,
            'description': '*** CALCULATED BALANCE ***' if getattr(account, 'is_balance_calculated', False) else '*** BANK VERIFIED BALANCE ***',
            'amount': start_balance,
            'running_balance': start_balance,
            'status': 'VERIFIED',
            'is_balance_calculated': getattr(account, 'is_balance_calculated', False)
        })

        for tx, running_balance in projection['rows']:
            ledger_data.append({
                'id': tx.id,
                'date': tx.dtposted,
                'description': tx.name, 
                'amount': tx.trnamt,
                'running_balance': running_balance,
                'status': 'PROJECTED'
            })

        return Response(ledger_data)

    @action(detail=True, methods=['get'], url_path='amortization')
    def amortization(self, request, pk=None):
        import calendar
        from datetime import timedelta
        
        account = self.get_object()
        
        # Check if this account actually has loan details attached
        try:
            loan = account.loan_details
        except AttributeError:
            return Response({"error": "No loan details attached to this account."}, status=400)

        # 1. Setup Starting Variables
        P = float(loan.original_balance)
        annual_rate = float(loan.interest_rate) / 100.0
        months = loan.term_months
        r_monthly = annual_rate / 12.0

        # 2. Calculate Standard Monthly P&I Payment (M)
        if r_monthly > 0:
            M = P * (r_monthly * (1 + r_monthly)**months) / ((1 + r_monthly)**months - 1)
        else:
            M = P / months

        # 3. Handle Frequencies and Extra Payments
        is_biweekly = loan.payment_schedule == 'BIWEEKLY'
        is_weekly = loan.payment_schedule == 'WEEKLY'
        
        if is_biweekly:
            periods_per_year = 26
            base_pi_payment = M / 2
            days_between = 14
        elif is_weekly:
            periods_per_year = 52
            base_pi_payment = M / 4
            days_between = 7
        else: # Standard Monthly
            periods_per_year = 12
            base_pi_payment = M
            days_between = None 
            
        r_period = annual_rate / periods_per_year
        extra_principal = float(loan.default_extra_principal)
        
        # Escrow (Taxes & Ins) for total cashflow tracking. 
        # (Converted from a monthly amount to a per-period amount)
        tax = float(loan.property_tax_amount)
        insurance = float(loan.homeowners_insurance_amount)
        
        escrow_multiplier = 12 / periods_per_year 
        period_tax = tax * escrow_multiplier
        period_ins = insurance * escrow_multiplier

        # 4. Prepare for the Amortization Loop
        schedule = []
        balance = P
        current_date = loan.first_payment_date
        period = 1
        total_interest = 0.0
        
        # Run until paid off (Capped at 4x the term length to prevent infinite loops from bad data)
        while balance > 0.01 and period <= (months * 4): 
            interest_payment = balance * r_period
            
            # Principal is whatever is left of the base payment, PLUS your extra principal
            principal_payment = base_pi_payment - interest_payment + extra_principal
            
            # Catch the final payment so we don't overpay and go negative
            if balance < principal_payment:
                principal_payment = balance
                
            balance -= principal_payment
            total_interest += interest_payment
            
            # Total cash leaving your checking account this period
            total_cashflow = principal_payment + interest_payment + period_tax + period_ins
            
            schedule.append({
                'period': period,
                'date': current_date.strftime('%Y-%m-%d'),
                'total_payment': round(total_cashflow, 2),
                'principal': round(principal_payment, 2),
                'interest': round(interest_payment, 2),
                'extra_principal': round(extra_principal, 2),
                'tax': round(period_tax, 2),
                'insurance': round(period_ins, 2),
                'balance': round(abs(balance), 2)
            })
            
            # 5. Increment Date Safely
            if days_between:
                current_date += timedelta(days=days_between)
            else:
                # Add exactly one month, clamping to the last day of the month if necessary
                m = current_date.month - 1 + 1
                year = current_date.year + m // 12
                month = m % 12 + 1
                day = min(current_date.day, calendar.monthrange(year, month)[1])
                current_date = current_date.replace(year=year, month=month, day=day)
                
            period += 1

        # 6. Return the payload for React
        return Response({
            "summary": {
                "original_balance": round(P, 2),
                "interest_rate": float(loan.interest_rate),
                "standard_monthly_pi": round(M, 2),
                "actual_period_pi": round(base_pi_payment + extra_principal, 2),
                "total_period_cashflow": round(base_pi_payment + extra_principal + period_tax + period_ins, 2),
                "frequency": loan.payment_schedule,
                "total_interest_paid": round(total_interest, 2),
                "payoff_date": schedule[-1]['date'] if schedule else None,
                "total_payments": len(schedule)
            },
            "schedule": schedule
        })

class SubCategoryViewSet(viewsets.ModelViewSet):
    queryset = SubCategory.objects.all().order_by('name')
    serializer_class = SubCategorySerializer

    def destroy(self, request, *args, **kwargs):
        """Only when nothing uses it; otherwise POST remove/, saying where things go."""
        sub = self.get_object()
        used = categories.usage(sub)
        if sub.role or any(value for key, value in used.items() if key != 'role'):
            return Response({'error': 'Something uses this subcategory: choose where it should go (remove/).', 'usage': used},
                            status=status.HTTP_400_BAD_REQUEST)
        sub.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)

    @action(detail=True, methods=['get'])
    def usage(self, request, pk=None):
        """What uses it, for the delete window, and whether it can be left uncategorized."""
        sub = self.get_object()
        return Response({**categories.usage(sub), 'cannot_leave_uncategorized': categories.can_leave_uncategorized(sub)})

    @action(detail=True, methods=['post'])
    def remove(self, request, pk=None):
        """Deletes it: everything moves to {"replacement": id}, or with null is left uncategorized."""
        sub = self.get_object()
        replacement_id = request.data.get('replacement')
        try:
            replacement = SubCategory.objects.get(pk=replacement_id) if replacement_id else None
            categories.delete_subcategory(sub, replacement)
        except SubCategory.DoesNotExist:
            return Response({'error': 'That replacement no longer exists.'}, status=status.HTTP_400_BAD_REQUEST)
        except categories.CategoryError as error:
            return Response({'error': str(error)}, status=status.HTTP_400_BAD_REQUEST)
        return Response({'deleted': True})


class CategoryViewSet(viewsets.ModelViewSet):
    queryset = Category.objects.prefetch_related('subcategories')
    serializer_class = CategorySerializer

    def destroy(self, request, *args, **kwargs):
        """Only when empty; otherwise POST remove/, saying where its subcategories go."""
        category = self.get_object()
        if category.subcategories.exists():
            return Response({'error': 'It still has subcategories: choose where they should go (remove/).'},
                            status=status.HTTP_400_BAD_REQUEST)
        category.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)

    @action(detail=True, methods=['get'])
    def usage(self, request, pk=None):
        return Response(categories.category_usage(self.get_object()))

    @action(detail=True, methods=['post'])
    def remove(self, request, pk=None):
        """
        Deletes it. Its subcategories move to {"move_to": category id}, or are
        deleted with everything sent to {"replacement": subcategory id}, or
        with {"uncategorize": true} left uncategorized.
        """
        category = self.get_object()
        try:
            move_to = Category.objects.get(pk=request.data['move_to']) if request.data.get('move_to') else None
            replacement = SubCategory.objects.get(pk=request.data['replacement']) if request.data.get('replacement') else None
            categories.delete_category(category, move_to=move_to, replacement=replacement,
                                       uncategorize=bool(request.data.get('uncategorize')))
        except (Category.DoesNotExist, SubCategory.DoesNotExist):
            return Response({'error': 'What you chose no longer exists.'}, status=status.HTTP_400_BAD_REQUEST)
        except categories.CategoryError as error:
            return Response({'error': str(error)}, status=status.HTTP_400_BAD_REQUEST)
        return Response({'deleted': True})
        
    @action(detail=False, methods=['post'], url_path='reorder')
    def reorder(self, request):
        ordered_ids = request.data.get('ordered_ids', [])
        for index, cat_id in enumerate(ordered_ids):
            Category.objects.filter(id=cat_id).update(sort_order=index)
        return Response({'status': 'success'})


class TransactionRuleViewSet(viewsets.ModelViewSet):
    queryset = TransactionRule.objects.select_related('subcategory__category')
    serializer_class = TransactionRuleSerializer

    def list(self, request, *args, **kwargs):
        """
        The rules in the order they are tried, each with its place in that
        order and any rule it repeats or that always matches before it.
        """
        all_rules = rules.ordered(self.get_queryset())
        found = rules.analyse(all_rules)
        data = self.get_serializer(all_rules, many=True).data
        for row in data:
            row.update(found.get(row['id'], {}))
        return Response(data)

    def perform_create(self, serializer):
        rules.place_new(serializer.save())

    @action(detail=True, methods=['post'])
    def move(self, request, pk=None):
        """
        Moves a rule in the list: {"to": place} (1 is the top) or
        {"before": another rule's id}. Returns the whole list, as listed.
        """
        rule = self.get_object()
        before, to = request.data.get('before'), request.data.get('to')
        try:
            if before is not None:
                if not TransactionRule.objects.filter(pk=int(before)).exists():
                    return Response({'error': 'That rule no longer exists.'}, status=status.HTTP_404_NOT_FOUND)
                rules.move(rule, before=int(before))
            elif to is not None:
                rules.move(rule, to=int(to))
            else:
                return Response({'error': 'Say where to move it: "to" a place, or "before" a rule.'}, status=status.HTTP_400_BAD_REQUEST)
        except (TypeError, ValueError):
            return Response({'error': 'The place must be a whole number.'}, status=status.HTTP_400_BAD_REQUEST)
        return self.list(request)


class BudgetViewSet(viewsets.ModelViewSet):
    queryset = Budget.objects.all()
    serializer_class = BudgetSerializer

    @action(detail=False, methods=['get'], url_path='summary')
    def summary(self, request):
        """
        Budget against actual for one month (?year=&month=), or for the year
        so far (?year=&period=year: every month of a past year, or up to this
        month of this one). Savings is income less expenses; transfers
        between your own accounts are neither, so aren't counted.
        """
        today = date.today()
        year = whole_number(request.query_params.get('year', today.year), 'year', 1900, 2200)
        if request.query_params.get('period') == 'year':
            last = 12 if year < today.year else today.month if year == today.year else 0
            first = 1
        else:
            first = last = whole_number(request.query_params.get('month', today.month), 'month', 1, 12)
        return Response({'year': year, 'first_month': first, 'last_month': last, 'years': budget_years(today),
                         **budget_summary(year, first, last)})

    @action(detail=False, methods=['post'])
    def plan(self, request):
        """
        Sets up a year's budget from the year before (finance/budget_planning.py).
        With "preview": true, only says what it would come to.
        """
        data = request.data
        try:
            result = budget_planning.plan_year(
                whole_number(data.get('year'), 'year', 1900, 2200),
                source=data.get('source', 'budget'),
                percent=Decimal(str(data.get('percent') or 0)),
                income_percent=Decimal(str(data['income_percent'])) if data.get('income_percent') not in (None, '') else None,
                expense_percent=Decimal(str(data['expense_percent'])) if data.get('expense_percent') not in (None, '') else None,
                rounding=int(data.get('rounding') or 0),
                kinds=data.get('kinds') or ['EXP', 'INC'],
                replace=data.get('mode') == 'replace',
                save=not data.get('preview'),
            )
        except (InvalidOperation, ValueError):
            raise ValidationError({'percent': 'percent and rounding must be numbers.'})
        except budget_planning.PlanError as error:
            return Response({'error': str(error)}, status=status.HTTP_400_BAD_REQUEST)
        return Response(result)

    @action(detail=False, methods=['get'])
    def suggestions(self, request):
        """
        Next year's budget suggested per category from history, with reasons
        (finance/budget_suggestions.py): ?year=&years=1-3&recent=1&yearly=as_paid|spread
        &kinds=EXP,INC&rounding=0|1|10|100.
        """
        params = request.query_params
        try:
            result = budget_suggestions.suggest(
                whole_number(params.get('year', date.today().year + 1), 'year', 1900, 2200),
                years_back=whole_number(params.get('years', 3), 'years', 1, 3),
                recent_first=params.get('recent', '1') not in ('0', 'false', 'no'),
                yearly_bills=params.get('yearly', 'as_paid'),
                kinds=[k for k in params.get('kinds', 'EXP,INC').split(',') if k],
                rounding=whole_number(params.get('rounding', 1), 'rounding', 0, 100),
            )
        except budget_suggestions.SuggestionError as error:
            return Response({'error': str(error)}, status=status.HTTP_400_BAD_REQUEST)
        return Response(result)

    @action(detail=False, methods=['post'], url_path='apply-suggestions')
    def apply_suggestions(self, request):
        """Sets the budget from reviewed suggestions: {year, mode: fill|replace, rows: [{subcategory, months}]}."""
        data = request.data
        try:
            result = budget_suggestions.apply(
                whole_number(data.get('year'), 'year', 1900, 2200),
                data.get('rows') or [],
                replace=data.get('mode') == 'replace',
            )
        except budget_suggestions.SuggestionError as error:
            return Response({'error': str(error)}, status=status.HTTP_400_BAD_REQUEST)
        return Response(result)

    @action(detail=False, methods=['post'], url_path='set-target')
    def set_target(self, request):
        data = request.data
        sub_id = data.get('subcategory_id')
        year = whole_number(data.get('year'), 'year', 1900, 2200)
        month = whole_number(data.get('month'), 'month', 1, 12)
        try:
            amount = Decimal(str(data.get('amount')))
            if not amount.is_finite():
                raise InvalidOperation
        except InvalidOperation:
            raise ValidationError({'amount': 'amount must be a number.'})
        apply_future = data.get('apply_future', False)
        months = range(month, 13) if apply_future else [month]

        # 0 clears the month: it shows as 0 either way, and a saved 0 would
        # look "already budgeted" to planning
        if amount == 0:
            Budget.objects.filter(subcategory_id=sub_id, year=year, month__in=months).delete()
        else:
            for m in months:
                Budget.objects.update_or_create(subcategory_id=sub_id, year=year, month=m, defaults={'amount': amount})

        return Response({'status': 'updated'})

class RecurringTransactionViewSet(viewsets.ModelViewSet):
    queryset = RecurringTransaction.objects.select_related('subcategory__category')
    serializer_class = RecurringTransactionSerializer


class StagedTransactionInboxViewSet(viewsets.ModelViewSet):
    queryset = StagedTransaction.objects.select_related(
        'account', 'subcategory', 'match_proposal__subcategory'
    ).prefetch_related('match_proposal__splits').order_by('date', 'id')
    serializer_class = StagedTransactionSerializer

    def list(self, request, *args, **kwargs):
        # Rows can go stale after staging (the account was mapped later, or
        # the ledger changed), so re-check anything still marked NEW.
        reclassify_staged(StagedTransaction.objects.all())
        return super().list(request, *args, **kwargs)
          
    @action(detail=False, methods=['post'], url_path='clear-duplicates')
    def clear_duplicates(self, request):
        deleted_count, _ = StagedTransaction.objects.filter(match_type='DUPE').delete()
        return Response({'status': 'success', 'deleted': deleted_count})

    @action(detail=True, methods=['post'])
    def approve(self, request, pk=None):
        """
        Adds an imported transaction to the ledger. With a match (proposed, or
        chosen with manual_match_id) the pending transaction entered by hand
        is updated with the bank's details instead: the bank's date and amount
        win, the entry's category, memo and splits stay. payee='entry' (the
        default) keeps the payee you typed and puts the bank's description in
        the bank memo; payee='bank' takes the bank's description. With
        as_new=true a proposed match is ignored and a new transaction is added.
        """
        staged = self.get_object()
        manual_match_id = request.data.get('manual_match_id')
        as_new = request.data.get('as_new') is True
        payee = request.data.get('payee', 'entry')
        if payee not in ('entry', 'bank'):
            return Response({'error': "payee must be 'entry' or 'bank'."}, status=status.HTTP_400_BAD_REQUEST)

        if not staged.account_id:
            return Response(
                {'error': 'This transaction is not mapped to an account yet. Map it first.'},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        target_tx = None
        if manual_match_id:
            target_tx = Transaction.objects.filter(id=manual_match_id).first()
            if not target_tx:
                return Response({'error': 'The transaction to link to no longer exists.'}, status=status.HTTP_404_NOT_FOUND)
        elif staged.match_proposal and not as_new:
            target_tx = staged.match_proposal

        # The bank's amount replaces the entry's. A split entry's lines would
        # then no longer add up, so that has to be put right first, by hand or
        # by editing the split in the Import Inbox (which replaces the lines).
        if target_tx and not staged.edited and target_tx.trnamt != staged.amount and target_tx.splits.exists():
            return Response(
                {'error': f'Your entry "{target_tx.name}" is split, and the bank\'s amount ({staged.amount}) differs from it '
                          f'({target_tx.trnamt}). Change its split lines to add up to the bank\'s amount, then match again.'},
                status=status.HTTP_409_CONFLICT
            )

        try:
            # The ledger allows one row per (account, fitid, amount, date).
            # atomic() keeps the session usable if that rule rejects this one.
            # A row from a file that set the account's balance is in that
            # balance, whatever its date (finance/balances.py)
            in_balance_as_of = staged.batch.balance_as_of if staged.batch_id else None
            with transaction.atomic():
                if target_tx:
                    target_tx.fitid = staged.fitid
                    target_tx.dtposted = staged.date
                    target_tx.status = 'C'
                    target_tx.trnamt = staged.amount
                    if in_balance_as_of:
                        target_tx.in_balance_as_of = in_balance_as_of
                    if staged.edited:
                        # Edited in the Import Inbox: the edits win over the entry
                        target_tx.name = staged.name
                        target_tx.subcategory = staged.subcategory
                        target_tx.personal_memo = staged.personal_memo or ''
                        target_tx.bank_memo = staged.bank_memo
                        target_tx.save()
                        target_tx.splits.all().delete()
                        for line in staged.splits or []:
                            TransactionSplit.objects.create(
                                transaction=target_tx, subcategory_id=line['subcategory'],
                                amount=Decimal(str(line['amount'])), memo=line.get('memo', ''),
                            )
                    elif payee == 'entry':
                        # The payee you typed ("ATM") is kept; the bank's
                        # description is still on record in the bank memo
                        target_tx.bank_memo = staged.bank_memo or staged.name
                    else:
                        target_tx.name = staged.name
                        target_tx.bank_memo = staged.bank_memo
                    target_tx.save()
                else:
                    new_tx = Transaction.objects.create(
                        account=staged.account,
                        subcategory=staged.subcategory,
                        fitid=staged.fitid,
                        dtposted=staged.date,
                        trnamt=staged.amount,
                        name=staged.name,
                        bank_memo=staged.bank_memo or "",
                        personal_memo=staged.personal_memo or "",
                        status='C',
                        in_balance_as_of=in_balance_as_of,
                    )
                    # Split lines set in the Import Inbox
                    for line in staged.splits or []:
                        TransactionSplit.objects.create(
                            transaction=new_tx, subcategory_id=line['subcategory'],
                            amount=Decimal(str(line['amount'])), memo=line.get('memo', ''),
                        )
        except IntegrityError:
            staged.match_type = StagedTransaction.STATUS_DUPE
            staged.save(update_fields=['match_type'])
            return Response(
                {'error': 'This transaction is already in the ledger. It has been marked as a duplicate.'},
                status=status.HTTP_409_CONFLICT
            )
            
        staged.delete()
        return Response({'status': 'approved'})

    @action(detail=True, methods=['post'])
    def reject(self, request, pk=None):
        staged = self.get_object()
        staged.delete()
        return Response({'status': 'rejected'})

# ==========================================
# 2. UNREGISTERED ACCOUNT PARKING LOGIC
# ==========================================

@api_view(['GET'])
def parked_accounts_summary(request):
    summary = StagedTransaction.objects.filter(
        account__isnull=True, 
        unregistered_account_id__isnull=False
    ).values('unregistered_account_id').annotate(
        tx_count=Count('id')
    ).order_by('-tx_count')
    
    return Response(summary)

PAYMENT_WORDS = ('PAYMENT', 'AUTOPAY', 'THANK YOU')


@api_view(['GET'])
def import_files(request):
    """
    The files with rows waiting in the Import Inbox: whether to ask which way
    their amounts go (with a row as the example), and a warning when a card's
    file is mostly money in.
    """
    files = []
    batches = BankImport.objects.filter(staged_transactions__isnull=False).select_related('account').distinct().order_by('-imported_at')
    for batch in batches:
        rows = list(batch.staged_transactions.order_by('date', 'id').values('name', 'amount', 'account__account_type'))
        account_type = next((row['account__account_type'] for row in rows if row['account__account_type']), None)
        example = next((row for row in rows if row['amount'] and not any(w in (row['name'] or '').upper() for w in PAYMENT_WORDS)), None)
        files.append({
            'id': batch.id,
            'filename': batch.filename,
            'account': batch.account.name if batch.account else None,
            'rows': len(rows),
            'ask': batch.sign_state == BankImport.SIGN_ASK,
            'example': {'name': example['name'], 'amount': example['amount']} if example else None,
            'card_mostly_money_in': account_type == 'CREDIT CARD' and mostly_money_in([row['amount'] for row in rows]),
        })
    return Response(files)


@api_view(['POST'])
def import_file_signs(request, pk):
    """
    The user's answer about a file's amounts: "right" as shown, or "reverse"
    them. Kept with the account, so its next files are read the same way.
    """
    try:
        batch = BankImport.objects.get(pk=pk)
    except BankImport.DoesNotExist:
        return Response({'error': 'That file is no longer in the Import Inbox.'}, status=status.HTTP_404_NOT_FOUND)
    action = request.data.get('action')
    if action not in ('right', 'reverse'):
        return Response({'error': 'action must be "right" or "reverse".'}, status=status.HTTP_400_BAD_REQUEST)
    with transaction.atomic():
        rows = StagedTransaction.objects.filter(batch=batch)
        if action == 'reverse':
            rows.update(amount=-F('amount'))
            # Matches were made with the old amounts: look again
            rows.update(match_type=StagedTransaction.STATUS_NEW, match_proposal=None, match_reason='')
            batch.amounts_reversed = not batch.amounts_reversed
        batch.sign_state = BankImport.SIGN_SETTLED
        batch.save(update_fields=['amounts_reversed', 'sign_state'])
        account = batch.account or next((row.account for row in rows.select_related('account') if row.account), None)
        if account:
            Account.objects.filter(pk=account.pk).update(
                import_amounts_reversed=batch.amounts_reversed, import_sign_confirmed=True)
    if action == 'reverse':
        reclassify_staged(StagedTransaction.objects.filter(batch=batch))
    return Response({'reversed': batch.amounts_reversed})


@api_view(['GET'])
def import_problems(request):
    """Bank files that couldn't be imported, and why, until they're put away."""
    failed = BankImport.objects.filter(status='Failed', dismissed=False).order_by('-imported_at')[:20]
    return Response([
        {'id': batch.id, 'filename': batch.filename, 'when': batch.imported_at, 'problem': batch.error_log}
        for batch in failed
    ])


@api_view(['POST'])
def dismiss_import_problem(request, pk):
    BankImport.objects.filter(pk=pk, status='Failed').update(dismissed=True)
    return Response({'dismissed': True})


@api_view(['POST'])
def resolve_parked_account(request):
    unregistered_id = request.data.get('unregistered_id')
    real_account_id = request.data.get('real_account_id')

    if not unregistered_id or not real_account_id:
        return Response({"error": "Missing required fields."}, status=status.HTTP_400_BAD_REQUEST)

    try:
        real_account = Account.objects.get(pk=real_account_id)
    except Account.DoesNotExist:
        return Response({"error": "Selected account does not exist."}, status=status.HTTP_404_NOT_FOUND)

    parked_txs = StagedTransaction.objects.filter(
        account__isnull=True, 
        unregistered_account_id=unregistered_id
    )
    
    mapped_ids = list(parked_txs.values_list('id', flat=True))

    # Remembered, so the next file with this identifier goes straight to this
    # account (a CSV's file name, or an account number written differently)
    if unregistered_id != real_account.account_number:
        AccountAlias.objects.update_or_create(identifier=unregistered_id, defaults={'account': real_account})

    # Which way each file's amounts go can be settled now its account is known
    for batch in BankImport.objects.filter(staged_transactions__id__in=mapped_ids).distinct():
        amounts_reversed, settled = decide_signs(batch.sign_facts, real_account)
        if amounts_reversed:
            StagedTransaction.objects.filter(batch=batch, id__in=mapped_ids).update(amount=-F('amount'))
        batch.account = real_account
        batch.amounts_reversed = amounts_reversed
        batch.sign_state = BankImport.SIGN_SETTLED if settled else BankImport.SIGN_ASK
        batch.save(update_fields=['account', 'amounts_reversed', 'sign_state'])

    parked_txs.update(
        account=real_account,
        unregistered_account_id=""
    )

    # These were staged with no account, so they skipped the duplicate and
    # pending-match checks. Run them now that the account is known.
    mapped = StagedTransaction.objects.filter(id__in=mapped_ids)
    reclassify_staged(mapped)
    duplicates = mapped.filter(match_type=StagedTransaction.STATUS_DUPE).count()

    message = f"Successfully mapped {len(mapped_ids)} transactions to {real_account.name}."
    if duplicates:
        message += f" {duplicates} already in the ledger were marked as duplicates."

    return Response({"message": message, "mapped": len(mapped_ids), "duplicates": duplicates})

# ==========================================
# 3. STORAGE RETENTION
# ==========================================

def _retention_state(request):
    policies = retention.get_policies()
    usage = retention.usage()
    last_run = RetentionRun.objects.first()
    return {
        # Anyone signed in can look; only staff can change policies or delete
        'can_edit': bool(request.user.is_staff),
        'categories': [
            {**category, 'retention_days': policies[category['key']], 'usage': usage[category['key']]}
            for category in retention.CATEGORIES
        ],
        'last_run': last_run and {
            'ran_at': last_run.ran_at,
            'trigger': last_run.trigger,
            'files_deleted': last_run.files_deleted,
            'bytes_freed': last_run.bytes_freed,
        },
    }


def _staff_only():
    return Response(
        {'error': 'Only an administrator can change retention policies or delete files.'},
        status=status.HTTP_403_FORBIDDEN
    )


@api_view(['GET', 'PUT'])
def retention_settings(request):
    if request.method == 'PUT':
        if not request.user.is_staff:
            return _staff_only()
        policies, error = retention.clean_policies(request.data.get('policies'))
        if error:
            return Response({'error': error}, status=status.HTTP_400_BAD_REQUEST)
        retention.save_policies(policies, user=request.user)
    return Response(_retention_state(request))


@api_view(['POST'])
def retention_preview(request):
    """Lists what would be deleted. Never deletes. Accepts unsaved policies to try out."""
    policies = None
    if request.data.get('policies') is not None:
        policies, error = retention.clean_policies(request.data.get('policies'))
        if error:
            return Response({'error': error}, status=status.HTTP_400_BAD_REQUEST)
    summary, _targets = retention.build_plan(policies)
    return Response(summary)


@api_view(['POST'])
@throttle_classes([VisitorLimit, UserLimit, HeavyLimit])
def retention_run(request):
    """Deletes what the saved policies say has expired."""
    if not request.user.is_staff:
        return _staff_only()
    return Response(retention.apply_policies(user=request.user, trigger='manual'))


# ==========================================
# 4. BACKUPS
# ==========================================

SCHEDULE_FIELDS = ('frequency', 'hour', 'weekday', 'monthday')


def _backup_state(request):
    backups.reconcile()
    config = BackupSettings.load()
    return {
        'can_edit': bool(request.user.is_staff),
        # Why a backup failed can include internal details: admins only
        'health': backups.health(details=request.user.is_staff),
        'folder': settings.BACKUP_DIR,
        'timezone': settings.TIME_ZONE,
        'server_time': timezone.localtime(),
        'encryption': {
            'enabled': bool(config.encryption_public_key),
            'disabled': config.encryption_disabled,
            'public_key': config.encryption_public_key,
        },
        'schedule': {
            kind: {field: getattr(config, f'{kind}_{field}') for field in SCHEDULE_FIELDS}
            for kind in ('db', 'full')
        },
        'backups': [
            {
                'id': b.id, 'kind': b.kind, 'status': b.status, 'trigger': b.trigger,
                'started_at': b.started_at, 'finished_at': b.finished_at,
                'filename': b.filename, 'size': b.size, 'encrypted': b.encrypted,
                'error': b.error if request.user.is_staff else ('Failed. An admin can see why.' if b.error else ''),
            }
            for b in Backup.objects.all()[:100]
        ],
    }


class HoldingViewSet(viewsets.ModelViewSet):
    """Assets (homes, vehicles, other) and loans, with their value history."""
    serializer_class = HoldingSerializer

    def get_queryset(self):
        return Account.objects.filter(
            Q(asset_details__isnull=False) | Q(loan_details__isnull=False)
        ).select_related('asset_details', 'loan_details').prefetch_related('balance_entries').order_by('name')

    @action(detail=True, methods=['post'])
    def values(self, request, pk=None):
        """
        Records what it was worth on a date (an asset), or a statement balance
        (a loan, or with which=escrow its escrow account). A statement balance
        becomes the loan's new starting point. A second entry for the same
        date replaces the first.
        """
        holding = self.get_object()
        serializer = BalanceEntrySerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        loan = getattr(holding, 'loan_details', None)
        if loan:
            target, negative = holding, True
            if request.data.get('which') == 'escrow':
                if not loan.escrow_account_id:
                    return Response({'error': 'This loan has no escrow account.'}, status=status.HTTP_400_BAD_REQUEST)
                target, negative = loan.escrow_account, False
            loans.set_statement_balance(target, data['date'], data['amount'], data.get('source', ''), negative=negative)
            return Response(self.get_serializer(self.get_queryset().get(pk=holding.pk)).data)
        BalanceEntry.objects.update_or_create(
            account=holding, date=serializer.validated_data['date'],
            defaults={'amount': serializer.validated_data['amount'], 'source': serializer.validated_data.get('source', '')},
        )
        return Response(self.get_serializer(self.get_queryset().get(pk=holding.pk)).data)

    def _loan(self):
        holding = self.get_object()
        loan = getattr(holding, 'loan_details', None)
        if loan is None:
            raise ValidationError({'error': 'This is not a loan.'})
        return holding, loan

    @staticmethod
    def _day(raw, name='date'):
        try:
            return date.fromisoformat(str(raw))
        except (TypeError, ValueError):
            raise ValidationError({'error': f'{name} must be a date like 2026-01-31.'})

    @action(detail=True, methods=['get'])
    def equity(self, request, pk=None):
        """A secured loan and its asset (either one's id) month by month: value, owed and equity."""
        holding = self.get_object()
        loan = getattr(holding, 'loan_details', None) or LoanDetail.objects.filter(asset_account=holding).first()
        if not loan or not loan.asset_account_id:
            return Response({'error': 'Link this loan to its home or vehicle first: edit the loan in Settings and choose "Secured by".'},
                            status=status.HTTP_400_BAD_REQUEST)
        return Response(loans.equity_history(loan.liability_account))

    @action(detail=True, methods=['get'], url_path='payment-split')
    def payment_split(self, request, pk=None):
        """
        A suggested split for a payment: ?date=…[&amount=…][&extra=…]. Also
        lists payments already in the ledger (e.g. imported from checking)
        that could be this one.
        """
        holding, loan = self._loan()
        day = self._day(request.query_params.get('date'))
        try:
            amount = Decimal(request.query_params['amount']) if request.query_params.get('amount') else None
            extra = Decimal(request.query_params.get('extra') or '0')
        except InvalidOperation:
            raise ValidationError({'error': 'The amount must be a number.'})
        split = loans.suggest_split(loan, holding, day, amount=amount, extra_principal=extra)
        candidates = loans.payment_candidates(holding, day, split['total'])
        return Response({
            **{name: str(value) for name, value in split.items()},
            'escrow_mode': loan.escrow_mode,
            'candidates': [
                {'id': tx.id, 'date': tx.dtposted, 'description': tx.name, 'amount': str(tx.trnamt), 'account_name': tx.account.name}
                for tx in candidates
            ],
        })

    @action(detail=True, methods=['post'])
    def payment(self, request, pk=None):
        """
        Records a payment: {date, interest, principal, property_tax, insurance,
        pmi, and either transaction_id (a payment already in the ledger) or
        from_account (a new one is made there), memo}.
        """
        holding, loan = self._loan()
        data = request.data
        day = self._day(data.get('date'))
        existing = from_account = None
        if data.get('transaction_id'):
            existing = Transaction.objects.filter(pk=data['transaction_id']).first()
            if existing is None:
                return Response({'error': 'That transaction no longer exists.'}, status=status.HTTP_404_NOT_FOUND)
        elif data.get('from_account'):
            from_account = Account.objects.filter(pk=data['from_account']).first()
            if from_account and (from_account.closed_date or not from_account.active):
                return Response({'error': f'"{from_account.name}" is closed. Choose an open account to pay from.'},
                                status=status.HTTP_400_BAD_REQUEST)
        try:
            payment = loans.record_payment(loan, holding, day, data, from_account=from_account, existing=existing,
                                           memo=data.get('memo', ''))
        except loans.LoanError as problem:
            return Response({'error': str(problem)}, status=status.HTTP_400_BAD_REQUEST)
        return Response({'payment_id': payment.id, 'holding': self.get_serializer(self.get_queryset().get(pk=holding.pk)).data},
                        status=status.HTTP_201_CREATED)

    @action(detail=True, methods=['post'], url_path='escrow-payment')
    def escrow_payment(self, request, pk=None):
        """The lender paid a bill from escrow: {date, amount, kind: property_tax|insurance|pmi|other, memo}."""
        holding, loan = self._loan()
        day = self._day(request.data.get('date'))
        try:
            loans.record_escrow_disbursement(loan, day, request.data.get('amount'), request.data.get('kind'), request.data.get('memo', ''))
        except loans.LoanError as problem:
            return Response({'error': str(problem)}, status=status.HTTP_400_BAD_REQUEST)
        return Response(self.get_serializer(self.get_queryset().get(pk=holding.pk)).data, status=status.HTTP_201_CREATED)

    @action(detail=True, methods=['delete'], url_path=r'values/(?P<entry_id>\d+)')
    def delete_value(self, request, pk=None, entry_id=None):
        holding = self.get_object()
        deleted, _ = BalanceEntry.objects.filter(account=holding, pk=entry_id).delete()
        if not deleted:
            return Response({'error': 'That value no longer exists.'}, status=status.HTTP_404_NOT_FOUND)
        return Response(self.get_serializer(self.get_queryset().get(pk=holding.pk)).data)

    def destroy(self, request, *args, **kwargs):
        holding = self.get_object()
        loan = getattr(holding, 'loan_details', None)
        escrow = loan.escrow_account if loan else None
        if Transaction.objects.filter(account__in=[a for a in (holding, escrow) if a]).exists():
            return Response({'error': 'This has payments recorded against it. Close it instead.'}, status=status.HTTP_400_BAD_REQUEST)
        response = super().destroy(request, *args, **kwargs)
        if escrow:
            escrow.delete()
        return response


@api_view(['GET'])
def net_worth(request):
    """
    Net worth over a range, with every account's balance (finance/networth.py).
    ?start=YYYY-MM-DD&end=YYYY-MM-DD, both optional: the whole history to today.
    """
    dates = {}
    for name in ('start', 'end'):
        raw = request.query_params.get(name)
        if raw:
            try:
                dates[name] = date.fromisoformat(raw)
            except ValueError:
                return Response({'error': f'{name} must be a date like 2026-01-31.'}, status=status.HTTP_400_BAD_REQUEST)
    if dates.get('start') and dates.get('end') and dates['start'] > dates['end']:
        return Response({'error': 'The start date is after the end date.'}, status=status.HTTP_400_BAD_REQUEST)
    return Response(networth.history(**dates))


@api_view(['GET'])
def backup_overview(request):
    return Response(_backup_state(request))


@api_view(['GET'])
def backup_health(request):
    """Small status the whole app can poll to warn about failed or overdue backups."""
    return Response(backups.health(details=request.user.is_staff))


@api_view(['GET', 'PUT'])
def system_settings(request):
    """
    The everyday settings (finance/system.py): what each is now and its
    default. Admins change them with PUT; a value of null restores the default.
    """
    if request.method == 'PUT':
        if not request.user.is_staff:
            return _staff_only()
        values = {}
        for key, value in (request.data or {}).items():
            if key not in system.defaults():
                return Response({'error': f'Unknown setting: {key}.'}, status=status.HTTP_400_BAD_REQUEST)
            if value is None:
                values[key] = None
            elif key == 'update_checks':
                if not isinstance(value, bool):
                    return Response({'error': 'update_checks must be true or false.'}, status=status.HTTP_400_BAD_REQUEST)
                values[key] = value
            else:
                low, high = system.LIMITS[key]
                try:
                    number = float(value) if key == 'disk_warn_gb' else int(value)
                except (TypeError, ValueError):
                    return Response({'error': f'{key} must be a number.'}, status=status.HTTP_400_BAD_REQUEST)
                if not low <= number <= high:
                    return Response({'error': f'{key} must be between {low} and {high}.'}, status=status.HTTP_400_BAD_REQUEST)
                values[key] = number
        system.save(values, request.user)
    return Response({'current': system.current(), 'defaults': system.defaults(), 'limits': system.LIMITS,
                     'can_edit': request.user.is_staff, 'repo': settings.UPDATE_CHECK_REPO})


@api_view(['GET'])
def system_status(request):
    """Free disk space for everyone signed in; whether a newer release is out, for admins."""
    return Response({'disk': system.disk_status(),
                     'update': system.update_status() if request.user.is_staff else None})


@api_view(['PUT'])
def backup_schedule(request):
    if not request.user.is_staff:
        return _staff_only()
    config = BackupSettings.load()
    limits = {'hour': (0, 23), 'weekday': (0, 6), 'monthday': (1, 28)}
    frequencies = {choice for choice, _ in BackupSettings.FREQUENCY_CHOICES}

    for kind in ('db', 'full'):
        wanted = (request.data.get('schedule') or {}).get(kind) or {}
        for field in SCHEDULE_FIELDS:
            if field not in wanted:
                continue
            value = wanted[field]
            if field == 'frequency':
                if value not in frequencies:
                    return Response({'error': f'{kind}: unknown frequency.'}, status=status.HTTP_400_BAD_REQUEST)
            else:
                low, high = limits[field]
                if isinstance(value, bool) or not isinstance(value, int) or not low <= value <= high:
                    return Response({'error': f'{kind}: {field} must be a whole number from {low} to {high}.'},
                                    status=status.HTTP_400_BAD_REQUEST)
            setattr(config, f'{kind}_{field}', value)

    config.updated_by = request.user
    config.save()
    backups.write_status()  # so whatever copies the backups learns the new schedule
    return Response(_backup_state(request))


@api_view(['POST'])
def backup_encryption(request):
    """
    action=generate: create a key pair and return the private key, once.
    action=disable:  write backups unencrypted from now on (must be confirmed).
    """
    if not request.user.is_staff:
        return _staff_only()
    action = request.data.get('action')

    if action == 'generate':
        private_key = backups.generate_key(user=request.user)
        # The only time the private key ever leaves the server. It is not stored.
        return Response({**_backup_state(request), 'private_key': private_key})

    if action == 'disable':
        if request.data.get('confirm') is not True:
            return Response({'error': 'Turning encryption off must be confirmed.'}, status=status.HTTP_400_BAD_REQUEST)
        backups.disable_encryption(user=request.user)
        return Response(_backup_state(request))

    return Response({'error': 'Unknown action.'}, status=status.HTTP_400_BAD_REQUEST)


@api_view(['POST'])
@throttle_classes([VisitorLimit, UserLimit, HeavyLimit])
def backup_run(request):
    """Starts a database or full backup now, in the background."""
    if not request.user.is_staff:
        return _staff_only()
    kind = request.data.get('kind')
    if kind not in backups.KINDS:
        return Response({'error': "kind must be 'db' or 'full'."}, status=status.HTTP_400_BAD_REQUEST)
    if not BackupSettings.load().encryption_ready:
        return Response({'error': 'Set up encryption (or turn it off) before making a backup.'},
                        status=status.HTTP_400_BAD_REQUEST)
    try:
        record = backups.start_backup(kind, user=request.user)
    except backups.BackupError as exc:
        return Response({'error': str(exc)}, status=status.HTTP_409_CONFLICT)
    return Response({'id': record.id, 'status': record.status}, status=status.HTTP_202_ACCEPTED)


@api_view(['GET', 'DELETE'])
def backup_file(request, pk):
    """GET downloads a backup file; DELETE removes it. Staff only."""
    if not request.user.is_staff:
        return _staff_only()
    backup = Backup.objects.filter(pk=pk).first()
    if not backup:
        return Response({'error': 'Backup not found.'}, status=status.HTTP_404_NOT_FOUND)

    if request.method == 'DELETE':
        if backup.status == Backup.RUNNING:
            return Response({'error': 'This backup is still running.'}, status=status.HTTP_409_CONFLICT)
        backups.delete_backup(backup)
        return Response(_backup_state(request))

    path = backups.path_for(backup)
    if backup.status != Backup.OK or not os.path.isfile(path):
        return Response({'error': 'This backup has no file to download.'}, status=status.HTTP_404_NOT_FOUND)
    logger.warning("Backup %s downloaded by %s", backup.filename, request.user)
    return FileResponse(open(path, 'rb'), as_attachment=True, filename=backup.filename)


# ==========================================
# 5. MISCELLANEOUS VIEWS
# ==========================================

class ReceiptViewSet(viewsets.ModelViewSet):
    serializer_class = ReceiptSerializer

    @action(detail=False, methods=['get'])
    def reader(self, request):
        """Whether the receipt reader is installed and ready, and how to turn it on if not."""
        ready = receipt_reader.available()
        return Response({'installed': receipt_reader.installed(), 'available': ready,
                         'how_to_turn_on': None if ready else receipt_reader.how_to_turn_on(),
                         # To paste on the server (Settings → Storage)
                         'command_on': system.host_command('receipt-reader.sh on'),
                         'command_off': system.host_command('receipt-reader.sh off'),
                         'in_folder': bool(settings.INSTALL_DIR),
                         'waiting': receipt_reader.waiting()})

    def get_queryset(self):
        return Receipt.objects.filter(approved=False).select_related(
            'transaction__account', 'transaction__subcategory'
        ).prefetch_related('transaction__splits').order_by('-date')

    def destroy(self, request, *args, **kwargs):
        """
        Deletes a receipt that is still in the inbox: its record and the image
        the app stored for it. For receipts that are stuck, misread beyond
        repair, or were never receipts at all.

        Only unapproved receipts can be reached here (see get_queryset), and
        an unapproved receipt has changed nothing in the ledger, so the
        transaction it may be linked to is left exactly as it is. The copy in
        the archive folder is not touched; that is governed by the storage
        retention policy.
        """
        receipt = self.get_object()
        name = receipt.original_filename
        image = receipt.image
        with transaction.atomic():
            receipt.delete()
        # Remove the file only once the record is really gone
        if image:
            try:
                image.storage.delete(image.name)
            except Exception:
                logger.exception("Receipt %s was deleted but its image file %s could not be removed", name, image.name)
        logger.warning("Receipt %s deleted from the inbox by %s", name, request.user)
        return Response(status=status.HTTP_204_NO_CONTENT)

    @action(detail=True, methods=['post'])
    def approve(self, request, pk=None):
        """
        The user has checked the amounts read from the image, corrected them
        if needed, and approves them. tax_amount and total_amount, when sent,
        are saved as the approved figures. With split_tax=true the linked
        transaction is also split into tax and the rest, using that tax amount.
        The split is all-or-nothing with the approval: if it cannot be made,
        the receipt stays in the inbox and the reason is returned.
        """
        receipt = self.get_object()
        split_tax = request.data.get('split_tax') is True

        # The figures the user is looking at when they approve are the ones
        # that are saved, so a correction typed in the box is never lost.
        for field, label in (('tax_amount', 'tax amount'), ('total_amount', 'total amount')):
            if request.data.get(field) is None:
                continue
            try:
                value = Decimal(str(request.data.get(field)))
                if not value.is_finite() or value < 0:
                    raise InvalidOperation
            except InvalidOperation:
                # Under 'error', where the page looks for the reason to show
                return Response(
                    {'error': f'The {label} must be a number, zero or more. Correct it in the box and approve again.'},
                    status=status.HTTP_400_BAD_REQUEST
                )
            setattr(receipt, field, value)

        with transaction.atomic():
            if split_tax:
                done, message = apply_tax_split(receipt.transaction, receipt)
                if not done:
                    transaction.set_rollback(True)
                    return Response({'error': message}, status=status.HTTP_409_CONFLICT)
            receipt.approved = True
            receipt.save()
        return Response({'status': 'approved', 'tax_split': split_tax})

    @action(detail=True, methods=['get'])
    def suggestions(self, request, pk=None):
        """
        Transactions that might belong to an unlinked receipt, for the user to
        pick from. The receipt's total only helps to find the transaction; the
        bank's amount is the one the ledger keeps.
        """
        receipt = self.get_object()
        return Response([
            {
                'id': tx.id,
                'date': tx.dtposted,
                'description': tx.name,
                'amount': str(tx.trnamt),
                'account_name': tx.account.name if tx.account else None,
            }
            for tx in suggest_receipt_matches(receipt)
        ])

    @action(detail=True, methods=['post'])
    def link_transaction(self, request, pk=None):
        receipt = self.get_object()
        transaction_id = request.data.get('transaction_id')
        
        try:
            transaction = Transaction.objects.get(id=transaction_id)
            receipt.transaction = transaction
            receipt.status = 'MATCHED'
            receipt.save()
            # No split here: the tax is reviewed and approved separately
            return Response({'status': 'linked', 'transaction': transaction.name})
            
        except Transaction.DoesNotExist:
            return Response({'error': 'Transaction not found'}, status=404)

def browser_copy_to_suffix(name):
    """A browser's copy number becomes "_copy-N": "X123 (1).csv" -> "X123_copy-1.csv".

    Done with plain text handling, which takes the same short time whatever
    the name holds."""
    stem, dot, extension = name.rpartition('.')
    if not dot:
        return name
    if stem.endswith(')'):
        start = stem.rfind('(')
        number = stem[start + 1:-1]
        if start >= 0 and number.isascii() and number.isdigit():
            stem = stem[:start].rstrip() + '_copy-' + number
    return stem + dot + extension


class FileUploadView(APIView):
    """
    Receives a bank file or receipt image and leaves it in the dropzone for
    the watcher. Only known file types, up to a size limit, and never over
    the top of a file that is still waiting to be processed.
    """
    parser_classes = (MultiPartParser, FormParser)
    throttle_classes = [VisitorLimit, UserLimit, UploadLimit]

    def post(self, request, *args, **kwargs):
        limit_mb = system.current()['upload_max_mb']
        max_bytes = limit_mb * 1024 * 1024
        too_large = Response(
            {'error': f'That file is too large. The limit is {limit_mb} MB.'},
            status=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE
        )

        # Refuse an oversized request before reading its body. The declared
        # length includes a little form overhead, hence the allowance.
        try:
            declared = int(request.META.get('CONTENT_LENGTH') or 0)
        except ValueError:
            declared = 0
        if declared > max_bytes + 64 * 1024:
            return too_large

        file_obj = request.FILES.get('file')
        if not file_obj:
            return Response({'error': 'No file provided'}, status=400)
        if file_obj.size > max_bytes:
            return too_large

        try:
            # Django has already stripped any folders from the name; this
            # also removes characters that are awkward in file names. A
            # browser's copy number ("X123(1).csv" for a second download)
            # becomes "_copy-1" first, or it would run into the account
            # number some banks put in the name ("X1231.csv").
            name = file_obj.name
            filename = get_valid_filename(browser_copy_to_suffix(name)) if len(name) <= 255 else ''

        except SuspiciousFileOperation:
            filename = ''
        if not filename or filename.startswith('.'):
            return Response({'error': 'That file name cannot be used.'}, status=400)

        allowed = settings.UPLOAD_ALLOWED_EXTENSIONS
        if not filename.lower().endswith(allowed):
            return Response(
                {'error': f"That type of file is not supported. Upload one of: {', '.join(allowed)}"},
                status=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE
            )

        # A receipt photo with nothing to read it would only end up in the failed folder
        if filename.lower().endswith(settings.RECEIPT_IMAGE_EXTENSIONS) and not receipt_reader.available():
            return Response({'error': receipt_reader.how_to_turn_on()}, status=status.HTTP_503_SERVICE_UNAVAILABLE)

        dropzone_path = settings.DROPZONE_DIR
        destination = os.path.join(dropzone_path, filename)
        if os.path.exists(destination):
            return Response(
                {'error': f'A file named {filename} is already waiting to be processed. Wait for it to finish, or rename this one.'},
                status=status.HTTP_409_CONFLICT
            )

        # Write under a hidden temporary name and rename when complete, so the
        # watcher never picks up a half-written file.
        temporary = os.path.join(dropzone_path, f'.upload-{uuid.uuid4().hex}.part')
        try:
            os.makedirs(dropzone_path, exist_ok=True)
            with open(temporary, 'wb') as destination_file:
                for chunk in file_obj.chunks():
                    destination_file.write(chunk)
            os.replace(temporary, destination)
            return Response({'status': 'success', 'filename': filename}, status=201)
        except Exception:
            # The detail goes to the server log, never to the browser
            logger.exception("Saving upload %s failed", filename)
            try:
                os.remove(temporary)
            except OSError:
                pass
            return Response({'error': 'The file could not be saved because of a server error.'}, status=500)
