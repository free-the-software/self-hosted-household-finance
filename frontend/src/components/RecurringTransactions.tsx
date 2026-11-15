// Copyright (C) 2026 free-the-software
// SPDX-License-Identifier: AGPL-3.0-or-later
// This file is part of Save More Money, under the GNU AGPL v3 or later: see LICENSE.
import { useEffect, useState } from 'react';
import { ChevronUpIcon, ChevronDownIcon, ArrowsUpDownIcon } from '@heroicons/react/24/outline'; // Added Icons
import { apiFetch } from '../api';
import { notify } from './Dialogs';

// --- TYPES ---
interface SubCategory {
    id: number;
    name: string;
}

interface Category {
    id: number;
    name: string;
    type: string;
    subcategories: SubCategory[];
}

interface Account {
    account_id: number;
    name: string;
}

interface RecurringTxn {
    id: number;
    name: string;
    amount: number;
    account: number | null;
    frequency: string;
    start_date: string;
    create_days_before: number;
    subcategory: number | null;
    category_name?: string;
    subcategory_name?: string;
    account_name?: string;
}

const FREQUENCIES = [
    { value: 'WEEKLY', label: 'Weekly' },
    { value: 'BIWEEKLY', label: 'Bi-Weekly' },
    { value: 'SEMI_MONTHLY', label: 'Semi-Monthly' },
    { value: 'MONTHLY', label: 'Monthly' },
    { value: 'SEMI_ANNUALLY', label: 'Semi-Annually' },
    { value: 'ANNUALLY', label: 'Annually' },
];

