// Copyright (C) 2026 free-the-software
// SPDX-License-Identifier: AGPL-3.0-or-later
// This file is part of Save More Money, under the GNU AGPL v3 or later: see LICENSE.
import { useEffect, useState } from 'react';
import { 
    ExclamationTriangleIcon, 
    ArrowRightCircleIcon, 
    PlusCircleIcon, 
    XMarkIcon 
} from '@heroicons/react/24/outline';
import api from '../api';
import { notify } from './Dialogs';

interface ParkedAccount {
    unregistered_account_id: string;
    tx_count: number;
}

interface Account {
    account_id: number;
    name: string;
}

interface ParkedAccountsBannerProps {
    onResolved: () => void; 
}

const ACCOUNT_TYPES = ['CHECKING', 'SAVINGS', 'CREDIT CARD', 'LOAN', 'INVESTMENT', 'CASH'];
const TAX_STATUS_TYPES = ['Taxable', 'Tax-Deferred', 'Tax-Free'];

const initialAccountState = {
    name: '',
    account_type: 'CREDIT CARD',
    fid: '',
    account_number: '',
    institution: '',
    owner: '',
    tax_status: 'Taxable',
    interest_rate: '',
    credit_limit: '',
    due_day: '',
    show_forecast: false,
    active: true
};

