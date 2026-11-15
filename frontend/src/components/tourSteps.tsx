// Copyright (C) 2026 free-the-software
// SPDX-License-Identifier: AGPL-3.0-or-later
// This file is part of Save More Money, under the GNU AGPL v3 or later: see LICENSE.
import type { TourStep } from './Tour';

// The getting-started tour, in the order a new user sets things up. Each
// target is a data-tour="..." on the page named by route.

const b = (text: string) => <span className="font-bold text-white">{text}</span>;

const tourSteps: TourStep[] = [
    {
        title: 'Welcome to Save More Money',
        body: <>
            <p>This short tour shows where everything is and the order to set things up:</p>
            <ol className="list-decimal list-inside space-y-1">
                <li>Add your accounts</li>
                <li>Set up categories and rules</li>
                <li>Import your bank files</li>
                <li>Review transactions, budgets and net worth</li>
            </ol>
            <p className="text-gray-400">Use Next and Back, or the arrow keys. Nothing on the page changes during the tour.</p>
        </>,
    },
    {
        title: 'The menu',
        route: '/transactions',
        target: 'menu',
        body: <p>Every page is in this menu. {b('Reorder menu')} at the bottom puts them in the order you like; the first is where the app opens. You can take this tour again any time with {b('Take the tour')}, also at the bottom.</p>,
    },
    {
        title: '1. Add your accounts',
        route: '/settings?tab=accounts',
        target: 'add-account',
        body: <>
            <p>Start in {b('Settings → Accounts')}. {b('Add Account')} offers two sorts:</p>
            <ul className="list-disc list-inside space-y-1">
                <li>{b('Bank accounts')} (checking, savings, credit cards) get their transactions from your bank's download files.</li>
                <li>{b('Assets and loans')} (your home, vehicles, mortgage, car loan) are kept by hand, for net worth.</li>
            </ul>
        </>,
    },
    {
        title: 'Bank account details',
        route: '/settings?tab=accounts',
        target: 'accounts-table',
        body: <>
            <p>A bank account needs the {b('FID')} (bank ID) and {b('account number')} that your bank puts in its download files, so imports land in the right place.</p>
            <p>Not sure what they are? Skip it: import a file first, and the Import Inbox will offer to create the account from the file's own details.</p>
            <p>Click an account here to edit it. Accounts you no longer use can be closed; their history is kept.</p>
        </>,
    },
    {
        title: '2. Categories',
        route: '/settings?tab=categories',
        target: 'add-category',
        body: <>
            <p>A starter set of categories is already here. Each category (such as Food) holds subcategories (Groceries, Restaurants); transactions are filed under a subcategory.</p>
            <p>Rename, add or remove them freely. Each category is an {b('Expense')}, {b('Income')} or {b('Transfer')}; transfers (paying a card from checking, say) aren't counted as spending.</p>
        </>,
    },
    {
        title: 'Rules',
        route: '/settings?tab=rules',
        target: 'add-rule',
        body: <>
            <p>Rules tidy up imports for you: when a bank description matches (for example contains "AMZN"), the rule renames the merchant and picks its subcategory.</p>
            <p>The easiest way to make one is from a transaction: click it in the register and choose {b('Create Rule')}.</p>
        </>,
    },
    {
        title: '3. Import your bank files',
        route: '/import',
        target: 'import-dropzone',
        body: <>
            <p>Download a statement from your bank's website and drop it here: Quicken {b('.qfx')} or {b('.ofx')} if offered (they carry the account number and balance), otherwise {b('.csv')}, which works for most banks.</p>
            <p>A file for an account you haven't added yet is held at the top of this page until you pick its account or create a new one.</p>
        </>,
    },
    {
        title: 'Review before it goes in',
        route: '/import',
        target: 'import-table',
        body: <>
            <p>Imported transactions wait here for you. Each is marked:</p>
            <ul className="list-disc list-inside space-y-1">
                <li>{b('New')}: not in your ledger yet. The green tick adds it.</li>
                <li>{b('Match')}: the same as one you entered by hand. The blue tick links them.</li>
                <li>{b('Near match')}: probably the same, with a different date or amount. Check, then link.</li>
                <li>{b('Duplicate')}: already imported. Clear them in one go.</li>
            </ul>
            <p>Click a row to change its category or split it before it goes in.</p>
        </>,
    },
    {
        title: '4. The register',
        route: '/transactions',
        target: 'add-transaction',
        body: <>
            <p>Every approved transaction is listed on the {b('Transactions')} page, newest first.</p>
            <p>{b('+ Add Transaction')} records one by hand, such as a cheque you've written. It stays pending until your bank file brings in the real one, and the Import Inbox matches them up.</p>
        </>,
    },
    {
        title: 'Find and fix',
        route: '/transactions',
        target: 'tx-filters',
        body: <>
            <p>Filter by account, merchant, subcategory or status. Choose {b('Uncategorized')} in the subcategory filter to find what still needs a category; those rows are marked in yellow.</p>
            <p>Click any transaction to edit it, split it across categories, or make a rule from it.</p>
        </>,
    },
    {
        title: 'Recurring bills',
        route: '/recurring',
        target: 'add-recurring',
        body: <p>Add bills and paychecks that repeat. Each is added as a pending transaction a few days before it's due, so your balance forecast can warn you before an account goes below zero.</p>,
    },
    {
        title: 'Budget to Actual',
        route: '/budget',
        target: 'budget-table',
        body: <p>Pick a month, or {b('Year to date')}, to see what came in and went out in each subcategory against its budget, and your savings (income less expenses). Click a month's budget amount to change it, or an amount spent to see the transactions behind it.</p>,
    },
    {
        title: 'Net worth',
        route: '/net-worth',
        target: 'networth-title',
        body: <>
            <p>Everything you own minus everything you owe, over any period, with each account's balance and outlook.</p>
            <p>Bank balances come from your imports. Your home, vehicles and loans come from Settings → Accounts; record loan payments here to keep them current. If an account is forecast to go below zero, a red warning shows on every page.</p>
        </>,
    },
    {
        title: 'Receipts',
        route: '/receipts',
        target: 'receipt-dropzone',
        body: <p>Drop a photo of a receipt here. The app reads it and suggests the matching transaction. Nothing is changed until you approve it, and your bank's amount always wins over what the receipt says.</p>,
    },
    {
        title: "You're ready",
        route: '/transactions',
        body: <>
            <p>A good first day: add your checking account, import one month of its transactions, and approve them.</p>
            <p>Take this tour again any time from the menu.</p>
        </>,
    },
];

export default tourSteps;
