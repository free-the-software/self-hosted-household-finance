// Copyright (C) 2026 free-the-software
// SPDX-License-Identifier: AGPL-3.0-or-later
// This file is part of Save More Money, under the GNU AGPL v3 or later: see LICENSE.
import { useEffect, useState } from 'react';
import { XMarkIcon } from '@heroicons/react/24/outline';
import api from '../api';
import type { Holding } from './HoldingForm';

// Recording a loan payment. The app suggests how it splits (interest on the
// actual balance, escrow and PMI from the loan's terms, the rest principal);
// every part can be corrected to match the lender's statement. The payment
// is either one already in the ledger (e.g. imported from checking), which
// gets split and linked, or a new pending one in the chosen account.

interface Candidate { id: number; date: string; description: string; amount: string; account_name: string; }
interface BankAccount { account_id: number; name: string; account_type: string; active: boolean; }

const PARTS: [string, string][] = [
    ['principal', 'Principal (incl. extra)'], ['interest', 'Interest'],
    ['property_tax', 'Property tax escrow'], ['insurance', 'Home insurance escrow'], ['pmi', 'PMI'],
];
const money = (value: number) => value.toLocaleString('en-US', { style: 'currency', currency: 'USD' });
const today = () => new Date().toISOString().slice(0, 10);

