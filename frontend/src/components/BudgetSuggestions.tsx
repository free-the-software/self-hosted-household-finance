// Copyright (C) 2026 free-the-software
// SPDX-License-Identifier: AGPL-3.0-or-later
// This file is part of Save More Money, under the GNU AGPL v3 or later: see LICENSE.
import { Fragment, useEffect, useMemo, useState } from 'react';
import { XMarkIcon } from '@heroicons/react/24/outline';
import api from '../api';
import { notify } from './Dialogs';

// Budget to Actual → Plan {year} → Review: the year's budget, category by
// category, to check and change before anything is saved. Either suggested
// from history with reasons (finance/budget_suggestions.py), or a quick plan
// worked out in the Plan window (plan: finance/budget_planning.py).

interface OneOff { id: string; date: string; description: string; amount: string; }
interface Row {
    subcategory: number; name: string; category: string; kind: 'EXP' | 'INC';
    history: { label: string; total: string }[];
    suggestion: string; months: string[]; reasons: string[]; one_offs: OneOff[]; existing_budget: string;
}
interface Result { year: number; blocks: string[]; rows: Row[]; }
// take: keep this category; addBack: one-offs added back in; months: the
// twelve amounts as edited (null: as suggested); total and shape: the year's
// total while it's being typed, and the months it's spread like
interface Choice { take: boolean; addBack: string[]; months: string[] | null; total: string | null; shape: number[] | null; }
const FRESH: Choice = { take: true, addBack: [], months: null, total: null, shape: null };
export interface QuickPlan {
    source: string; label: string; income_percent: number; expense_percent: number;
    rounding: number; kinds: string[]; mode: 'fill' | 'replace';
}

const money = (value: number) => value.toLocaleString('en-US', { style: 'currency', currency: 'USD', maximumFractionDigits: 0 });
const cents = (value: number) => Math.round(value * 100) / 100;

const MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
const asAmount = (text: string) => { const n = Number.parseFloat(text); return Number.isFinite(n) && n > 0 ? n : 0; };

// The twelve months a row would set: as edited, or the suggestion plus any
// one-offs added back (spread over the year)
const monthsFor = (row: Row, choice: Choice): number[] => {
    if (choice.months) return choice.months.map(asAmount).map(cents);
    const added = row.one_offs.filter(o => choice.addBack.includes(o.id)).reduce((sum, o) => sum + Number(o.amount), 0);
    return row.months.map(m => cents(Number(m) + added / 12));
};

// A year's total spread across twelve months in a given shape (evenly if it has none), to the cent
const spread = (total: number, shape: number[]) => {
    const sum = shape.reduce((a, b) => a + b, 0);
    const months = shape.map(m => cents(sum > 0 ? total * m / sum : total / 12));
    months[11] = cents(months[11] + total - months.reduce((a, b) => a + b, 0));    // any rounding left over
    return months;
};

