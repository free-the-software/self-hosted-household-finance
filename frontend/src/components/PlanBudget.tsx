// Copyright (C) 2026 free-the-software
// SPDX-License-Identifier: AGPL-3.0-or-later
// This file is part of Save More Money, under the GNU AGPL v3 or later: see LICENSE.
import { useEffect, useState } from 'react';
import { XMarkIcon } from '@heroicons/react/24/outline';
import api from '../api';

// Setting up a year's budget from the year before (finance/budget_planning.py):
// from last year's budget or what was actually spent, adjusted and rounded,
// with the totals it comes to shown before anything is saved.

const SOURCES: [string, string, string][] = [
    ['budget', "Last year's budget", 'Each month as it was budgeted.'],
    ['actual_monthly', "Last year's actual, month by month", 'What was really spent and earned each month, so a December bill stays in December. Months not over yet come from the year before.'],
    ['actual_average', "Last year's actual, averaged", 'The average month, the same every month.'],
    ['blank', 'Blank', 'No amounts: fill them in yourself. With "Replace them", clears the year.'],
];
const money = (value: number) => value.toLocaleString('en-US', { style: 'currency', currency: 'USD', maximumFractionDigits: 0 });

interface Preview { income_budget: string; expense_budget: string; savings_budget: string; months_set: number; months_kept: number; }

const PlanBudget = ({ year, onClose, onDone }: { year: number; onClose: () => void; onDone: () => void }) => {
    const [source, setSource] = useState('budget');
    const [percent, setPercent] = useState('0');
    const [rounding, setRounding] = useState('10');
    const [kinds, setKinds] = useState('both');
    const [mode, setMode] = useState('fill');
    const [preview, setPreview] = useState<Preview | null>(null);
    const [error, setError] = useState('');
    const [busy, setBusy] = useState(false);

    const body = () => ({
        year, source, percent: Number(percent) || 0, rounding: Number(rounding),
        kinds: kinds === 'both' ? ['EXP', 'INC'] : [kinds], mode,
    });

    // What it would come to, whenever a choice changes
    useEffect(() => {
        api.post('/budgets/plan/', { ...body(), preview: true })
            .then(res => { setPreview(res.data); setError(''); })
            .catch(err => { setPreview(null); setError(err.response?.data?.error || 'Could not work it out.'); });
    }, [source, percent, rounding, kinds, mode]);

    const create = async () => {
        setBusy(true);
        try {
            await api.post('/budgets/plan/', body());
            onDone();
        } catch (err: any) {
            setError(err.response?.data?.error || 'Could not set up the budget.');
            setBusy(false);
        }
    };

    const label = 'block text-xs font-bold text-gray-400 uppercase mb-1';
    const input = 'w-full bg-gray-800 border border-gray-700 rounded-lg text-white p-2 text-sm';
    return (
        <div className="fixed inset-0 z-50 bg-black/80 backdrop-blur-sm flex items-center justify-center p-4" role="dialog" aria-modal="true" aria-labelledby="plan-title">
            <div className="bg-gray-900 border border-gray-700 rounded-2xl shadow-2xl w-full max-w-lg max-h-[90vh] flex flex-col overflow-hidden">
                <div className="p-5 border-b border-gray-800 bg-gray-950 flex justify-between items-center">
                    <div>
                        <h2 id="plan-title" className="text-lg font-bold text-white">Plan {year}</h2>
                        <p className="text-xs text-gray-400 mt-1">Set up {year}'s budget from {year - 1}.</p>
                    </div>
                    <button onClick={onClose} className="text-gray-500 hover:text-white" aria-label="Close"><XMarkIcon className="w-6 h-6" /></button>
                </div>
                <div className="p-5 space-y-4 overflow-y-auto">
                    <fieldset className="space-y-2">
                        <legend className={label}>Start from</legend>
                        {SOURCES.map(([value, name, hint]) => (
                            <label key={value} className="flex items-start gap-2 text-sm text-gray-200 cursor-pointer">
                                <input type="radio" name="plan-source" className="mt-1" checked={source === value} onChange={() => setSource(value)} />
                                <span>{name}<span className="block text-xs text-gray-500">{hint}</span></span>
                            </label>
                        ))}
                    </fieldset>

                    {source !== 'blank' && (
                        <div className="grid grid-cols-2 gap-3">
                            <div>
                                <label htmlFor="plan-percent" className={label}>Change by (%)</label>
                                <input id="plan-percent" inputMode="decimal" value={percent} onChange={e => setPercent(e.target.value)} className={input} />
                            </div>
                            <div>
                                <label htmlFor="plan-round" className={label}>Round to</label>
                                <select id="plan-round" value={rounding} onChange={e => setRounding(e.target.value)} className={input}>
                                    <option value="0">The cent</option>
                                    <option value="1">$1</option>
                                    <option value="10">$10</option>
                                    <option value="100">$100</option>
                                </select>
                            </div>
                        </div>
                    )}

                    <div className="grid grid-cols-2 gap-3">
                        <div>
                            <label htmlFor="plan-kinds" className={label}>Budget</label>
                            <select id="plan-kinds" value={kinds} onChange={e => setKinds(e.target.value)} className={input}>
                                <option value="both">Income and expenses</option>
                                <option value="EXP">Expenses only</option>
                                <option value="INC">Income only</option>
                            </select>
                        </div>
                        <div>
                            <label htmlFor="plan-mode" className={label}>Months already budgeted</label>
                            <select id="plan-mode" value={mode} onChange={e => setMode(e.target.value)} className={input}>
                                <option value="fill">Keep them</option>
                                <option value="replace">Replace them</option>
                            </select>
                        </div>
                    </div>

                    {preview && (
                        <div className="bg-gray-800/50 border border-gray-700 rounded-xl p-4 text-sm">
                            <p className="text-xs font-bold text-gray-400 uppercase mb-2">{year} would come to</p>
                            <div className="grid grid-cols-3 gap-2 font-mono">
                                <div><span className="block text-[10px] text-gray-500 font-sans uppercase">Income</span>{money(Number(preview.income_budget))}</div>
                                <div><span className="block text-[10px] text-gray-500 font-sans uppercase">Expenses</span>{money(Number(preview.expense_budget))}</div>
                                <div><span className="block text-[10px] text-gray-500 font-sans uppercase">Savings</span>
                                    <span className={Number(preview.savings_budget) >= 0 ? 'text-green-400' : 'text-red-400'}>{money(Number(preview.savings_budget))}</span>
                                </div>
                            </div>
                            <p className="text-xs text-gray-500 mt-2">
                                {preview.months_set} month amount{preview.months_set === 1 ? '' : 's'} to set
                                {preview.months_kept > 0 && `, ${preview.months_kept} already budgeted kept`}.
                            </p>
                        </div>
                    )}
                    {error && <p className="text-sm text-red-400" role="alert">{error}</p>}
                </div>
                <div className="p-4 border-t border-gray-800 bg-gray-950 flex justify-end gap-3">
                    <button onClick={onClose} className="px-4 py-2 text-gray-400 hover:text-white">Cancel</button>
                    <button onClick={create} disabled={busy || !preview || (preview.months_set === 0 && mode !== 'replace')}
                        className="px-5 py-2 bg-blue-600 hover:bg-blue-500 disabled:opacity-50 text-white font-bold rounded-lg">
                        {busy ? 'Setting up...' : `Set up ${year}`}
                    </button>
                </div>
            </div>
        </div>
    );
};

export default PlanBudget;
