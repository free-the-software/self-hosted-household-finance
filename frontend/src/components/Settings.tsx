// Copyright (C) 2026 free-the-software
// SPDX-License-Identifier: AGPL-3.0-or-later
// This file is part of Save More Money, under the GNU AGPL v3 or later: see LICENSE.
import { useState, useEffect } from 'react';
import { useSearchParams } from 'react-router';
import { 
    TrashIcon, 
    ArchiveBoxIcon, 
    PlusCircleIcon, 
    Bars3Icon,
    XMarkIcon,
    ExclamationTriangleIcon,
    ChevronUpIcon,
    ChevronDownIcon
} from '@heroicons/react/24/outline';
import api, { getSession } from '../api';
import type { Session } from '../api';
import MyAccount from './MyAccount';
import UserSettings from './UserSettings';
import CategoryRemove from './CategoryRemove';
import UpdatesPanel from './UpdatesPanel';
import ReceiptReaderPanel from './ReceiptReaderPanel';
import SystemSettingsPanel from './SystemSettingsPanel';
import RetentionSettings from './RetentionSettings';
import HoldingForm, { KIND_LABELS, isLoan } from './HoldingForm';
import type { Holding, HoldingKind } from './HoldingForm';
import BackupSettings from './BackupSettings';
import { askConfirm, notify } from './Dialogs';

// --- DEFINED TYPES ---
type ModalViewType = 'ACCOUNT' | 'CATEGORY' | 'SUBCAT' | 'RULE';
type CategoryType = 'INC' | 'EXP' | 'XFR';

interface Account {
    account_id: number;
    name: string;
    // This bank's files write money out as positive; imports reverse them
    import_amounts_reversed?: boolean;
    import_sign_confirmed?: boolean;
    fid?: string;
    account_number?: string;
    institution?: string;
    account_type: string;
    tax_status?: string;
    owner?: string;
    is_liability: boolean;
    invert_sign: boolean;
    interest_rate?: number;
    credit_limit?: number;
    due_day?: number;
    closed_date?: string;
    active: boolean;
    show_forecast: boolean;
    last_balance: number;
}

interface Category {
    id: number;
    name: string;
    type: CategoryType;
    sort_order: number;
    // role_label: the job the app gives it (loan principal, receipt sales tax...), if any
    subcategories: { id: number, name: string, role_label?: string | null }[];
}

interface RuleCondition {
    field: string;
    operator: string;
    value: string;
}

interface TransactionRule {
    id: number;
    rule_name: string;
    conditions: RuleCondition[]; 
    logic_operator: 'AND' | 'OR';
    clean_merchant_name?: string;
    subcategory: number; 
    subcategory_name?: string;
    category_name?: string;
    // Its place in the list (rules are tried top first); moved, not typed
    position: number;
    is_active: boolean;
    // How often it has matched on import, and when it last did
    hit_count?: number;
    last_matched?: string | null;
    // From the server: its place in the order rules are tried, and problems
    order?: number;
    duplicate_of?: { id: number; rule_name: string } | null;
    shadowed_by?: { id: number; rule_name: string } | null;
    redundant_with?: { id: number; rule_name: string } | null;
}

const FIELD_LABELS: Record<string, string> = { name: 'Merchant', description: 'Merchant', amount: 'Amount', personal_memo: 'Memo', account: 'Account' };
const OPERATOR_LABELS: Record<string, string> = { contains: 'contains', exact: 'is', startswith: 'starts with', endswith: 'ends with', gt: 'over', lt: 'under' };

// A rule's conditions in words: Merchant contains "COSTCO" AND Amount over $50
const describeConditions = (rule: TransactionRule) => (rule.conditions || [])
    .map(cond => `${FIELD_LABELS[cond.field] || cond.field} ${OPERATOR_LABELS[cond.operator] || cond.operator} ${cond.field === 'amount' ? `$${cond.value}` : `"${cond.value}"`}`)
    .join(` ${rule.logic_operator || 'AND'} `);

// The Rules tab's sortable columns, and the value each sorts by
type RuleSortKey = 'order' | 'rule' | 'when' | 'rename' | 'category' | 'used';
const RULE_SORT_VALUE: Record<RuleSortKey, (rule: TransactionRule) => string | number> = {
    order: rule => rule.order ?? 0,
    rule: rule => rule.rule_name.toLowerCase(),
    when: rule => describeConditions(rule).toLowerCase(),
    rename: rule => (rule.clean_merchant_name || '').toLowerCase(),
    category: rule => (rule.subcategory_name || '').toLowerCase(),
    used: rule => rule.hit_count ?? 0,
};

// What may be wrong with a rule, in words, for the Rules tab
const ruleProblems = (rule: TransactionRule) => [
    rule.duplicate_of && `Same conditions as "${rule.duplicate_of.rule_name}", which is tried first. This one never runs.`,
    rule.shadowed_by && `Never runs: "${rule.shadowed_by.rule_name}" always matches these transactions first.`,
    rule.redundant_with && `Not needed: "${rule.redundant_with.rule_name}" gives the same name and category anyway.`,
].filter(Boolean) as string[];

// --- Constants ---
const ACCOUNT_TYPES = ['CHECKING', 'SAVINGS', 'CREDIT CARD', 'LOAN', 'INVESTMENT', 'CASH'];

// "Add account" asks the type first. Bank accounts get transactions from
// bank files; assets and loans are kept by hand (values, payments).
const BANK_CHOICES: [string, string][] = [
    ['CHECKING', 'Checking'], ['SAVINGS', 'Savings'], ['CREDIT CARD', 'Credit card'],
    ['INVESTMENT', 'Investment (imported from bank files)'], ['CASH', 'Cash'],
];
const HOLDING_CHOICES: [HoldingKind, string][] = [
    ['home', 'Home'], ['vehicle', 'Vehicle'], ['investment', 'Investment (value from statements)'], ['other', 'Other asset'],
    ['mortgage', 'Mortgage'], ['auto_loan', 'Car loan'], ['loan', 'Other loan'],
];
const SETTINGS_TABS = ['Accounts', 'Categories', 'Rules', 'Storage', 'Backups', 'Users', 'My account'];
// In the address: "My account" is ?tab=my-account
const tabKey = (label: string) => label.toLowerCase().replace(/ /g, '-');
const TAX_STATUS_TYPES = ['Taxable', 'Tax-Deferred', 'Tax-Free'];

// --- Reusable Tailwind Classes ---
const inputClass = "w-full bg-gray-800 border border-gray-700 text-white text-sm rounded-lg p-2.5 focus:ring-blue-500 focus:border-blue-500";
// The same look without w-full, for the boxes sharing a rule condition's row
// (w-full would override their own widths and squeeze the value box)
const conditionBoxClass = inputClass.replace('w-full ', '');
const labelClass = "block text-xs font-bold text-gray-400 mb-1";
const sectionHeaderClass = "text-xs font-bold text-blue-400 uppercase tracking-widest mb-3 border-b border-gray-800 pb-1 mt-6 first:mt-0";