const ParkedAccountsBanner = ({ onResolved }: ParkedAccountsBannerProps) => {
    const [parkedAccounts, setParkedAccounts] = useState<ParkedAccount[]>([]);
    const [accounts, setAccounts] = useState<Account[]>([]);
    const [selections, setSelections] = useState<Record<string, number>>({});
    const [loading, setLoading] = useState(false);

    // --- Modal State ---
    const [isModalOpen, setIsModalOpen] = useState(false);
    const [activeUnregisteredId, setActiveUnregisteredId] = useState('');
    const [newAccount, setNewAccount] = useState(initialAccountState);

    const fetchData = async () => {
        try {
            const parkedRes = await api.get('/import/parked/');
            setParkedAccounts(parkedRes.data);

            const accountsRes = await api.get('/accounts/');
            setAccounts(accountsRes.data);
        } catch (err) {
            console.error("Failed to fetch parked accounts", err);
        }
    };

    useEffect(() => {
        fetchData();
    }, []);

    // 1. Map to an existing account
    const handleResolve = async (unregisteredId: string) => {
        const targetAccountId = selections[unregisteredId];
        
        if (!targetAccountId) {
            notify("Please select an account to map these transactions to.");
            return;
        }

        setLoading(true);
        try {
            await api.post('/import/resolve/', {
                unregistered_id: unregisteredId,
                real_account_id: targetAccountId
            });
            
            await fetchData();
            onResolved(); 
        } catch (err: any) {
            notify(`Error mapping account: ${err.response?.data?.error || err.message}`);
        } finally {
            setLoading(false);
        }
    };

    // 2. Create a new account inline and auto-map it
    const handleCreateAndMap = async () => {
        if (!newAccount.name) {
            notify("Give the account a name.");
            return;
        }
        
        setLoading(true);
        try {
            const payload = {
                ...newAccount,
                interest_rate: newAccount.interest_rate || null,
                credit_limit: newAccount.credit_limit || null,
                due_day: newAccount.due_day || null,
            };

            // A. Create the account
            const accRes = await api.post('/accounts/', payload);
            const newAccountId = accRes.data.account_id || accRes.data.id;

            // B. Immediately map the parked transactions to the new account
            await api.post('/import/resolve/', {
                unregistered_id: activeUnregisteredId,
                real_account_id: newAccountId
            });

            // C. Clean up
            setIsModalOpen(false);
            setNewAccount(initialAccountState);
            await fetchData();
            onResolved();
        } catch (err: any) {
            notify(`Error creating account: ${JSON.stringify(err.response?.data || err.message)}`);
        } finally {
            setLoading(false);
        }
    };

    const openCreateModal = (unregisteredId: string) => {
        setActiveUnregisteredId(unregisteredId);
        // A QFX/OFX file's account number; a CSV's file name is only remembered, not an account number
        setNewAccount({ ...initialAccountState, account_number: unregisteredId.startsWith('csv:') ? '' : unregisteredId });
        setIsModalOpen(true);
    };

    if (parkedAccounts.length === 0) return null;

    return (
        <div className="mb-6 bg-red-900/20 border border-red-500/50 rounded-xl p-6 shadow-lg animate-in fade-in slide-in-from-top-4 duration-300">
            <div className="flex items-center mb-4">
                <ExclamationTriangleIcon className="w-6 h-6 text-red-400 mr-2 stroke-[2]" />
                <h2 className="text-lg font-bold text-red-400">Action Required: Unregistered Accounts Detected</h2>
            </div>
            <p className="text-sm text-gray-300 mb-4">
                These files are for accounts the app doesn't recognise yet. Pick the account each belongs to, or create it.
                The choice is remembered, so the next file like it goes straight in.
            </p>

            <div className="space-y-3">
                {parkedAccounts.map((pa) => (
                    <div key={pa.unregistered_account_id} className="flex flex-wrap md:flex-nowrap items-center justify-between bg-gray-900/50 p-4 rounded-lg border border-red-500/30 gap-4">
                        
                        <div className="flex-1">
                            <span className="text-xs font-black text-gray-500 uppercase tracking-widest block mb-1">
                                {pa.unregistered_account_id.startsWith('csv:') ? 'CSV file named' : 'Account number in the file'}
                            </span>
                            <span className="font-mono text-red-300 font-bold text-lg">{pa.unregistered_account_id.replace(/^csv:/, '')}</span>
                            <span className="text-xs text-gray-400 ml-3">({pa.tx_count} transactions waiting)</span>
                        </div>

                        <div className="flex-1 flex items-center gap-2 w-full md:w-auto">
                            <select 
                                className="flex-1 bg-gray-800 border-gray-700 text-white text-sm rounded-lg p-2.5 focus:ring-red-500 focus:border-red-500"
                                value={selections[pa.unregistered_account_id] || ''}
                                onChange={(e) => setSelections({
                                    ...selections, 
                                    [pa.unregistered_account_id]: Number(e.target.value)
                                })}
                            >
                                <option value="">Select Account to map to...</option>
                                {accounts.map(acc => (
                                    <option key={acc.account_id} value={acc.account_id}>{acc.name}</option>
                                ))}
                            </select>
                            
                            <button 
                                onClick={() => handleResolve(pa.unregistered_account_id)}
                                disabled={loading || !selections[pa.unregistered_account_id]}
                                className="bg-red-600 hover:bg-red-500 text-white px-4 py-2.5 rounded-lg text-sm font-bold transition-colors flex items-center disabled:opacity-50 disabled:cursor-not-allowed whitespace-nowrap"
                            >
                                Map <ArrowRightCircleIcon className="w-4 h-4 ml-1" />
                            </button>

                            <span className="text-gray-600 text-sm mx-1">or</span>

                            <button 
                                onClick={() => openCreateModal(pa.unregistered_account_id)}
                                className="bg-gray-800 hover:bg-gray-700 border border-gray-700 text-white px-4 py-2.5 rounded-lg text-sm font-bold transition-colors flex items-center whitespace-nowrap shadow-lg"
                            >
                                <PlusCircleIcon className="w-4 h-4 mr-1 text-green-400" /> New Account
                            </button>
                        </div>
                    </div>
                ))}
            </div>

            {/* --- COMPREHENSIVE CREATE ACCOUNT MODAL --- */}
            {isModalOpen && (
                <div className="fixed inset-0 bg-black/80 backdrop-blur-sm flex items-center justify-center z-[100] p-4">
                    <div className="bg-gray-900 border border-gray-700 rounded-2xl shadow-2xl w-full max-w-3xl overflow-hidden flex flex-col max-h-[90vh] animate-in zoom-in-95 duration-200">
                        <div className="p-6 border-b border-gray-800 flex justify-between items-center bg-gray-950 shrink-0">
                            <h3 className="text-lg font-bold text-white">Create & Map New Account</h3>
                            <button onClick={() => setIsModalOpen(false)} className="text-gray-500 hover:text-white">
                                <XMarkIcon className="w-6 h-6" />
                            </button>
                        </div>
                        
                        <div className="p-6 overflow-y-auto space-y-6">
                            {/* --- Identification --- */}
                            <div>
                                <h4 className="text-xs font-bold text-blue-400 uppercase tracking-widest mb-3 border-b border-gray-800 pb-1">Identification</h4>
                                <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                                    <div>
                                        <label className="block text-xs font-bold text-gray-400 mb-1">Account Name *</label>
                                        <input type="text" required value={newAccount.name} onChange={(e) => setNewAccount({...newAccount, name: e.target.value})} className="w-full bg-gray-800 border-gray-700 rounded-lg text-white p-2 text-sm" />
                                    </div>
                                    <div>
                                        <label className="block text-xs font-bold text-gray-400 mb-1">Type *</label>
                                        <select value={newAccount.account_type} onChange={(e) => setNewAccount({...newAccount, account_type: e.target.value})} className="w-full bg-gray-800 border-gray-700 rounded-lg text-white p-2 text-sm">
                                            {ACCOUNT_TYPES.map(o => <option key={o} value={o}>{o}</option>)}
                                        </select>
                                    </div>
                                </div>
                            </div>

                            {/* --- QFX Settings --- */}
                            <div>
                                <h4 className="text-xs font-bold text-blue-400 uppercase tracking-widest mb-3 border-b border-gray-800 pb-1">QFX Import / Watcher Settings</h4>
                                <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                                    <div>
                                        <label className="block text-xs font-bold text-gray-400 mb-1">FID (Bank ID)</label>
                                        <input type="text" required value={newAccount.fid} onChange={(e) => setNewAccount({...newAccount, fid: e.target.value})} className="w-full bg-gray-800 border-gray-700 rounded-lg text-white p-2 text-sm" />
                                    </div>
                                    <div>
                                        <label className="block text-xs font-bold text-gray-400 mb-1">Account Number</label>
                                        <input type="text" required value={newAccount.account_number} onChange={(e) => setNewAccount({...newAccount, account_number: e.target.value})} className="w-full bg-blue-900/20 border-blue-500/50 rounded-lg text-blue-300 font-mono p-2 text-sm" />
                                    </div>
                                    <div>
                                        <label className="block text-xs font-bold text-gray-400 mb-1">Institution</label>
                                        <input type="text" required value={newAccount.institution} onChange={(e) => setNewAccount({...newAccount, institution: e.target.value})} className="w-full bg-gray-800 border-gray-700 rounded-lg text-white p-2 text-sm" />
                                    </div>
                                    <div>
                                        <label className="block text-xs font-bold text-gray-400 mb-1">Owner</label>
                                        <input type="text" value={newAccount.owner} onChange={(e) => setNewAccount({...newAccount, owner: e.target.value})} className="w-full bg-gray-800 border-gray-700 rounded-lg text-white p-2 text-sm" />
                                    </div>
                                </div>
                            </div>

                            {/* --- Financial Metadata --- */}
                            <div>
                                <h4 className="text-xs font-bold text-blue-400 uppercase tracking-widest mb-3 border-b border-gray-800 pb-1">Financial Metadata</h4>
                                <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
                                    <div>
                                        <label className="block text-xs font-bold text-gray-400 mb-1">Tax Status</label>
                                        <select value={newAccount.tax_status} onChange={(e) => setNewAccount({...newAccount, tax_status: e.target.value})} className="w-full bg-gray-800 border-gray-700 rounded-lg text-white p-2 text-sm">
                                            {TAX_STATUS_TYPES.map(o => <option key={o} value={o}>{o}</option>)}
                                        </select>
                                    </div>
                                    <div>
                                        <label className="block text-xs font-bold text-gray-400 mb-1">Interest %</label>
                                        <input type="number" step="0.01" value={newAccount.interest_rate} onChange={(e) => setNewAccount({...newAccount, interest_rate: e.target.value})} className="w-full bg-gray-800 border-gray-700 rounded-lg text-white p-2 text-sm" />
                                    </div>
                                    
                                    {['CREDIT CARD', 'LOAN'].includes(newAccount.account_type) && (
                                        <>
                                            <div>
                                                <label className="block text-xs font-bold text-gray-400 mb-1">Credit Limit</label>
                                                <input type="number" value={newAccount.credit_limit} onChange={(e) => setNewAccount({...newAccount, credit_limit: e.target.value})} className="w-full bg-gray-800 border-gray-700 rounded-lg text-white p-2 text-sm" />
                                            </div>
                                            <div>
                                                <label className="block text-xs font-bold text-gray-400 mb-1">Due Day</label>
                                                <input type="number" value={newAccount.due_day} onChange={(e) => setNewAccount({...newAccount, due_day: e.target.value})} className="w-full bg-gray-800 border-gray-700 rounded-lg text-white p-2 text-sm" />
                                            </div>
                                        </>
                                    )}
                                </div>
                            </div>

                            {/* --- Toggles --- */}
                            <div className="flex gap-6 bg-gray-800/50 p-4 rounded-xl border border-gray-700">
                                <label className="flex items-center cursor-pointer">
                                    <input type="checkbox" checked={newAccount.show_forecast} onChange={(e) => setNewAccount({...newAccount, show_forecast: e.target.checked})} className="w-4 h-4 text-blue-600 bg-gray-900 border-gray-600 rounded focus:ring-blue-600" />
                                    <span className="ml-2 text-sm font-medium text-white">Dashboard Forecast</span>
                                </label>
                                <label className="flex items-center cursor-pointer">
                                    <input type="checkbox" checked={newAccount.active} onChange={(e) => setNewAccount({...newAccount, active: e.target.checked})} className="w-4 h-4 text-green-600 bg-gray-900 border-gray-600 rounded focus:ring-green-600" />
                                    <span className="ml-2 text-sm font-medium text-white">Active Status</span>
                                </label>
                            </div>
                        </div>

                        <div className="p-6 bg-gray-950 border-t border-gray-800 flex justify-end gap-3 shrink-0">
                            <button onClick={() => setIsModalOpen(false)} className="px-4 py-2 text-gray-400 font-medium hover:text-white">Cancel</button>
                            <button 
                                onClick={handleCreateAndMap} 
                                disabled={loading}
                                className="px-6 py-2 bg-green-600 hover:bg-green-500 text-white font-bold rounded-lg transition-colors flex items-center shadow-lg disabled:opacity-50"
                            >
                                {loading ? 'Saving...' : 'Create & Import'}
                            </button>
                        </div>
                    </div>
                </div>
            )}
        </div>
    );
};

export default ParkedAccountsBanner;