const LoanPaymentForm = ({ holding, onClose, onSaved }: { holding: Holding; onClose: () => void; onSaved: () => void }) => {
    const [date, setDate] = useState(today());
    const [extra, setExtra] = useState('');
    const [parts, setParts] = useState<Record<string, string>>({});
    const [owedBefore, setOwedBefore] = useState('');
    const [escrowMode, setEscrowMode] = useState('EXPENSE');
    const [candidates, setCandidates] = useState<Candidate[]>([]);
    const [useExisting, setUseExisting] = useState<number | null>(null);
    const [accounts, setAccounts] = useState<BankAccount[]>([]);
    const [fromAccount, setFromAccount] = useState('');
    const [memo, setMemo] = useState('');
    const [error, setError] = useState('');
    const [saving, setSaving] = useState(false);

    useEffect(() => {
        api.get('/accounts/').then(res => {
            const open = res.data.filter((a: BankAccount) => a.active);
            setAccounts(open);
            const checking = open.find((a: BankAccount) => (a.account_type || '').toUpperCase().includes('CHECKING'));
            if (checking) setFromAccount(String(checking.account_id));
        }).catch(() => undefined);
    }, []);

    // A new suggestion whenever the date or the extra principal changes
    useEffect(() => {
        if (!date) return;
        api.get(`/holdings/${holding.id}/payment-split/`, { params: { date, extra: extra || 0 } })
            .then(res => {
                const d = res.data;
                setParts(Object.fromEntries(PARTS.map(([name]) => [name, d[name]])));
                setOwedBefore(d.owed_before);
                setEscrowMode(d.escrow_mode);
                setCandidates(d.candidates);
                setUseExisting(d.candidates.length ? d.candidates[0].id : null);
                setError('');
            })
            .catch(err => setError(err.response?.data?.error || 'Could not work out the payment.'));
    }, [date, extra]);

    const total = PARTS.reduce((sum, [name]) => sum + (Number(parts[name]) || 0), 0);
    // Imported payments that match the parts' total exactly
    const matching = candidates.filter(c => Math.abs(-Number(c.amount) - total) < 0.005);

    const save = async () => {
        setSaving(true);
        try {
            const body: Record<string, any> = { date, memo, ...parts };
            const chosen = matching.find(c => c.id === useExisting);
            if (chosen) body.transaction_id = chosen.id;
            else body.from_account = fromAccount ? Number(fromAccount) : null;
            await api.post(`/holdings/${holding.id}/payment/`, body);
            onSaved();
        } catch (err: any) {
            setError(err.response?.data?.error || 'Could not record the payment.');
        } finally {
            setSaving(false);
        }
    };

    const input = 'w-full bg-gray-800 border border-gray-700 rounded-lg text-white p-2 text-sm focus:border-blue-500 outline-none';
    const chosenExisting = matching.find(c => c.id === useExisting);

    return (
        <div className="fixed inset-0 bg-black/80 backdrop-blur-sm flex items-center justify-center z-50 p-4">
            <div className="bg-gray-900 border border-gray-700 rounded-2xl shadow-2xl w-full max-w-lg max-h-[90vh] flex flex-col overflow-hidden">
                <div className="p-5 border-b border-gray-800 flex justify-between items-center bg-gray-950">
                    <div>
                        <h3 className="text-lg font-bold text-white">Record payment: {holding.name}</h3>
                        {owedBefore && <p className="text-xs text-gray-400 mt-1">Owed before this payment: <span className="font-mono">{money(Number(owedBefore))}</span></p>}
                    </div>
                    <button onClick={onClose} className="text-gray-500 hover:text-white" aria-label="Close"><XMarkIcon className="w-6 h-6" /></button>
                </div>
                <div className="p-5 overflow-y-auto space-y-4">
                    <div className="grid grid-cols-2 gap-3">
                        <div>
                            <label htmlFor="p-date" className="block text-xs font-bold text-gray-400 uppercase mb-1">Date</label>
                            <input id="p-date" type="date" value={date} onChange={e => setDate(e.target.value)} className={input} />
                        </div>
                        <div>
                            <label htmlFor="p-extra" className="block text-xs font-bold text-gray-400 uppercase mb-1">Extra principal</label>
                            <input id="p-extra" inputMode="decimal" placeholder="0.00" value={extra} onChange={e => setExtra(e.target.value)} className={input} />
                        </div>
                    </div>

                    <div className="bg-gray-800/50 border border-gray-700 rounded-xl p-4">
                        <p className="text-xs text-gray-400 mb-3">
                            Suggested from the actual balance and the loan's terms. Correct any part to match your statement.
                            {escrowMode === 'ACCOUNT' ? ' The escrow parts go into the escrow account.' : ' The escrow parts are expensed now.'}
                        </p>
                        <div className="space-y-2">
                            {PARTS.map(([name, label]) => (
                                <div key={name} className="flex items-center gap-3">
                                    <label htmlFor={`p-${name}`} className="flex-1 text-sm text-gray-300">{label}</label>
                                    <input id={`p-${name}`} inputMode="decimal" value={parts[name] ?? ''} onChange={e => setParts({ ...parts, [name]: e.target.value })}
                                        className="w-32 bg-gray-900 border border-gray-700 rounded-lg text-white p-1.5 text-sm text-right font-mono" />
                                </div>
                            ))}
                            <div className="flex items-center gap-3 pt-2 border-t border-gray-700">
                                <span className="flex-1 text-sm font-bold text-white">Total payment</span>
                                <span className="w-32 text-right font-mono font-bold text-white pr-1.5">{money(total)}</span>
                            </div>
                        </div>
                    </div>

                    <div>
                        <p className="text-xs font-bold text-gray-400 uppercase mb-2">The payment</p>
                        {matching.map(c => (
                            <label key={c.id} className="flex items-start gap-2 text-sm text-gray-300 mb-2 cursor-pointer">
                                <input type="radio" name="p-source" checked={useExisting === c.id} onChange={() => setUseExisting(c.id)} className="mt-1" />
                                <span>Already imported: <span className="text-white">{c.description}</span>, {c.account_name}, {c.date} <span className="font-mono">({money(-Number(c.amount))})</span></span>
                            </label>
                        ))}
                        <label className="flex items-start gap-2 text-sm text-gray-300 cursor-pointer">
                            <input type="radio" name="p-source" checked={!chosenExisting} onChange={() => setUseExisting(null)} className="mt-1" />
                            <span className="flex-1">
                                New payment from
                                <select aria-label="From account" value={fromAccount} onChange={e => setFromAccount(e.target.value)} disabled={!!chosenExisting}
                                    className="ml-2 bg-gray-800 border border-gray-700 rounded-lg text-white p-1 text-sm disabled:opacity-40">
                                    <option value="">Choose an account</option>
                                    {accounts.map(a => <option key={a.account_id} value={a.account_id}>{a.name}</option>)}
                                </select>
                                <span className="block text-xs text-gray-500 mt-1">It's added as pending, and matched when your bank file is imported.</span>
                            </span>
                        </label>
                        {candidates.length > matching.length && (
                            <p className="text-xs text-amber-300 mt-2">
                                {candidates.length - matching.length} imported payment{candidates.length - matching.length > 1 ? 's' : ''} near this date
                                {' '}for the suggested amount no longer match the total; adjust the parts if one of them is this payment.
                            </p>
                        )}
                    </div>

                    <div>
                        <label htmlFor="p-memo" className="block text-xs font-bold text-gray-400 uppercase mb-1">Memo</label>
                        <input id="p-memo" value={memo} onChange={e => setMemo(e.target.value)} className={input} />
                    </div>
                    {error && <p className="text-sm text-red-400">{error}</p>}
                </div>
                <div className="p-4 border-t border-gray-800 bg-gray-950 flex justify-end gap-3">
                    <button onClick={onClose} className="px-4 py-2 text-gray-400 hover:text-white">Cancel</button>
                    <button onClick={save} disabled={saving || total <= 0} className="px-5 py-2 bg-blue-600 hover:bg-blue-500 disabled:opacity-50 text-white font-bold rounded-lg">
                        {saving ? 'Saving...' : `Record ${money(total)}`}
                    </button>
                </div>
            </div>
        </div>
    );
};

export default LoanPaymentForm;