const Settings = () => {
    // The tab is also in the address (?tab=rules), so a link or the tour can open one
    const [searchParams, setSearchParams] = useSearchParams();
    const tabOf = (name: string | null) => Math.max(SETTINGS_TABS.findIndex(label => tabKey(label) === name), 0);
    const [session, setSession] = useState<Session | null>(null);
    useEffect(() => { getSession().then(setSession).catch(() => undefined); }, []);
    const [tabIndex, setTabIndex] = useState(() => tabOf(searchParams.get('tab')));
    // A category or subcategory being deleted (CategoryRemove asks where its things go)
    const [removing, setRemoving] = useState<{ kind: 'category' | 'subcategory'; id: number; name: string } | null>(null);
    useEffect(() => setTabIndex(tabOf(searchParams.get('tab'))), [searchParams]);
    const [accounts, setAccounts] = useState<Account[]>([]);
    const [holdings, setHoldings] = useState<Holding[]>([]);
    const [choosingType, setChoosingType] = useState(false);
    const [holdingForm, setHoldingForm] = useState<{ kind: HoldingKind; holding: Holding | null } | null>(null);
    const [categories, setCategories] = useState<Category[]>([]);
    const [rules, setRules] = useState<TransactionRule[]>([]);
    const [ruleSearch, setRuleSearch] = useState('');
    const [onlyProblems, setOnlyProblems] = useState(false);
    // Which column the rules are sorted by; the order they are tried in by default
    const [ruleSort, setRuleSort] = useState<{ key: RuleSortKey; ascending: boolean }>({ key: 'order', ascending: true });
    // The rule being dragged, and the row it would land on
    const [dragRuleId, setDragRuleId] = useState<number | null>(null);
    const [dropRuleId, setDropRuleId] = useState<number | null>(null);

    // Moves a rule in the list: { to: place } (1 is the top) or { before: rule id }.
    // The server renumbers the list and sends it back.
    const moveRule = async (id: number, where: { to?: number; before?: number }) => {
        try {
            const res = await api.post(`/transaction-rules/${id}/move/`, where);
            setRules(res.data);
        } catch (err: any) {
            notify('Could not move the rule: ' + (err.response?.data?.error || err.message));
        }
    };

    const askMoveTo = (rule: TransactionRule) => {
        const answer = window.prompt(`Move "${rule.rule_name}" to which place? (1 is the top, ${rules.length} the bottom)`, String(rule.order ?? ''));
        if (answer === null) return;
        const place = Number.parseInt(answer, 10);
        if (Number.isNaN(place)) { notify('Enter a whole number.'); return; }
        moveRule(rule.id, { to: place });
    };
    const [openModal, setOpenModal] = useState(false);
    
    const [modalType, setModalType] = useState<ModalViewType>('ACCOUNT');
    const [editItem, setEditItem] = useState<any>({}); 

    // --- DRAG AND DROP STATE ---
    const [draggedIdx, setDraggedIdx] = useState<number | null>(null);

    useEffect(() => {
        fetchData();
    }, []);

    const fetchData = async () => {
        try {
            const [accRes, catRes, ruleRes, holdRes] = await Promise.all([
                api.get('/accounts/'),
                api.get('/categories/'),
                api.get('/transaction-rules/'),
                api.get('/holdings/'),
            ]);
            setAccounts(accRes.data);
            setHoldings(holdRes.data);
            const sortedCats = catRes.data.sort((a: Category, b: Category) => a.sort_order - b.sort_order);
            setCategories(sortedCats);
            setRules(ruleRes.data);
        } catch (e) {
            console.error("Failed to load settings", e);
        }
    };

    // --- DRAG AND DROP HANDLERS ---
    const handleDragStart = (e: React.DragEvent, index: number) => {
        setDraggedIdx(index);
        e.dataTransfer.effectAllowed = 'move';
        e.dataTransfer.setData('text/html', index.toString());
    };

    const handleDragOver = (e: React.DragEvent) => {
        e.preventDefault();
        e.dataTransfer.dropEffect = 'move';
    };

    const handleDrop = async (e: React.DragEvent, dropIdx: number) => {
        e.preventDefault();
        if (draggedIdx === null || draggedIdx === dropIdx) return;

        const newCategories = [...categories];
        const [movedCat] = newCategories.splice(draggedIdx, 1);
        newCategories.splice(dropIdx, 0, movedCat);
        setCategories(newCategories);
        setDraggedIdx(null);

        const ordered_ids = newCategories.map(c => c.id);
        try {
            await api.post('/categories/reorder/', { ordered_ids });
        } catch (err) {
            console.error("Reorder failed", err);
            fetchData();
        }
    };

    const handleDelete = async (type: ModalViewType, id: number) => {
        if (type === 'ACCOUNT') {
            if (!(await askConfirm({
                title: 'Close this account?',
                message: 'It is hidden from daily views. Its transactions and history are kept.',
                confirmLabel: 'Close account',
            }))) return;
            try {
                await api.post(`/accounts/${id}/close/`);
                fetchData();
            } catch (err: any) {
                notify(err.response?.data?.error || "Failed to close account");
            }
            return; 
        }
        
        // Rules; categories and subcategories go through CategoryRemove, which asks where their things go
        if (!(await askConfirm({ title: 'Delete this rule?', message: 'This cannot be undone.', confirmLabel: 'Delete', danger: true }))) return;

        try {
            await api.delete(`/transaction-rules/${id}/`);
            fetchData();
        } catch (err: any) {
            notify(err.response?.data?.error || "Delete failed");
        }
    };

    const addCondition = () => {
        setEditItem({
            ...editItem,
            conditions: [...(editItem.conditions || []), { field: 'name', operator: 'contains', value: '' }]
        });
    };

    const removeCondition = (index: number) => {
        const newConds = [...(editItem.conditions || [])];
        newConds.splice(index, 1);
        setEditItem({ ...editItem, conditions: newConds });
    };

    const updateCondition = (index: number, key: string, val: string) => {
        const newConds = [...(editItem.conditions || [])];
        const updated = { ...newConds[index], [key]: val };
        // Amounts and text have different comparisons: keep one that fits the field
        if (key === 'field') {
            const amountOps = ['exact', 'gt', 'lt'];
            const textOps = ['contains', 'exact', 'startswith', 'endswith'];
            const allowed = val === 'amount' ? amountOps : textOps;
            if (!allowed.includes(updated.operator)) updated.operator = val === 'amount' ? 'exact' : 'contains';
        }
        newConds[index] = updated;
        setEditItem({ ...editItem, conditions: newConds });
    };

    const handleSave = async () => {
        try {
            if (modalType === 'ACCOUNT') {
                // Only the name: the account number links QFX/OFX files, and a file that
                // isn't recognised waits in the Import Inbox for you to pick its account
                if (!editItem.name) {
                    notify("Give the account a name."); return; 
                }
                const payload = {
                    name: editItem.name,
                    account_type: editItem.account_type,
                    fid: editItem.fid,
                    account_number: editItem.account_number,
                    institution: editItem.institution,
                    tax_status: editItem.tax_status,
                    owner: editItem.owner,
                    interest_rate: editItem.interest_rate || null,
                    credit_limit: editItem.credit_limit || null,
                    due_day: editItem.due_day || null,
                    closed_date: editItem.closed_date || null,
                    show_forecast: editItem.show_forecast || false,
                    active: editItem.active !== undefined ? editItem.active : true,
                    // Only when changed here: sending it counts as the answer, so imports stop asking
                    ...(editItem.reversedChanged ? { import_amounts_reversed: !!editItem.import_amounts_reversed } : {}),
                };

                if (editItem.account_id) {
                    await api.patch(`/accounts/${editItem.account_id}/`, payload);
                } else {
                    await api.post('/accounts/', payload);
                }
            } 
            else if (modalType === 'CATEGORY') {
                const payload = { name: editItem.name, type: editItem.type || 'EXP' };
                if (editItem.id) await api.patch(`/categories/${editItem.id}/`, payload);
                else await api.post('/categories/', payload);
            }
            else if (modalType === 'SUBCAT') {
                // Renamed, or moved to another category: its history keeps up, as it points at the subcategory
                const payload = { name: editItem.name, category: editItem.category };
                if (editItem.id) await api.patch(`/subcategories/${editItem.id}/`, payload);
                else await api.post('/subcategories/', payload);
            }
            else if (modalType === 'RULE') {
                const payload = {
                    rule_name: editItem.rule_name,
                    conditions: editItem.conditions,
                    logic_operator: editItem.logic_operator,
                    clean_merchant_name: editItem.clean_merchant_name || '',
                    subcategory: editItem.subcategory, 
                    is_active: editItem.is_active !== undefined ? editItem.is_active : true
                };

                if (editItem.id) {
                    await api.patch(`/transaction-rules/${editItem.id}/`, payload);
                } else {
                    await api.post('/transaction-rules/', payload);
                }
            }

            setOpenModal(false);
            fetchData();
        } catch (err: any) {
            const data = err.response?.data;
            // e.g. {"conditions": ["An amount condition needs a number."]}
            const reasons = data && typeof data === 'object' ? Object.values(data).flat().join(' ') : '';
            notify("Save failed: " + (reasons || JSON.stringify(data)));
        }
    };

    const openEditAccount = (acc?: Account) => {
        setEditItem(acc || { show_forecast: false, active: true, account_type: 'CHECKING', is_liability: false, invert_sign: false });
        setModalType('ACCOUNT');
        setOpenModal(true);
    };

    const openNewSubcat = (catId: number) => {
        setEditItem({ category: catId, name: '' });
        setModalType('SUBCAT');
        setOpenModal(true);
    };

    const openEditRule = (rule?: TransactionRule) => {
        setEditItem(rule || { 
            rule_name: '', conditions: [{ field: 'name', operator: 'contains', value: '' }], logic_operator: 'AND', is_active: true, subcategory: ''
        });
        setModalType('RULE');
        setOpenModal(true);
    };

    return (
        <div className="p-6 text-gray-100 max-w-7xl mx-auto h-full">
            <h1 className="text-3xl font-bold text-white mb-6 tracking-tight">System Settings</h1>
            
            {/* --- TABS --- */}
            <div className="flex flex-wrap gap-x-6 gap-y-2 border-b border-gray-800 mb-6">
                {SETTINGS_TABS.map((label, idx) => (label === 'Users' && !session?.is_admin) ? null : (
                    <button
                        key={idx}
                        onClick={() => setSearchParams({ tab: tabKey(label) }, { replace: true })}
                        className={`pb-3 font-medium text-sm transition-colors relative ${
                            tabIndex === idx 
                            ? 'text-blue-500' 
                            : 'text-gray-400 hover:text-gray-200'
                        }`}
                    >
                        {label}
                        {tabIndex === idx && (
                            <span className="absolute bottom-0 left-0 w-full h-0.5 bg-blue-500 rounded-t-md"></span>
                        )}
                    </button>
                ))}
            </div>

            {/* --- TAB 0: ACCOUNTS --- */}
            {tabIndex === 0 && (
                <div className="animate-in fade-in duration-300">
                    <div className="relative inline-block mb-4">
                        <button 
                            data-tour="add-account"
                            onClick={() => setChoosingType(!choosingType)} 
                            className="bg-blue-600 hover:bg-blue-500 text-white px-4 py-2 rounded-lg text-sm font-bold transition-colors flex items-center shadow-lg"
                        >
                            <PlusCircleIcon className="w-5 h-5 mr-2" /> Add Account
                        </button>
                        {choosingType && (
                            <div className="absolute z-40 mt-2 w-80 bg-gray-900 border border-gray-700 rounded-xl shadow-2xl p-2">
                                <p className="px-2 pt-1 pb-1 text-[10px] font-black uppercase text-gray-500">Bank accounts (transactions from bank files)</p>
                                {BANK_CHOICES.map(([type, label]) => (
                                    <button key={type} onClick={() => { setChoosingType(false); openEditAccount({ show_forecast: type === 'CHECKING', active: true, account_type: type, is_liability: type === 'CREDIT CARD', invert_sign: false } as any); }}
                                        className="block w-full text-left px-2 py-1.5 rounded-lg text-sm text-gray-200 hover:bg-gray-800">{label}</button>
                                ))}
                                <p className="px-2 pt-3 pb-1 text-[10px] font-black uppercase text-gray-500">Assets and loans (kept by hand)</p>
                                {HOLDING_CHOICES.map(([kind, label]) => (
                                    <button key={kind} onClick={() => { setChoosingType(false); setHoldingForm({ kind, holding: null }); }}
                                        className="block w-full text-left px-2 py-1.5 rounded-lg text-sm text-gray-200 hover:bg-gray-800">{label}</button>
                                ))}
                            </div>
                        )}
                    </div>
                    
                    <div data-tour="accounts-table" className="bg-gray-900 rounded-xl border border-gray-800 overflow-hidden shadow-xl">
                        <table className="w-full text-left text-sm text-gray-400">
                            <thead className="bg-gray-950 text-xs uppercase text-gray-500 font-bold border-b border-gray-800">
                                <tr>
                                    <th className="px-6 py-4">Name</th>
                                    <th className="px-6 py-4">Details</th>
                                    <th className="px-6 py-4">Status</th>
                                    <th className="px-6 py-4 text-right">Actions</th>
                                </tr>
                            </thead>
                            <tbody className="divide-y divide-gray-800">
                                {accounts.map(acc => (
                                    <tr 
                                        key={acc.account_id} 
                                        onClick={() => openEditAccount(acc)}
                                        className="hover:bg-gray-800/40 transition-colors cursor-pointer group"
                                    >
                                        <td className="px-6 py-4 text-white font-bold">{acc.name}</td>
                                        <td className="px-6 py-4">
                                            <div className="text-gray-300 mb-1">{acc.institution}</div>
                                            <span className="bg-gray-800 text-gray-400 text-[10px] font-bold px-2 py-0.5 rounded border border-gray-700">
                                                {acc.account_type}
                                            </span>
                                        </td>
                                        <td className="px-6 py-4">
                                            {acc.active ? <span className="text-green-400 font-bold text-xs">Active</span> : <span className="text-red-400 font-bold text-xs">Closed</span>}
                                        </td>
                                        <td className="px-6 py-4 text-right">
                                            {acc.active && (
                                                <button 
                                                    onClick={(e) => { e.stopPropagation(); handleDelete('ACCOUNT', acc.account_id); }} 
                                                    className="p-2 text-gray-500 hover:text-red-400 hover:bg-red-900/20 rounded-lg transition-colors"
                                                >
                                                    <ArchiveBoxIcon className="w-5 h-5" />
                                                </button>
                                            )}
                                        </td>
                                    </tr>
                                ))}
                                {holdings.map(h => (
                                    <tr
                                        key={`holding-${h.id}`}
                                        onClick={() => setHoldingForm({ kind: h.kind, holding: h })}
                                        className="hover:bg-gray-800/40 transition-colors cursor-pointer group"
                                    >
                                        <td className="px-6 py-4 text-white font-bold">{h.name}</td>
                                        <td className="px-6 py-4">
                                            <div className="text-gray-300 mb-1">{isLoan(h.kind) ? h.loan?.lender : ''}</div>
                                            <span className="bg-gray-800 text-gray-400 text-[10px] font-bold px-2 py-0.5 rounded border border-gray-700 uppercase">
                                                {KIND_LABELS[h.kind]}
                                            </span>
                                        </td>
                                        <td className="px-6 py-4">
                                            {h.active ? <span className="text-green-400 font-bold text-xs">Active</span> : <span className="text-red-400 font-bold text-xs">Closed</span>}
                                        </td>
                                        <td className="px-6 py-4 text-right">
                                            <button
                                                onClick={async (e) => {
                                                    e.stopPropagation();
                                                    if (!(await askConfirm({ title: `Delete "${h.name}" and its recorded values?`, confirmLabel: 'Delete', danger: true }))) return;
                                                    try { await api.delete(`/holdings/${h.id}/`); fetchData(); }
                                                    catch (err: any) { notify(err.response?.data?.error || 'Could not delete it.'); }
                                                }}
                                                className="p-2 text-gray-500 hover:text-red-400 hover:bg-red-900/20 rounded-lg transition-colors"
                                                title="Delete"
                                            >
                                                <TrashIcon className="w-5 h-5" />
                                            </button>
                                        </td>
                                    </tr>
                                ))}
                            </tbody>
                        </table>
                    </div>
                    {holdingForm && (
                        <HoldingForm
                            kind={holdingForm.kind}
                            holding={holdingForm.holding}
                            assets={holdings.filter(h => !isLoan(h.kind))}
                            onClose={() => setHoldingForm(null)}
                            onSaved={() => { setHoldingForm(null); fetchData(); }}
                        />
                    )}
                </div>
            )}

            {/* --- TAB 1: CATEGORIES --- */}
            {tabIndex === 1 && (
                <div className="animate-in fade-in duration-300">
                    <div className="flex justify-between items-center mb-6">
                        <button 
                            data-tour="add-category"
                            onClick={() => { setEditItem({}); setModalType('CATEGORY'); setOpenModal(true); }}
                            className="bg-blue-600 hover:bg-blue-500 text-white px-4 py-2 rounded-lg text-sm font-bold transition-colors flex items-center shadow-lg"
                        >
                            <PlusCircleIcon className="w-5 h-5 mr-2" /> Add Category
                        </button>
                        <p className="text-xs text-gray-400 italic">Click a name to rename or move it. Drag categories to reorder them.</p>
                    </div>
                    
                    <div className="flex flex-col gap-3">
                        {categories.map((cat, index) => (
                            <div 
                                key={cat.id}
                                draggable
                                onDragStart={(e) => handleDragStart(e, index)}
                                onDragOver={handleDragOver}
                                onDrop={(e) => handleDrop(e, index)}
                                onDragEnd={() => setDraggedIdx(null)}
                                className={`bg-gray-900 border rounded-xl p-4 transition-all duration-200 ${
                                    draggedIdx === index 
                                    ? 'opacity-40 scale-[0.99] border-blue-500 shadow-lg' 
                                    : 'opacity-100 scale-100 border-gray-800'
                                }`}
                            >
                                <div className="flex justify-between items-center mb-2">
                                    <div className="flex items-center gap-3">
                                        <div className="cursor-grab active:cursor-grabbing text-gray-600 hover:text-white p-1">
                                            <Bars3Icon className="w-6 h-6" />
                                        </div>
                                        <button type="button" className="text-left group" title="Rename, or change its type"
                                            onClick={() => { setEditItem({ id: cat.id, name: cat.name, type: cat.type, originalType: cat.type }); setModalType('CATEGORY'); setOpenModal(true); }}>
                                            <h3 className="text-lg font-bold text-white leading-none group-hover:underline decoration-dotted underline-offset-4">{cat.name}</h3>
                                            <span className={`text-[10px] font-black uppercase tracking-wider ${
                                                cat.type === 'INC' ? 'text-green-400' : 
                                                cat.type === 'XFR' ? 'text-blue-400' : 'text-red-400'
                                            }`}>
                                                {cat.type === 'INC' ? 'Income' : cat.type === 'XFR' ? 'Transfer' : 'Expense'}
                                            </span>
                                        </button>
                                    </div>
                                    <button onClick={() => setRemoving({ kind: 'category', id: cat.id, name: cat.name })} className="p-2 text-gray-600 hover:text-red-400 rounded-lg" aria-label={`Delete ${cat.name}`}>
                                        <TrashIcon className="w-5 h-5" />
                                    </button>
                                </div>
                                <div className="flex flex-wrap gap-2 pl-10">
                                    {cat.subcategories.map(sub => (
                                        <div key={sub.id} className="bg-gray-800 border border-gray-700 text-gray-300 text-xs px-3 py-1 rounded-full flex items-center">
                                            <button type="button" className="hover:text-white hover:underline decoration-dotted underline-offset-2" title="Rename, or move to another category"
                                                onClick={() => { setEditItem({ id: sub.id, name: sub.name, category: cat.id, role_label: sub.role_label }); setModalType('SUBCAT'); setOpenModal(true); }}>
                                                {sub.name}
                                            </button>
                                            {sub.role_label && <span className="ml-1.5 text-[10px] text-blue-300" title={`The app uses this for ${sub.role_label}`}>· {sub.role_label}</span>}
                                            <button onClick={() => setRemoving({ kind: 'subcategory', id: sub.id, name: sub.name })} className="ml-2 text-gray-500 hover:text-red-400" aria-label={`Delete ${sub.name}`}>
                                                <XMarkIcon className="w-3 h-3 stroke-[3]" />
                                            </button>
                                        </div>
                                    ))}
                                    <button 
                                        onClick={() => openNewSubcat(cat.id)}
                                        className="border border-blue-500/50 text-blue-400 hover:bg-blue-900/30 text-xs px-3 py-1 rounded-full transition-colors flex items-center"
                                    >
                                        + Add Sub
                                    </button>
                                </div>
                            </div>
                        ))}
                    </div>
                </div>
            )}

            {/* --- TAB 2: RULES --- */}
            {tabIndex === 2 && (() => {
                const term = ruleSearch.trim().toLowerCase();
                const shown = rules.filter(rule =>
                    (!onlyProblems || ruleProblems(rule).length > 0) &&
                    (!term || [rule.rule_name, describeConditions(rule), rule.clean_merchant_name, rule.subcategory_name]
                        .some(text => (text || '').toLowerCase().includes(term))));
                const problemCount = rules.filter(rule => ruleProblems(rule).length > 0).length;
                // Moving only makes sense when the list is shown in the order rules are tried;
                // dragging also needs every rule on screen
                const canMove = ruleSort.key === 'order' && ruleSort.ascending;
                const canDrag = canMove && !term && !onlyProblems;
                const value = RULE_SORT_VALUE[ruleSort.key];
                shown.sort((a, b) => {
                    const [x, y] = [value(a), value(b)];
                    const compared = typeof x === 'number' && typeof y === 'number' ? x - y : String(x).localeCompare(String(y));
                    // Ties keep the order rules are tried in
                    return (ruleSort.ascending ? compared : -compared) || (a.order ?? 0) - (b.order ?? 0);
                });
                // A header that sorts by its column; clicking it again reverses the order
                const header = (key: RuleSortKey, label: string, className = '') => (
                    <th className={`px-4 py-4 ${className}`} aria-sort={ruleSort.key === key ? (ruleSort.ascending ? 'ascending' : 'descending') : 'none'}>
                        <button
                            type="button"
                            onClick={() => setRuleSort(prev => ({ key, ascending: prev.key === key ? !prev.ascending : true }))}
                            className={`inline-flex items-center gap-1 uppercase font-bold hover:text-white ${ruleSort.key === key ? 'text-white' : ''}`}
                            title={`Sort by ${label.toLowerCase()}`}
                        >
                            {label}
                            <span className="text-[10px]">{ruleSort.key === key ? (ruleSort.ascending ? '▲' : '▼') : '↕'}</span>
                        </button>
                    </th>
                );
                return (
                <div className="animate-in fade-in duration-300 space-y-4">
                    <div className="flex flex-wrap items-center gap-3">
                        <button 
                            data-tour="add-rule"
                            onClick={() => openEditRule()} 
                            className="bg-blue-600 hover:bg-blue-500 text-white px-4 py-2 rounded-lg text-sm font-bold transition-colors flex items-center shadow-lg"
                        >
                            <PlusCircleIcon className="w-5 h-5 mr-2" /> Add Rule
                        </button>
                        <input
                            type="text" value={ruleSearch} onChange={e => setRuleSearch(e.target.value)}
                            placeholder="Search rules, conditions, names or categories..."
                            className="flex-1 min-w-[16rem] bg-gray-800 border border-gray-700 text-gray-300 text-sm rounded-lg p-2.5"
                        />
                        {problemCount > 0 && (
                            <button
                                onClick={() => setOnlyProblems(!onlyProblems)}
                                className={`flex items-center text-sm font-bold px-3 py-2 rounded-lg border ${onlyProblems ? 'bg-amber-500 text-gray-900 border-amber-400' : 'text-amber-300 border-amber-600/60 hover:bg-amber-900/30'}`}
                            >
                                <ExclamationTriangleIcon className="w-4 h-4 mr-1.5" />
                                {onlyProblems ? 'Showing' : 'Show'} {problemCount} to tidy
                            </button>
                        )}
                    </div>
                    <p className="text-xs text-gray-500">
                        Rules are tried from the top and the first that matches wins. {canMove
                            ? (canDrag ? 'Drag a rule by its handle, or use the arrows, to change the order.' : 'Use the arrows to change the order; clear the search and filter to drag.')
                            : 'Sort by # to change the order.'} A new rule is placed just above the first rule that would otherwise catch its
                        transactions. Text ignores case and extra spaces; amounts ignore the sign. Uses are counted when bank files are imported.
                    </p>
                    <div className="bg-gray-900 rounded-xl border border-gray-800 overflow-hidden shadow-xl">
                        <div className="overflow-x-auto">
                            <table className="w-full text-left text-sm text-gray-400">
                                <thead className="bg-gray-950 text-xs uppercase text-gray-500 font-bold border-b border-gray-800">
                                    <tr>
                                        {canMove && <th className="px-2 py-4 w-px"><span className="sr-only">Move</span></th>}
                                        {header('order', '#', 'w-px whitespace-nowrap')}
                                        {header('rule', 'Rule')}
                                        {header('when', 'When')}
                                        {header('rename', 'Renames To', 'whitespace-nowrap')}
                                        {header('category', 'Category')}
                                        {header('used', 'Used', 'text-right w-px whitespace-nowrap')}
                                        <th className="px-4 py-4 text-right w-px"></th>
                                    </tr>
                                </thead>
                                <tbody className="divide-y divide-gray-800">
                                    {shown.length === 0 && (
                                        <tr><td colSpan={canMove ? 8 : 7} className="px-4 py-8 text-center text-gray-500">No rules match.</td></tr>
                                    )}
                                    {shown.map(rule => {
                                        const problems = ruleProblems(rule);
                                        return (
                                        <tr 
                                            key={rule.id} 
                                            onClick={() => openEditRule(rule)}
                                            draggable={canDrag}
                                            onDragStart={canDrag ? (e) => { setDragRuleId(rule.id); e.dataTransfer.effectAllowed = 'move'; } : undefined}
                                            onDragOver={canDrag ? (e) => { e.preventDefault(); if (dropRuleId !== rule.id) setDropRuleId(rule.id); } : undefined}
                                            onDragEnd={() => { setDragRuleId(null); setDropRuleId(null); }}
                                            onDrop={canDrag ? (e) => {
                                                e.preventDefault();
                                                // The dragged rule takes this row's place
                                                if (dragRuleId !== null && dragRuleId !== rule.id && rule.order) moveRule(dragRuleId, { to: rule.order });
                                                setDragRuleId(null); setDropRuleId(null);
                                            } : undefined}
                                            className={`hover:bg-gray-800/40 transition-colors cursor-pointer group align-top ${rule.is_active ? '' : 'opacity-50'} ${problems.length ? 'bg-amber-900/10' : ''} ${dragRuleId === rule.id ? 'opacity-40' : ''} ${dropRuleId === rule.id && dragRuleId !== rule.id ? 'outline outline-2 outline-blue-500' : ''}`}
                                        >
                                            {canMove && (
                                                <td className="px-2 py-3 whitespace-nowrap" onClick={(e) => e.stopPropagation()}>
                                                    <div className="flex items-center gap-0.5">
                                                        {canDrag && <span className="cursor-grab active:cursor-grabbing text-gray-500 hover:text-white p-1" title="Drag to move"><Bars3Icon className="w-4 h-4" /></span>}
                                                        <button type="button" disabled={rule.order === 1} onClick={() => moveRule(rule.id, { to: (rule.order ?? 1) - 1 })} className="p-1 text-gray-500 hover:text-white disabled:opacity-20 disabled:hover:text-gray-500" title="Move up"><ChevronUpIcon className="w-4 h-4" /></button>
                                                        <button type="button" disabled={rule.order === rules.length} onClick={() => moveRule(rule.id, { to: (rule.order ?? 1) + 1 })} className="p-1 text-gray-500 hover:text-white disabled:opacity-20 disabled:hover:text-gray-500" title="Move down"><ChevronDownIcon className="w-4 h-4" /></button>
                                                        <button type="button" onClick={() => askMoveTo(rule)} className="px-1.5 py-0.5 text-[10px] font-bold uppercase text-gray-500 hover:text-white border border-gray-700 rounded" title="Move to a place in the list">To…</button>
                                                    </div>
                                                </td>
                                            )}
                                            <td className="px-4 py-3 font-mono text-gray-600">{rule.order}</td>
                                            <td className="px-4 py-3">
                                                <div className="text-white font-bold">{rule.rule_name}{!rule.is_active && <span className="ml-2 text-xs font-normal text-gray-500">(off)</span>}</div>
                                                {problems.map(problem => (
                                                    <div key={problem} className="flex items-start text-xs text-amber-300 mt-1">
                                                        <ExclamationTriangleIcon className="w-3.5 h-3.5 mr-1 mt-px shrink-0" /> {problem}
                                                    </div>
                                                ))}
                                                {rule.shadowed_by && (
                                                    <button
                                                        type="button"
                                                        onClick={(e) => { e.stopPropagation(); moveRule(rule.id, { before: rule.shadowed_by!.id }); }}
                                                        className="mt-1 text-xs font-bold text-blue-400 hover:text-blue-300 underline"
                                                    >
                                                        Move above "{rule.shadowed_by.rule_name}"
                                                    </button>
                                                )}
                                            </td>
                                            <td className="px-4 py-3 text-xs text-gray-300 font-mono">{describeConditions(rule) || <span className="text-gray-600 italic">no conditions</span>}</td>
                                            <td className="px-4 py-3 text-gray-300">{rule.clean_merchant_name || <span className="text-gray-600">—</span>}</td>
                                            <td className="px-4 py-3">
                                                {rule.subcategory_name ? <span className="bg-gray-800 border border-gray-700 text-gray-300 px-2 py-1 rounded text-xs whitespace-nowrap">{rule.subcategory_name}</span> : '-'}
                                            </td>
                                            <td className="px-4 py-3 text-right whitespace-nowrap" title={rule.last_matched ? `Last used ${new Date(rule.last_matched).toLocaleDateString()}` : 'Not used since use counting began'}>
                                                {rule.hit_count ? <span className="text-gray-300">{rule.hit_count}×</span> : <span className="text-gray-600">0</span>}
                                            </td>
                                            <td className="px-4 py-3 text-right">
                                                <button 
                                                    onClick={(e) => { e.stopPropagation(); handleDelete('RULE', rule.id); }} 
                                                    className="p-2 text-gray-500 hover:text-red-400 hover:bg-red-900/20 rounded-lg transition-colors"
                                                    title="Delete rule"
                                                >
                                                    <TrashIcon className="w-5 h-5" />
                                                </button>
                                            </td>
                                        </tr>
                                        );
                                    })}
                                </tbody>
                            </table>
                        </div>
                    </div>
                </div>
                );
            })()}

            {/* --- TAB 3: STORAGE & RETENTION --- */}
            {tabIndex === 3 && <><UpdatesPanel /><ReceiptReaderPanel /><SystemSettingsPanel /><RetentionSettings /></>}

            {/* --- TAB 4: BACKUPS --- */}
            {tabIndex === 4 && <BackupSettings />}

            {/* --- TAB 5: USERS (admins) --- */}
            {tabIndex === 5 && (session?.is_admin ? <UserSettings session={session} /> : <p className="text-gray-400">Only an admin can manage users.</p>)}

            {/* --- TAB 6: MY ACCOUNT --- */}
            {tabIndex === 6 && <MyAccount session={session} />}

            {removing && (
                <CategoryRemove kind={removing.kind} target={removing} categories={categories}
                    onClose={() => setRemoving(null)} onDone={() => { setRemoving(null); fetchData(); }} />
            )}

            {/* --- SHARED MODAL --- */}
            {openModal && (
                <div className="fixed inset-0 z-50 bg-black/80 backdrop-blur-sm flex items-center justify-center p-4">
                    <div className="bg-gray-900 border border-gray-800 rounded-2xl w-full max-w-3xl flex flex-col max-h-[90vh] shadow-2xl animate-in zoom-in-95 duration-200">
                        {/* Modal Header */}
                        <div className="flex justify-between items-center p-6 border-b border-gray-800 bg-gray-950 shrink-0">
                            <h2 className="text-lg font-bold text-white">
                                {modalType === 'ACCOUNT' ? (editItem.account_id ? 'Edit Account' : 'New Account') : 
                                 modalType === 'CATEGORY' ? (editItem.id ? 'Edit Category' : 'New Category') : 
                                 modalType === 'RULE' ? (editItem.id ? 'Edit Rule' : 'New Rule') : (editItem.id ? 'Edit Subcategory' : 'New Subcategory')}
                            </h2>
                            <button onClick={() => setOpenModal(false)} className="text-gray-500 hover:text-white transition-colors">
                                <XMarkIcon className="w-6 h-6" />
                            </button>
                        </div>

                        {/* Modal Body */}
                        <div className="p-6 overflow-y-auto space-y-6 flex-1">
                            {modalType === 'ACCOUNT' && (
                                <div className="space-y-6">
                                    <div>
                                        <h4 className={sectionHeaderClass}>Identification</h4>
                                        <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                                            <div>
                                                <label className={labelClass}>Account Name *</label>
                                                <input type="text" required value={editItem.name || ''} onChange={e => setEditItem({...editItem, name: e.target.value})} className={inputClass} />
                                            </div>
                                            <div>
                                                <label className={labelClass}>Type *</label>
                                                <select value={editItem.account_type || 'CHECKING'} onChange={e => setEditItem({...editItem, account_type: e.target.value})} className={inputClass}>
                                                    {ACCOUNT_TYPES.map(o => <option key={o} value={o}>{o}</option>)}
                                                </select>
                                            </div>
                                        </div>
                                    </div>

                                    <div>
                                        <h4 className={sectionHeaderClass}>QFX Import / Watcher Settings</h4>
                                        <div className="grid grid-cols-1 md:grid-cols-12 gap-4">
                                            <div className="md:col-span-4">
                                                <label className={labelClass}>FID (Bank ID)</label>
                                                <input type="text" value={editItem.fid || ''} onChange={e => setEditItem({...editItem, fid: e.target.value})} className={inputClass} />
                                            </div>
                                            <div className="md:col-span-8">
                                                <label className={labelClass}>Account Number</label>
                                                <input type="text" value={editItem.account_number || ''} onChange={e => setEditItem({...editItem, account_number: e.target.value})} className={inputClass} />
                                                <p className="text-xs text-gray-500 mt-1">As in your bank's QFX/OFX files, so they import here by themselves. Leave it blank if you're not sure: a file the app doesn't recognise waits in the Import Inbox for you to pick its account.</p>
                                            </div>
                                            <div className="md:col-span-6">
                                                <label className={labelClass}>Institution</label>
                                                <input type="text" value={editItem.institution || ''} onChange={e => setEditItem({...editItem, institution: e.target.value})} className={inputClass} />
                                            </div>
                                            <div className="md:col-span-6">
                                                <label className={labelClass}>Owner</label>
                                                <input type="text" value={editItem.owner || ''} onChange={e => setEditItem({...editItem, owner: e.target.value})} className={inputClass} />
                                            </div>
                                        </div>
                                    </div>

                                    <div>
                                        <h4 className={sectionHeaderClass}>Financial Metadata</h4>
                                        <div className="grid grid-cols-1 md:grid-cols-12 gap-4">
                                            <div className="md:col-span-4">
                                                <label className={labelClass}>Tax Status</label>
                                                <select value={editItem.tax_status || 'Taxable'} onChange={e => setEditItem({...editItem, tax_status: e.target.value})} className={inputClass}>
                                                    {TAX_STATUS_TYPES.map(o => <option key={o} value={o}>{o}</option>)}
                                                </select>
                                            </div>
                                            <div className="md:col-span-4">
                                                <label className={labelClass}>Interest %</label>
                                                <input type="number" step="0.01" value={editItem.interest_rate || ''} onChange={e => setEditItem({...editItem, interest_rate: e.target.value})} className={inputClass} />
                                            </div>
                                            
                                            {['CREDIT CARD', 'LOAN'].includes(editItem.account_type || '') && (
                                                <>
                                                    <div className="md:col-span-4">
                                                        <label className={labelClass}>Credit Limit</label>
                                                        <input type="number" value={editItem.credit_limit || ''} onChange={e => setEditItem({...editItem, credit_limit: e.target.value})} className={inputClass} />
                                                    </div>
                                                    <div className="md:col-span-4">
                                                        <label className={labelClass}>Due Day</label>
                                                        <input type="number" value={editItem.due_day || ''} onChange={e => setEditItem({...editItem, due_day: e.target.value})} className={inputClass} />
                                                    </div>
                                                </>
                                            )}
                                            <div className="md:col-span-8">
                                                <label className={labelClass}>Closed Date</label>
                                                <input type="date" value={editItem.closed_date || ''} onChange={e => setEditItem({...editItem, closed_date: e.target.value})} className={inputClass} />
                                            </div>
                                        </div>
                                    </div>

                                    <label className="flex items-start gap-3 bg-gray-800/50 p-4 rounded-xl border border-gray-700 mt-2 cursor-pointer">
                                        <input type="checkbox" className="mt-1" checked={!!editItem.import_amounts_reversed}
                                            onChange={e => setEditItem({ ...editItem, import_amounts_reversed: e.target.checked, reversedChanged: true })} />
                                        <span className="text-sm text-white">
                                            This bank's files show money out as positive
                                            <span className="block text-xs text-gray-400 mt-0.5">
                                                Some card issuers' downloads list purchases as positive amounts. Tick this and imports reverse them.
                                                {editItem.account_id && !editItem.import_sign_confirmed && ' The Import Inbox will also ask you the first time, with an example from the file.'}
                                            </span>
                                        </span>
                                    </label>

                                    {/* Tailwind Toggles */}
                                    <div className="flex flex-wrap gap-6 bg-gray-800/50 p-4 rounded-xl border border-gray-700 mt-2">
                                        <label className="relative inline-flex items-center cursor-pointer">
                                            <input type="checkbox" className="sr-only peer" checked={editItem.show_forecast || false} onChange={e => setEditItem({...editItem, show_forecast: e.target.checked})} />
                                            <div className="w-11 h-6 bg-gray-700 peer-focus:outline-none rounded-full peer peer-checked:after:translate-x-full peer-checked:after:border-white after:content-[''] after:absolute after:top-[2px] after:left-[2px] after:bg-white after:border-gray-300 after:border after:rounded-full after:h-5 after:w-5 after:transition-all peer-checked:bg-blue-600"></div>
                                            <span className="ml-3 text-sm font-medium text-white">Dashboard Forecast</span>
                                        </label>

                                        {editItem.account_id && (
                                            <label className="relative inline-flex items-center cursor-pointer">
                                                <input type="checkbox" className="sr-only peer" checked={editItem.active || false} onChange={e => setEditItem({...editItem, active: e.target.checked})} />
                                                <div className="w-11 h-6 bg-gray-700 peer-focus:outline-none rounded-full peer peer-checked:after:translate-x-full peer-checked:after:border-white after:content-[''] after:absolute after:top-[2px] after:left-[2px] after:bg-white after:border-gray-300 after:border after:rounded-full after:h-5 after:w-5 after:transition-all peer-checked:bg-green-500"></div>
                                                <span className="ml-3 text-sm font-medium text-white">Active Status</span>
                                            </label>
                                        )}
                                    </div>
                                </div>
                            )}

                            {modalType === 'CATEGORY' && (
                                <div className="space-y-4">
                                    <div>
                                        <label className={labelClass}>Category Name</label>
                                        <input type="text" value={editItem.name || ''} onChange={e => setEditItem({...editItem, name: e.target.value})} className={inputClass} />
                                    </div>
                                    <div>
                                        <label className={labelClass}>Type</label>
                                        <select value={editItem.type || 'EXP'} onChange={e => setEditItem({...editItem, type: e.target.value})} className={inputClass}>
                                            <option value="EXP">Expense</option>
                                            <option value="INC">Income</option>
                                            <option value="XFR">Transfer</option>
                                        </select>
                                        <p className="text-xs text-gray-500 mt-1">
                                            Expenses count as spending and Income as income in budgets and Savings. Transfers (money moved
                                            between your own accounts, card payments) count as neither.
                                            {editItem.id && editItem.type !== editItem.originalType && ' Changing it changes how everything already in it is counted.'}
                                        </p>
                                    </div>
                                </div>
                            )}

                            {modalType === 'SUBCAT' && (
                                <div className="space-y-4">
                                    {editItem.id ? (
                                        <div>
                                            <label className={labelClass}>In category</label>
                                            <select value={editItem.category} onChange={e => setEditItem({...editItem, category: Number(e.target.value)})} className={inputClass}>
                                                {categories.map(c => <option key={c.id} value={c.id}>{c.name} ({c.type === 'INC' ? 'Income' : c.type === 'XFR' ? 'Transfer' : 'Expense'})</option>)}
                                            </select>
                                        </div>
                                    ) : (
                                        <div className="bg-blue-900/20 border border-blue-500/30 p-4 rounded-lg text-blue-300 text-sm">
                                            Adding subcategory to: <strong className="text-white">{categories.find(c => c.id === editItem.category)?.name}</strong>
                                        </div>
                                    )}
                                    <div>
                                        <label className={labelClass}>Subcategory Name</label>
                                        <input type="text" autoFocus value={editItem.name || ''} onChange={e => setEditItem({...editItem, name: e.target.value})} className={inputClass} />
                                    </div>
                                    {editItem.id && <p className="text-xs text-gray-500">Transactions, budgets and rules already in it keep up with the new name or category.</p>}
                                    {editItem.role_label && <p className="text-xs text-blue-300">The app uses this for {editItem.role_label}: rename or move it freely, it will still be found.</p>}
                                </div>
                            )}

                            {modalType === 'RULE' && (
                                <div className="space-y-6">
                                    <div>
                                        <label className={labelClass}>Rule Name *</label>
                                        <input type="text" required value={editItem.rule_name || ''} onChange={e => setEditItem({...editItem, rule_name: e.target.value})} className={inputClass} />
                                    </div>

                                    <div>
                                        <div className="flex justify-between items-center mb-2">
                                            <h4 className="text-xs font-bold text-blue-400 uppercase tracking-widest">Conditions</h4>
                                            <div className="flex bg-gray-950 rounded-lg p-1 border border-gray-700">
                                                <button onClick={() => setEditItem({...editItem, logic_operator: 'AND'})} className={`px-3 py-1 rounded text-xs font-bold transition-colors ${(editItem.logic_operator || 'AND') === 'AND' ? 'bg-blue-600 text-white' : 'text-gray-500'}`}>AND</button>
                                                <button onClick={() => setEditItem({...editItem, logic_operator: 'OR'})} className={`px-3 py-1 rounded text-xs font-bold transition-colors ${editItem.logic_operator === 'OR' ? 'bg-orange-600 text-white' : 'text-gray-500'}`}>OR</button>
                                            </div>
                                        </div>
                                        
                                        <div className="bg-gray-800/50 rounded-xl p-4 border border-gray-700 space-y-3">
                                            {editItem.conditions?.map((cond: RuleCondition, index: number) => (
                                                <div key={index} className="flex gap-2 items-center">
                                                    <select value={cond.field} onChange={(e) => updateCondition(index, 'field', e.target.value)} className={`${conditionBoxClass} w-32 shrink-0`}>
                                                        <option value="name">Merchant</option>
                                                        <option value="amount">Amount</option>
                                                        <option value="personal_memo">Memo</option>
                                                        <option value="account">Account Name</option>
                                                    </select>
                                                    <select value={cond.operator} onChange={(e) => updateCondition(index, 'operator', e.target.value)} className={`${conditionBoxClass} w-36 shrink-0`}>
                                                        {cond.field === 'amount' ? (<>
                                                            <option value="exact">Exactly</option>
                                                            <option value="gt">Over</option>
                                                            <option value="lt">Under</option>
                                                        </>) : (<>
                                                            <option value="contains">Contains</option>
                                                            <option value="exact">Exact Match</option>
                                                            <option value="startswith">Starts With</option>
                                                            <option value="endswith">Ends With</option>
                                                        </>)}
                                                    </select>
                                                    <input type="text" placeholder="Value..." value={cond.value} onChange={(e) => updateCondition(index, 'value', e.target.value)} className={`${conditionBoxClass} flex-1 min-w-0`} />
                                                    <button onClick={() => removeCondition(index)} className="text-red-400 hover:text-red-300 p-2 rounded">
                                                        <TrashIcon className="w-5 h-5" />
                                                    </button>
                                                </div>
                                            ))}
                                            <button onClick={addCondition} className="text-sm font-bold text-blue-400 hover:text-blue-300 flex items-center mt-2">
                                                <PlusCircleIcon className="w-5 h-5 mr-1" /> Add Condition
                                            </button>
                                        </div>
                                    </div>

                                    <div>
                                        <h4 className={sectionHeaderClass}>Actions</h4>
                                        <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                                            <div>
                                                <label className={labelClass}>Rename Merchant To</label>
                                                <input type="text" value={editItem.clean_merchant_name || ''} onChange={e => setEditItem({...editItem, clean_merchant_name: e.target.value})} className={inputClass} placeholder="Leave blank to keep original" />
                                            </div>
                                            <div>
                                                <label className={labelClass}>Assign Subcategory *</label>
                                                <select value={editItem.subcategory || ''} onChange={e => setEditItem({...editItem, subcategory: e.target.value})} className={inputClass} required>
                                                    <option value="">Select Subcategory...</option>
                                                    {categories.map((cat) => (
                                                        <optgroup key={cat.id} label={cat.name} className="bg-gray-900 text-gray-400 font-bold">
                                                            {cat.subcategories.map(sub => (
                                                                <option key={sub.id} value={sub.id} className="bg-gray-800 text-white font-normal">{sub.name}</option>
                                                            ))}
                                                        </optgroup>
                                                    ))}
                                                </select>
                                            </div>
                                            <div className="flex items-center pt-5">
                                                <label className="relative inline-flex items-center cursor-pointer">
                                                    <input type="checkbox" className="sr-only peer" checked={editItem.is_active !== false} onChange={e => setEditItem({...editItem, is_active: e.target.checked})} />
                                                    <div className="w-11 h-6 bg-gray-700 peer-focus:outline-none rounded-full peer peer-checked:after:translate-x-full peer-checked:after:border-white after:content-[''] after:absolute after:top-[2px] after:left-[2px] after:bg-white after:border-gray-300 after:border after:rounded-full after:h-5 after:w-5 after:transition-all peer-checked:bg-green-500"></div>
                                                    <span className="ml-3 text-sm font-medium text-white">Rule Active</span>
                                                </label>
                                            </div>
                                        </div>
                                    </div>
                                </div>
                            )}
                        </div>

                        {/* Modal Footer */}
                        <div className="p-6 bg-gray-950 border-t border-gray-800 flex justify-end gap-3 shrink-0">
                            <button onClick={() => setOpenModal(false)} className="px-4 py-2 text-gray-400 font-medium hover:text-white transition-colors">Cancel</button>
                            <button onClick={handleSave} className="px-6 py-2 bg-blue-600 hover:bg-blue-500 text-white font-bold rounded-lg shadow-lg transition-colors">Save Changes</button>
                        </div>
                    </div>
                </div>
            )}
        </div>
    );
};

export default Settings;