const BudgetSuggestions = ({ year, plan, onClose, onDone }: { year: number; plan?: QuickPlan; onClose: () => void; onDone: () => void }) => {
    const [years, setYears] = useState(3);
    const [recent, setRecent] = useState(true);
    const [yearly, setYearly] = useState<'as_paid' | 'spread'>('as_paid');
    const [rounding, setRounding] = useState(10);
    const [result, setResult] = useState<Result | null>(null);
    const [error, setError] = useState('');
    const [loading, setLoading] = useState(true);
    const [choices, setChoices] = useState<Record<number, Choice>>({});
    const [mode, setMode] = useState<'fill' | 'replace'>(plan?.mode ?? 'fill');
    const [saving, setSaving] = useState(false);

    useEffect(() => {
        setLoading(true);
        setError('');
        const request = plan
            ? api.post('/budgets/plan/', { year, source: plan.source, income_percent: plan.income_percent, expense_percent: plan.expense_percent,
                                          rounding: plan.rounding, kinds: plan.kinds, mode: 'replace', preview: true })
                .then(res => ({ ...res, data: { year, blocks: [String(year - 1)], rows: res.data.rows } }))
            : api.get('/budgets/suggestions/', { params: { year, years, recent: recent ? 1 : 0, yearly, rounding } });
        request
            .then(res => {
                setResult(res.data);
                // Keep what was already ticked or typed when an option changes
                setChoices(current => Object.fromEntries(res.data.rows.map((r: Row) =>
                    [r.subcategory, current[r.subcategory] ?? FRESH])));
            })
            .catch(err => { setResult(null); setError(err.response?.data?.error || 'Could not work out suggestions.'); })
            .finally(() => setLoading(false));
    }, [year, years, recent, yearly, rounding, plan]);

    const change = (id: number, update: Partial<Choice>) => setChoices(current => ({ ...current, [id]: { ...(current[id] ?? FRESH), ...update } }));
    const editMonth = (row: Row, choice: Choice, index: number, text: string) => {
        const months = (choice.months ?? monthsFor(row, choice).map(m => m.toFixed(2))).slice();
        months[index] = text;
        change(row.subcategory, { months });
    };
    const startTotal = (row: Row, choice: Choice) => change(row.subcategory, { shape: monthsFor(row, choice), total: null });
    const editTotal = (row: Row, choice: Choice, text: string) => {
        const shape = choice.shape ?? monthsFor(row, choice);
        change(row.subcategory, { total: text, shape, months: spread(asAmount(text), shape).map(m => m.toFixed(2)) });
    };
    const toggleOneOff = (row: Row, choice: Choice, one: OneOff, on: boolean) => {
        const addBack = on ? [...choice.addBack, one.id] : choice.addBack.filter(id => id !== one.id);
        // Already edited: the one-off is added to (or taken from) each month
        const months = choice.months && choice.months.map(m => Math.max(0, asAmount(m) + (on ? 1 : -1) * Number(one.amount) / 12).toFixed(2));
        change(row.subcategory, { addBack, months });
    };

    const totals = useMemo(() => {
        const sum = { income: 0, expenses: 0 };
        for (const row of result?.rows ?? []) {
            const choice = choices[row.subcategory];
            if (!choice?.take) continue;
            const yearTotal = monthsFor(row, choice).reduce((a, b) => a + b, 0);
            if (row.kind === 'INC') sum.income += yearTotal; else sum.expenses += yearTotal;
        }
        return sum;
    }, [result, choices]);

    const apply = async () => {
        if (!result) return;
        const rows = result.rows.filter(r => choices[r.subcategory]?.take)
            .map(r => ({ subcategory: r.subcategory, months: monthsFor(r, choices[r.subcategory]).map(m => m.toFixed(2)) }));
        if (!rows.length) { notify('Tick at least one category to apply.'); return; }
        setSaving(true);
        try {
            const res = await api.post('/budgets/apply-suggestions/', { year, mode, rows });
            notify(`${year}'s budget is set: ${res.data.months_set} month amounts set${res.data.months_kept ? `, ${res.data.months_kept} kept` : ''}.`, 'info');
            onDone();
        } catch (err: any) {
            notify(err.response?.data?.error || 'Could not apply the budget.');
        } finally {
            setSaving(false);
        }
    };

    const control = 'bg-gray-800 border border-gray-700 rounded-lg text-white p-1.5 text-sm';
    const sections: [Row['kind'], string][] = [['INC', 'Income'], ['EXP', 'Expenses']];
    const savings = totals.income - totals.expenses;

    return (
        <div className="fixed inset-0 z-50 bg-black/80 backdrop-blur-sm flex items-center justify-center p-2 sm:p-4" role="dialog" aria-modal="true" aria-labelledby="suggest-title">
            <div className="bg-gray-900 border border-gray-700 rounded-2xl shadow-2xl w-full max-w-[110rem] max-h-[95vh] flex flex-col overflow-hidden">
                <div className="p-5 border-b border-gray-800 bg-gray-950 flex justify-between items-start gap-4">
                    <div>
                        <h2 id="suggest-title" className="text-lg font-bold text-white">{plan ? `Review ${year}'s budget` : `Suggested budget for ${year}`}</h2>
                        <p className="text-xs text-gray-400 mt-1">
                            {plan ? `From ${plan.label}.` : `Worked out from what you actually spent and earned${result ? ` (${result.blocks.join(', ')})` : ''}.`} Tick what to keep, change any amount, then apply. Nothing is saved until then.
                        </p>
                    </div>
                    <button onClick={onClose} className="text-gray-500 hover:text-white" aria-label="Close"><XMarkIcon className="w-6 h-6" /></button>
                </div>

                {!plan && <div className="px-5 py-3 border-b border-gray-800 flex flex-wrap gap-x-6 gap-y-2 items-center text-sm text-gray-300">
                    <label className="flex items-center gap-2">History
                        <select value={years} onChange={e => setYears(Number(e.target.value))} className={control}>
                            <option value={1}>1 year</option><option value={2}>2 years</option><option value={3}>3 years</option>
                        </select>
                    </label>
                    <label className="flex items-center gap-2 cursor-pointer">
                        <input type="checkbox" checked={recent} onChange={e => setRecent(e.target.checked)} disabled={years === 1} />
                        Recent years count more
                    </label>
                    <label className="flex items-center gap-2">Yearly bills
                        <select value={yearly} onChange={e => setYearly(e.target.value as 'as_paid' | 'spread')} className={control}>
                            <option value="as_paid">In the month they're paid</option>
                            <option value="spread">A twelfth each month</option>
                        </select>
                    </label>
                    <label className="flex items-center gap-2">Round each month to
                        <select value={rounding} onChange={e => setRounding(Number(e.target.value))} className={control}>
                            <option value={0}>nearest 0.01</option><option value={1}>$1</option><option value={10}>$10</option><option value={100}>$100</option>
                        </select>
                    </label>
                </div>}

                <div className="flex-1 overflow-auto">
                    {loading ? <p className="p-5 text-gray-400">Working it out...</p> : error ? (
                        <p className="p-5 text-amber-300">{error}</p>
                    ) : result && (
                        <table className="w-full text-sm">
                            <thead className="sticky top-0 z-10 bg-gray-900 text-xs uppercase text-gray-500">
                                <tr>
                                    <th className="p-2 w-8"><span className="sr-only">Keep</span></th>
                                    <th className="p-2 text-left min-w-[14rem]">Category</th>
                                    <th className="p-2 text-right whitespace-nowrap">{plan ? `${year - 1} actual` : 'Last 12 months'}</th>
                                    {MONTHS.map(m => <th key={m} className="p-1 text-right">{m}</th>)}
                                    <th className="p-2 text-right whitespace-nowrap">{year} total</th>
                                </tr>
                            </thead>
                            <tbody>
                                {sections.map(([kind, title]) => {
                                    const rows = result.rows.filter(r => r.kind === kind);
                                    if (!rows.length) return null;
                                    return (
                                        <Fragment key={kind}>
                                            <tr><td colSpan={16} className="px-2 pt-4 pb-1 text-xs font-bold uppercase text-gray-500">{title}</td></tr>
                                            {rows.map(row => {
                                                const choice = choices[row.subcategory] ?? FRESH;
                                                const months = monthsFor(row, choice);
                                                const yearTotal = months.reduce((a, b) => a + b, 0);
                                                const last = Number(row.history[0]?.total ?? 0);
                                                const cell = 'w-20 bg-gray-800 border border-gray-700 rounded-md text-white px-1.5 py-1 text-right font-mono text-xs';
                                                return (
                                                    <tr key={row.subcategory} className={`border-t border-gray-800 align-top ${choice.take ? '' : 'opacity-40'}`}>
                                                        <td className="p-2">
                                                            <input type="checkbox" checked={choice.take} onChange={e => change(row.subcategory, { take: e.target.checked })}
                                                                aria-label={`Keep ${row.name}`} />
                                                        </td>
                                                        <td className="p-2">
                                                            <div className="text-white">{row.name}</div>
                                                            <div className="text-xs text-gray-500">{row.category}</div>
                                                            {Number(row.existing_budget) > 0 && (
                                                                <div className="text-xs text-amber-300/80 mt-1">Already budgeted: {money(Number(row.existing_budget))}</div>
                                                            )}
                                                            <div className="text-xs text-gray-400 mt-1 space-y-0.5">
                                                                {row.reasons.map(reason => <div key={reason}>{reason}</div>)}
                                                                {row.one_offs.map(one => (
                                                                    <label key={one.id} className="flex items-center gap-2 text-gray-300 cursor-pointer">
                                                                        <input type="checkbox" checked={choice.addBack.includes(one.id)}
                                                                            onChange={e => toggleOneOff(row, choice, one, e.target.checked)} />
                                                                        Add back {money(Number(one.amount))} ({one.description || 'no description'}, {one.date})
                                                                    </label>
                                                                ))}
                                                                {choice.months && (
                                                                    <button type="button" onClick={() => change(row.subcategory, { months: null, total: null, shape: null })}
                                                                        className="text-blue-400 hover:text-blue-300 underline">reset to the suggestion</button>
                                                                )}
                                                            </div>
                                                        </td>
                                                        <td className="p-2 text-right font-mono text-gray-300 whitespace-nowrap">
                                                            {money(last)}
                                                            <div className="text-xs text-gray-500">{money(last / 12)}/mo</div>
                                                        </td>
                                                        {months.map((amount, index) => (
                                                            <td key={MONTHS[index]} className="p-1">
                                                                <input type="number" min="0" step="0.01" aria-label={`${row.name}, ${MONTHS[index]} ${year}`}
                                                                    value={choice.months ? choice.months[index] : String(amount)}
                                                                    onChange={e => editMonth(row, choice, index, e.target.value)}
                                                                    className={cell} />
                                                            </td>
                                                        ))}
                                                        <td className="p-2 text-right">
                                                            <input type="number" min="0" step="1" aria-label={`${row.name}, ${year} total`}
                                                                value={choice.total ?? String(cents(yearTotal))}
                                                                onFocus={() => startTotal(row, choice)}
                                                                onChange={e => editTotal(row, choice, e.target.value)}
                                                                onBlur={() => change(row.subcategory, { total: null, shape: null })}
                                                                className="w-28 bg-gray-800 border border-gray-700 rounded-md text-white px-1.5 py-1 text-right font-mono text-xs font-bold" />
                                                        </td>
                                                    </tr>
                                                );
                                            })}
                                        </Fragment>
                                    );
                                })}
                            </tbody>
                        </table>
                    )}
                </div>

                <div className="p-4 border-t border-gray-800 bg-gray-950 flex flex-wrap gap-4 items-center justify-between">
                    <div className="flex flex-wrap gap-x-6 gap-y-1 text-sm">
                        <span className="text-gray-400">Income <span className="font-mono text-green-400">{money(totals.income)}</span></span>
                        <span className="text-gray-400">Expenses <span className="font-mono text-red-300">{money(totals.expenses)}</span></span>
                        <span className="text-gray-400">Left to save <span className={`font-mono font-bold ${savings >= 0 ? 'text-green-400' : 'text-red-400'}`}>{money(savings)}</span></span>
                    </div>
                    <div className="flex flex-wrap gap-3 items-center">
                        <select value={mode} onChange={e => setMode(e.target.value as 'fill' | 'replace')} className={control} aria-label="Months already budgeted">
                            <option value="fill">Keep months already budgeted</option>
                            <option value="replace">Replace these categories' {year} budgets</option>
                        </select>
                        <button onClick={onClose} className="px-4 py-2 text-gray-300 hover:text-white">Cancel</button>
                        <button onClick={apply} disabled={saving || loading || !result}
                            className="px-5 py-2 bg-blue-600 hover:bg-blue-500 disabled:opacity-50 text-white font-bold rounded-lg">
                            {saving ? 'Applying...' : `Apply to ${year}`}
                        </button>
                    </div>
                </div>
            </div>
        </div>
    );
};

export default BudgetSuggestions;
