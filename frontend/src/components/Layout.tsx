// Copyright (C) 2026 free-the-software
// SPDX-License-Identifier: AGPL-3.0-or-later
// This file is part of Save More Money, under the GNU AGPL v3 or later: see LICENSE.
import { useEffect, useState } from 'react';
import { Link, useLocation, Outlet } from 'react-router';
import {
    Bars3Icon,
    XMarkIcon,
    PowerIcon,
    QuestionMarkCircleIcon,
    ArrowsUpDownIcon,
    ChevronUpIcon,
    ChevronDownIcon,
} from '@heroicons/react/24/outline';
import { MENU, SETTINGS_ITEM, orderMenu } from './menu';
import api, { logout } from '../api';
import { useTour, tourWasSeen } from './Tour';
import { notify } from './Dialogs';
import Logo from './Logo';

const Layout = () => {
    const location = useLocation();
    const [isNavOpen, setIsNavOpen] = useState(false);
    // Shown on every page, because a backup that fails quietly is worse than none
    const [backupWarning, setBackupWarning] = useState('');
    // Also on every page: the server's disk running low; and, for admins, a newer release (finance/system.py)
    const [diskWarning, setDiskWarning] = useState('');
    const [newRelease, setNewRelease] = useState('');
    // Also on every page: accounts forecast to go below zero (finance/forecast.py)
    const [shortfalls, setShortfalls] = useState<{ id: number; name: string; deficit_date: string; projected_balance: string }[]>([]);
    const tour = useTour();

    // A new install (no accounts yet) offers the getting-started tour, once per browser
    useEffect(() => {
        if (tourWasSeen()) return;
        api.get('/accounts/').then(res => { if (res.data.length === 0) tour.start(); }).catch(() => undefined);
    }, []);

    useEffect(() => {
        api.get('/backups/health/')
            .then(res => setBackupWarning(['failed', 'overdue'].includes(res.data.state) ? res.data.message : ''))
            .catch(() => undefined);
        api.get('/system/status/')
            .then(res => {
                setDiskWarning(res.data.disk?.low ? res.data.disk.message : '');
                setNewRelease(res.data.update?.newer ? res.data.update.latest : '');
            })
            .catch(() => undefined);
        api.get('/accounts/dashboard/')
            .then(res => setShortfalls(res.data.checking.filter((a: any) => a.will_decrement)))
            .catch(() => undefined);
    }, [location.pathname]);

    // The menu in this user's order (saved on the server, so it follows them
    // to every device); the first page is where the app opens
    const [menuOrder, setMenuOrder] = useState<string[]>([]);
    const [reordering, setReordering] = useState<string[] | null>(null);    // the order being edited
    useEffect(() => {
        api.get('/account/preferences/').then(res => setMenuOrder(res.data.menu_order || [])).catch(() => undefined);
    }, []);
    const navigation = [...orderMenu(menuOrder), SETTINGS_ITEM];

    const move = (index: number, by: number) => setReordering(order => {
        if (!order) return order;
        const next = [...order];
        [next[index], next[index + by]] = [next[index + by], next[index]];
        return next;
    });
    const saveOrder = async (order: string[]) => {
        try {
            const res = await api.put('/account/preferences/', { menu_order: order });
            setMenuOrder(res.data.menu_order);
            setReordering(null);
        } catch {
            notify('Could not save the menu order.');
        }
    };

    return (
        <div className="flex flex-col h-screen bg-black text-white">
            
            {/* --- TOP HEADER --- */}
            <header className="bg-gray-900 border-b border-gray-800 flex-shrink-0 h-16 relative z-10">
                <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 h-full flex items-center justify-start gap-4">
                    
                    {/* 1. Hamburger Button */}
                    <button 
                        data-tour="menu"
                        onClick={() => setIsNavOpen(true)}
                        className="p-2 text-gray-400 hover:text-white hover:bg-gray-800 rounded-md focus:outline-none"
                    >
                        <Bars3Icon className="w-8 h-8" />
                    </button>

                    {/* 2. Logo */}
                    <div className="flex items-center">
                        <h1 aria-label="Save More Money"><Logo /></h1>
                    </div>
                    
                </div>
            </header>

            {/* --- BACKUP WARNING --- */}
            {shortfalls.map(a => (
                <div key={a.id} className="bg-red-900/60 border-b border-red-800 text-red-100 text-sm px-4 py-2 flex-shrink-0">
                    <span className="font-bold">Shortfall ahead:</span> {a.name} is forecast to go below zero{' '}
                    {a.deficit_date ? `on ${new Date(a.deficit_date + 'T00:00:00').toLocaleDateString('en-US', { month: 'short', day: 'numeric', year: 'numeric' })}` : 'now'}
                    {' '}(lowest {Number(a.projected_balance).toLocaleString('en-US', { style: 'currency', currency: 'USD' })}).{' '}
                    <Link to={`/net-worth?account=${a.id}`} className="underline hover:text-white">See its transactions</Link>
                </div>
            ))}
            {diskWarning && (
                <div className="bg-red-900/60 border-b border-red-800 text-red-100 text-sm px-4 py-2 flex-shrink-0">
                    <span className="font-bold">Low disk space:</span> {diskWarning}
                </div>
            )}
            {backupWarning && (
                <div className="bg-red-900/60 border-b border-red-800 text-red-100 text-sm px-4 py-2 flex-shrink-0">
                    <span className="font-bold">Backup problem:</span> {backupWarning}{' '}
                    <Link to="/settings" className="underline hover:text-white">Open Settings, then Backups</Link>
                </div>
            )}

            {/* --- POP-UP NAVIGATION SIDEBAR --- */}
            {isNavOpen && (
                <div className="relative z-50">
                    
                    {/* Dark Backdrop */}
                    <button
                        type="button"
                        className="fixed inset-0 bg-black/80 backdrop-blur-sm w-full h-full cursor-default"
                        onClick={() => setIsNavOpen(false)}
                        aria-label="Close navigation menu"
                    />

                    {/* Sidebar Panel */}
                    <div className="fixed inset-y-0 left-0 w-64 bg-gray-900 shadow-2xl flex flex-col border-r border-gray-800 animate-slide-in">
                        
                        {/* Header inside Sidebar */}
                        <div className="flex items-center justify-between px-4 h-16 border-b border-gray-800">
                            <span className="text-lg font-bold text-gray-500">Menu</span>
                            <button 
                                onClick={() => setIsNavOpen(false)}
                                className="p-2 text-gray-400 hover:text-white rounded-md"
                            >
                                <XMarkIcon className="w-6 h-6" />
                            </button>
                        </div>

                        {/* Navigation Links */}
                        <nav className="flex-1 px-2 py-4 space-y-2 overflow-y-auto">
                            {reordering ? (
                                <div className="space-y-2">
                                    <p className="px-2 text-xs text-gray-400">Move pages up or down. The first is where the app opens.</p>
                                    {reordering.map((href, index) => {
                                        const item = MENU.find(m => m.href === href)!;
                                        return (
                                            <div key={href} className="flex items-center px-3 py-2 rounded-lg bg-gray-800/60 text-gray-200">
                                                <item.icon className="w-5 h-5 mr-3 text-gray-500" />
                                                <span className="flex-1 text-sm">{item.name}{index === 0 && <span className="ml-2 text-[10px] text-blue-300">opens first</span>}</span>
                                                <button onClick={() => move(index, -1)} disabled={index === 0} aria-label={`Move ${item.name} up`}
                                                    className="p-1 text-gray-400 hover:text-white disabled:opacity-20"><ChevronUpIcon className="w-5 h-5" /></button>
                                                <button onClick={() => move(index, 1)} disabled={index === reordering.length - 1} aria-label={`Move ${item.name} down`}
                                                    className="p-1 text-gray-400 hover:text-white disabled:opacity-20"><ChevronDownIcon className="w-5 h-5" /></button>
                                            </div>
                                        );
                                    })}
                                    <div className="flex gap-2 pt-2 px-1">
                                        <button onClick={() => saveOrder(reordering)} className="flex-1 py-2 bg-blue-600 hover:bg-blue-500 text-white text-sm font-bold rounded-lg">Done</button>
                                        <button onClick={() => setReordering(null)} className="px-3 py-2 text-sm text-gray-400 hover:text-white">Cancel</button>
                                    </div>
                                    <button onClick={() => saveOrder([])} className="w-full text-xs text-gray-500 hover:text-white">Reset to the usual order</button>
                                </div>
                            ) : navigation.map((item) => {
                                const isActive = location.pathname === item.href;
                                return (
                                    <Link
                                        key={item.name}
                                        to={item.href}
                                        onClick={() => setIsNavOpen(false)}
                                        className={`flex items-center px-4 py-3 text-base font-medium rounded-lg transition-colors ${
                                            isActive 
                                                ? 'bg-blue-600 text-white shadow-md shadow-blue-900/20' 
                                                : 'text-gray-400 hover:bg-gray-800 hover:text-white'
                                        }`}
                                    >
                                        <item.icon className={`w-6 h-6 mr-3 ${isActive ? 'text-white' : 'text-gray-500 group-hover:text-white'}`} />
                                        {item.name}
                                    </Link>
                                );
                            })}
                        </nav>
                        
                        {/* Footer */}
                        <div className="p-4 border-t border-gray-800">
                            {!reordering && (
                                <button
                                    onClick={() => setReordering(orderMenu(menuOrder).map(item => item.href))}
                                    className="flex items-center w-full px-4 py-3 mb-2 text-base font-medium rounded-lg text-gray-400 hover:bg-gray-800 hover:text-white transition-colors"
                                >
                                    <ArrowsUpDownIcon className="w-6 h-6 mr-3 text-gray-500" />
                                    Reorder menu
                                </button>
                            )}
                            <button
                                onClick={() => { setIsNavOpen(false); tour.start(); }}
                                className="flex items-center w-full px-4 py-3 mb-2 text-base font-medium rounded-lg text-gray-400 hover:bg-gray-800 hover:text-white transition-colors"
                            >
                                <QuestionMarkCircleIcon className="w-6 h-6 mr-3 text-gray-500" />
                                Take the tour
                            </button>
                            <button
                                onClick={() => logout().catch(() => notify("Sign out failed"))}
                                className="flex items-center w-full px-4 py-3 mb-2 text-base font-medium rounded-lg text-gray-400 hover:bg-gray-800 hover:text-white transition-colors"
                            >
                                <PowerIcon className="w-6 h-6 mr-3 text-gray-500" />
                                Sign out
                            </button>
                            <div className="text-xs text-center text-gray-500 space-y-1">
                                <p>
                                    <Link to="/settings?tab=storage" onClick={() => setIsNavOpen(false)} className="text-gray-400 hover:text-white underline">
                                        {__APP_VERSION__ ? `Version ${__APP_VERSION__}` : import.meta.env.DEV ? 'Development' : 'Built from source'}
                                    </Link>
                                </p>
                                <p>
                                    <a href={__SOURCE_URL__} target="_blank" rel="noreferrer" className="text-gray-400 hover:text-white underline">Source code</a>
                                    {' · '}
                                    <a href="https://www.gnu.org/licenses/agpl-3.0.html" target="_blank" rel="noreferrer" className="text-gray-400 hover:text-white underline">AGPL licence</a>
                                </p>
                            </div>
                            {newRelease && (
                                <Link to="/settings?tab=storage" onClick={() => setIsNavOpen(false)} className="block text-xs text-center text-blue-400 hover:text-blue-300 mt-1">
                                    Version {newRelease} is available
                                </Link>
                            )}
                        </div>

                    </div>
                </div>
            )}

            {/* --- MAIN CONTENT AREA --- */}
            <div className="flex-1 overflow-hidden bg-black relative">
                <main className="h-full overflow-y-auto p-4 sm:p-6 lg:p-8">
                    <div className="max-w-7xl mx-auto">
                        <Outlet />
                    </div>
                </main>
            </div>

        </div>
    );
};

export default Layout;