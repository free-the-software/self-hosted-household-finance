// Copyright (C) 2026 free-the-software
// SPDX-License-Identifier: AGPL-3.0-or-later
// This file is part of Save More Money, under the GNU AGPL v3 or later: see LICENSE.
import {
    CreditCardIcon, ArrowDownTrayIcon, ChartBarIcon, ArrowPathIcon, Cog6ToothIcon, ScaleIcon,
} from '@heroicons/react/24/outline';
import type { ComponentType, SVGProps } from 'react';

// The pages in the menu, in the usual order. Each user can reorder them
// (Layout, saved in their profile); the first is where the app opens
// (StartPage). Settings stays at the bottom, so it can always be found.

export interface MenuItem { name: string; href: string; icon: ComponentType<SVGProps<SVGSVGElement>>; }

export const MENU: MenuItem[] = [
    { name: 'Budget to Actual', href: '/budget', icon: ChartBarIcon },
    { name: 'Net Worth', href: '/net-worth', icon: ScaleIcon },
    { name: 'Transactions', href: '/transactions', icon: CreditCardIcon },
    { name: 'Recurring', href: '/recurring', icon: ArrowPathIcon },
    { name: 'Import Inbox', href: '/import', icon: ArrowDownTrayIcon },
    { name: 'Receipt Inbox', href: '/receipts', icon: ArrowDownTrayIcon },
];
export const SETTINGS_ITEM: MenuItem = { name: 'Settings', href: '/settings', icon: Cog6ToothIcon };
// Where the app opens for someone who hasn't reordered the menu
export const DEFAULT_START = '/transactions';

// The pages in a saved order: those it names first, then any it doesn't
// (added since it was saved) in their usual place at the end
export const orderMenu = (saved: string[] = []): MenuItem[] => {
    const known = saved.map(href => MENU.find(item => item.href === href)).filter((item): item is MenuItem => !!item);
    return [...known, ...MENU.filter(item => !known.includes(item))];
};