export default function RecurringTransactions() {
    const [recurring, setRecurring] = useState<RecurringTxn[]>([]);
    const [categories, setCategories] = useState<Category[]>([]);
    const [accounts, setAccounts] = useState<Account[]>([]);
    const [loading, setLoading] = useState(true);

    // Dialog States
    const [isModalOpen, setIsModalOpen] = useState(false);
    const [isDeleteConfirm, setIsDeleteConfirm] = useState(false);

    // The transaction currently being edited/created
    const [currentTxn, setCurrentTxn] = useState<Partial<RecurringTxn>>({});

    // --- SORTING STATES ---
    type SortField = 'name' | 'amount' | 'frequency' | 'account' | 'subcategory_name' | 'start_date' | 'create_days_before';
    const [sortConfig, setSortConfig] = useState<{ key: SortField, direction: 'asc' | 'desc' }>({ key: 'start_date', direction: 'asc' });

    const handleSort = (key: SortField) => {
        setSortConfig(prev => ({
            key,
            direction: prev.key === key && prev.direction === 'asc' ? 'desc' : 'asc'
        }));
    };

    // --- 1. FETCH DATA ---
    const fetchData = async () => {
        setLoading(true);
        try {
            const [recRes, catRes, accRes] = await Promise.all([
                apiFetch('/api/recurring/'),
                apiFetch('/api/categories/'),
                apiFetch('/api/accounts/')
            ]);
            const recData = await recRes.json();
            const catData = await catRes.json();
            const accData = await accRes.json();

            setRecurring(recData);
            setCategories(catData);
            setAccounts(accData);
        } catch (err) {
            console.error("Error fetching data:", err);
        } finally {
            setLoading(false);
        }
    };

    useEffect(() => { fetchData(); }, []);

    // --- 2. HANDLERS ---
    const handleRowClick = (txn: RecurringTxn) => {
        setCurrentTxn(txn);
        setIsModalOpen(true);
        setIsDeleteConfirm(false);
    };

    const handleAddNew = () => {
        setCurrentTxn({
            name: '',
            amount: 0,
            frequency: 'MONTHLY',
            start_date: new Date().toISOString().split('T')[0],
            create_days_before: 0,
            subcategory: null,
            account: accounts.length > 0 ? accounts[0].account_id : null
        });
        setIsModalOpen(true);
        setIsDeleteConfirm(false);
    };

    const handleSave = async () => {
        // Basic Validation
        if (!currentTxn.name || !currentTxn.amount || !currentTxn.account) {
            notify("Please fill in Name, Amount, and Account.");
            return;
        }

        const method = currentTxn.id ? 'PUT' : 'POST';
        const url = currentTxn.id
            ? `/api/recurring/${currentTxn.id}/`
            : `/api/recurring/`;

        try {
            const res = await apiFetch(url, {
                method: method,
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(currentTxn)
            });

            if (res.ok) {
                closeModal();
                fetchData();
            } else {
                const err = await res.json();
                console.error("Failed to save", err);
                const reasons = Object.entries(err || {}).map(([field, why]) => `${field}: ${([] as unknown[]).concat(why).join(' ')}`);
                notify(`Could not save. ${reasons.join(' ') || `Error ${res.status}.`}`);
            }
        } catch (err) {
            console.error(err);
        }
    };

    const handleDelete = async () => {
        if (!currentTxn.id) return;
        try {
            await apiFetch(`/api/recurring/${currentTxn.id}/`, { method: 'DELETE' });
            closeModal();
            fetchData();
        } catch (err) {
            console.error(err);
        }
    };

    const closeModal = () => {
        setIsModalOpen(false);
        setIsDeleteConfirm(false);
        setCurrentTxn({});
    };

    const formatCurrency = (val: number) => {
        return new Intl.NumberFormat('en-US', { style: 'currency', currency: 'USD' }).format(val);
    };

    const getFrequencyLabel = (val: string) => FREQUENCIES.find(f => f.value === val)?.label || val;
    
    const getAccountName = (id: number | null | undefined) => accounts.find(a => a.account_id === id)?.name || '-';

    // --- SORTING LOGIC ---
    const sortedRecurring = [...recurring].sort((a, b) => {
        let valA: any = a[sortConfig.key];
        let valB: any = b[sortConfig.key];

        // Translate IDs/Codes to human-readable strings before sorting
        if (sortConfig.key === 'account') {
            valA = getAccountName(a.account);
            valB = getAccountName(b.account);
        } else if (sortConfig.key === 'frequency') {
            valA = getFrequencyLabel(a.frequency);
            valB = getFrequencyLabel(b.frequency);
        }

        if (valA === undefined || valA === null) valA = '';
        if (valB === undefined || valB === null) valB = '';

        if (typeof valA === 'number' && typeof valB === 'number') {
            return sortConfig.direction === 'asc' ? valA - valB : valB - valA;
        }

        const strA = valA.toString().toLowerCase();
        const strB = valB.toString().toLowerCase();

        if (strA < strB) return sortConfig.direction === 'asc' ? -1 : 1;
        if (strA > strB) return sortConfig.direction === 'asc' ? 1 : -1;
        return 0;
    });

    // --- HELPER COMPONENT FOR CLEAN HEADERS ---
    const SortableHeader = ({ label, field, align = 'left' }: { label: string, field: SortField, align?: 'left' | 'right' | 'center' }) => (
        <th 
            className={`px-6 py-4 font-medium cursor-pointer hover:text-white hover:bg-gray-800 transition-colors select-none`}
            onClick={() => handleSort(field)}
        >
            <div className={`flex items-center gap-2 ${align === 'right' ? 'justify-end' : align === 'center' ? 'justify-center' : ''}`}>
                {label}
                <div className="flex flex-col">
                    {sortConfig.key === field ? (
                        sortConfig.direction === 'asc' ? 
                            <ChevronUpIcon className="w-3 h-3 text-blue-400 stroke-[3]" /> : 
                            <ChevronDownIcon className="w-3 h-3 text-blue-400 stroke-[3]" />
                    ) : (
                        <ArrowsUpDownIcon className="w-3 h-3 text-gray-600" />
                    )}
                </div>
            </div>
        </th>
    );

    return (
        <div className="space-y-6">
            <div className="flex justify-between items-end">
                <div>
                    <h1 className="text-3xl font-bold text-white tracking-tight">Recurring Transactions</h1>
                    <p className="text-gray-400 mt-1 text-sm">Manage automated entries and bills</p>
                </div>
                <button
                    data-tour="add-recurring"
                    onClick={handleAddNew}
                    className="bg-blue-600 hover:bg-blue-700 text-white px-4 py-2 rounded-lg text-sm font-bold transition-colors"
                >
                    + Add Recurring Rule
                </button>
            </div>

            {/* --- TABLE --- */}
            <div className="relative overflow-x-auto shadow-md sm:rounded-lg border border-gray-800">
                <table className="w-full text-sm text-left text-gray-400">
                    <thead className="text-xs text-gray-400 uppercase bg-gray-900/50 border-b border-gray-800">
                        <tr>
                            <SortableHeader label="Name" field="name" />
                            <SortableHeader label="Amount" field="amount" align="right" />
                            <SortableHeader label="Frequency" field="frequency" />
                            <SortableHeader label="Account" field="account" />
                            <SortableHeader label="Category" field="subcategory_name" />
                            <SortableHeader label="Next Date" field="start_date" />
                            <SortableHeader label="Auto-Enter" field="create_days_before" />
                        </tr>
                    </thead>
                    <tbody>
                        {loading ? (
                            <tr><td colSpan={7} className="text-center py-8 text-gray-500">Loading rules...</td></tr>
                        ) : sortedRecurring.length === 0 ? (
                            <tr><td colSpan={7} className="text-center py-8 text-gray-500">No recurring transactions found.</td></tr>
                        ) : (
                            sortedRecurring.map((row) => (
                                <tr
                                    key={row.id}
                                    onClick={() => handleRowClick(row)}
                                    className="border-b transition-colors cursor-pointer bg-gray-800/50 border-gray-800 hover:bg-gray-700"
                                >
                                    <td className="px-6 py-4 text-white font-medium">{row.name}</td>
                                    <td className="px-6 py-4 font-bold font-mono text-right text-white">
                                        {formatCurrency(Number(row.amount))}
                                    </td>
                                    <td className="px-6 py-4">
                                        <span className="bg-purple-900/30 text-purple-400 border border-purple-500/30 px-2 py-1 rounded text-xs font-bold">
                                            {getFrequencyLabel(row.frequency)}
                                        </span>
                                    </td>
                                    <td className="px-6 py-4">
                                        <span className="bg-gray-700 text-gray-300 text-xs font-medium px-2.5 py-0.5 rounded border border-gray-600">
                                            {getAccountName(row.account)}
                                        </span>
                                    </td>
                                    <td className="px-6 py-4">{row.subcategory_name || '-'}</td>
                                    <td className="px-6 py-4 font-mono text-gray-300">
                                        {row.start_date}
                                    </td>
                                    <td className="px-6 py-4 text-xs">
                                        {row.create_days_before === 0
                                            ? <span className="text-green-400">On Due Date</span>
                                            : <span className="text-blue-400">{row.create_days_before} Days Before</span>}
                                    </td>
                                </tr>
                            ))
                        )}
                    </tbody>
                </table>
            </div>

            {/* --- MODAL POPUP --- */}
            {isModalOpen && (
                <div className="fixed inset-0 bg-black/70 backdrop-blur-sm flex items-center justify-center z-50 p-4">
                    <div className="bg-gray-900 border border-gray-800 rounded-xl shadow-2xl w-full max-w-lg overflow-hidden max-h-[90vh] overflow-y-auto">
                        
                        {/* Header */}
                        <div className="p-6 border-b border-gray-800 flex justify-between items-center sticky top-0 bg-gray-900 z-10">
                            <h2 className="text-xl font-bold text-white">
                                {isDeleteConfirm ? 'Confirm Deletion' : (currentTxn.id ? 'Edit Rule' : 'New Recurring Rule')}
                            </h2>
                            <button onClick={closeModal} className="text-gray-500 hover:text-white text-2xl leading-none">&times;</button>
                        </div>

                        {/* Content */}
                        {isDeleteConfirm ? (
                             <div className="p-8 text-center space-y-4">
                                <div className="text-red-500 mb-4">
                                    <svg className="w-16 h-16 mx-auto" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M19 7l-.867 12.142A2 2 0 0116.138 21H7.862a2 2 0 01-1.995-1.858L5 7m5 4v6m4-6v6m1-10V4a1 1 0 00-1-1h-4a1 1 0 00-1 1v3M4 7h16" /></svg>
                                </div>
                                <h3 className="text-xl font-bold text-white">Delete {currentTxn.name}?</h3>
                                <p className="text-gray-400">This will stop future transactions from being created. Existing history will remain.</p>
                                <div className="flex justify-center space-x-4 mt-8 pt-4">
                                    <button onClick={() => setIsDeleteConfirm(false)} className="px-6 py-2 bg-gray-700 text-white rounded-lg hover:bg-gray-600">Cancel</button>
                                    <button onClick={handleDelete} className="px-6 py-2 bg-red-600 text-white rounded-lg hover:bg-red-700">Yes, Delete</button>
                                </div>
                            </div>
                        ) : (
                            <>
                                <div className="p-6 space-y-4 text-left">
                                    <div className="grid grid-cols-2 gap-4">
                                        <div>
                                            <label className="block text-xs font-bold text-gray-500 uppercase mb-1">Transaction Name</label>
                                            <input
                                                type="text"
                                                value={currentTxn.name || ''}
                                                onChange={(e) => setCurrentTxn({...currentTxn, name: e.target.value})}
                                                className="w-full bg-gray-800 border-gray-700 rounded-lg text-white p-2 focus:ring-2 focus:ring-blue-600 outline-none"
                                            />
                                        </div>
                                        <div>
                                            <label className="block text-xs font-bold text-gray-500 uppercase mb-1">Amount</label>
                                            <input
                                                type="number"
                                                step="0.01"
                                                value={currentTxn.amount || ''}
                                                onChange={(e) => setCurrentTxn({...currentTxn, amount: parseFloat(e.target.value)})}
                                                className="w-full bg-gray-800 border-gray-700 rounded-lg text-white p-2 focus:ring-2 focus:ring-blue-600 outline-none"
                                            />
                                        </div>
                                    </div>

                                    <div className="grid grid-cols-2 gap-4">
                                        <div>
                                            <label className="block text-xs font-bold text-gray-500 uppercase mb-1">Account</label>
                                            <select
                                                value={currentTxn.account?.toString() || ''}
                                                onChange={(e) => setCurrentTxn({...currentTxn, account: Number(e.target.value)})}
                                                className="w-full bg-gray-800 border-gray-700 rounded-lg text-white p-2 focus:ring-2 focus:ring-blue-600 outline-none"
                                            >
                                                {accounts.map(acc => (
                                                    <option key={acc.account_id} value={acc.account_id}>{acc.name}</option>
                                                ))}
                                            </select>
                                        </div>
                                        <div>
                                            <label className="block text-xs font-bold text-gray-500 uppercase mb-1">Frequency</label>
                                            <select
                                                value={currentTxn.frequency || 'MONTHLY'}
                                                onChange={(e) => setCurrentTxn({...currentTxn, frequency: e.target.value})}
                                                className="w-full bg-gray-800 border-gray-700 rounded-lg text-white p-2 focus:ring-2 focus:ring-blue-600 outline-none"
                                            >
                                                {FREQUENCIES.map(f => (
                                                    <option key={f.value} value={f.value}>{f.label}</option>
                                                ))}
                                            </select>
                                        </div>
                                    </div>

                                    <div className="grid grid-cols-2 gap-4">
                                        <div>
                                            <label className="block text-xs font-bold text-gray-500 uppercase mb-1">Next Due Date</label>
                                            <input
                                                type="date"
                                                value={currentTxn.start_date || ''}
                                                onChange={(e) => setCurrentTxn({...currentTxn, start_date: e.target.value})}
                                                className="w-full bg-gray-800 border-gray-700 rounded-lg text-white p-2 focus:ring-2 focus:ring-blue-600 outline-none"
                                            />
                                        </div>
                                        <div>
                                            <label className="block text-xs font-bold text-gray-500 uppercase mb-1">Auto-Enter (Days Before)</label>
                                            <input
                                                type="number"
                                                value={currentTxn.create_days_before}
                                                onChange={(e) => setCurrentTxn({...currentTxn, create_days_before: parseInt(e.target.value) || 0})}
                                                className="w-full bg-gray-800 border-gray-700 rounded-lg text-white p-2 focus:ring-2 focus:ring-blue-600 outline-none"
                                            />
                                        </div>
                                    </div>

                                    <div>
                                        <label className="block text-xs font-bold text-gray-500 uppercase mb-1">Category</label>
                                        <select
                                            value={currentTxn.subcategory?.toString() || ''}
                                            onChange={(e) => setCurrentTxn({...currentTxn, subcategory: Number(e.target.value)})}
                                            className="w-full bg-gray-800 border-gray-700 rounded-lg text-white p-2 focus:ring-2 focus:ring-blue-600 outline-none"
                                        >
                                            <option value="">Uncategorized</option>
                                            {categories.map(cat => (
                                                <optgroup key={cat.id} label={cat.name} className="text-gray-400 font-bold bg-gray-900">
                                                    {cat.subcategories.map(sub => (
                                                        <option key={sub.id} value={sub.id} className="text-white bg-gray-800 font-normal">
                                                            {sub.name}
                                                        </option>
                                                    ))}
                                                </optgroup>
                                            ))}
                                        </select>
                                    </div>
                                </div>

                                {/* Footer */}
                                <div className="p-6 bg-gray-800/30 flex justify-between items-center border-t border-gray-800">
                                    <div>
                                        {currentTxn.id && (
                                            <button
                                                onClick={() => setIsDeleteConfirm(true)}
                                                className="text-red-400 hover:text-red-300 text-sm font-bold"
                                            >
                                                Delete Rule
                                            </button>
                                        )}
                                    </div>
                                    <div className="flex space-x-3">
                                        <button onClick={closeModal} className="px-4 py-2 text-gray-400 font-medium hover:text-white transition-colors">Cancel</button>
                                        <button onClick={handleSave} className="px-6 py-2 bg-blue-600 hover:bg-blue-500 text-white font-bold rounded-lg transition-colors">
                                            {currentTxn.id ? 'Update Rule' : 'Create Rule'}
                                        </button>
                                    </div>
                                </div>
                            </>
                        )}
                    </div>
                </div>
            )}
        </div>
    );
}