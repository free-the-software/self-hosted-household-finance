// Copyright (C) 2026 free-the-software
// SPDX-License-Identifier: AGPL-3.0-or-later
// This file is part of Save More Money, under the GNU AGPL v3 or later: see LICENSE.
import { useEffect, useState } from 'react';
import { ExclamationTriangleIcon, XMarkIcon } from '@heroicons/react/24/outline';
import api from '../api';
import CategoryPicker from './CategoryPicker';
import type { CategoryGroup } from './CategoryAllocation';

// Deleting a category or subcategory (finance/categories.py): says what uses
// it, and where that should go: moved to another subcategory (which is also
// how two are merged) or left uncategorized. Nothing that uses it is lost
// without the user choosing.

interface Usage {
    transactions: number; split_lines: number; rules: number; budget_months: number;
    recurring: number; inbox_rows: number; loans: number;
    role?: string | null; roles?: string[]; subcategories?: number;
    cannot_leave_uncategorized?: string | null;
}
type Choice = 'move' | 'replace' | 'uncategorize';

const LINES: [keyof Usage, string, string][] = [
    ['transactions', 'transaction', 'transactions'], ['split_lines', 'split line', 'split lines'], ['rules', 'rule', 'rules'],
    ['budget_months', 'budget month', 'budget months'], ['recurring', 'recurring bill', 'recurring bills'],
    ['inbox_rows', 'Import Inbox row', 'Import Inbox rows'], ['loans', 'loan setting', 'loan settings'],
];

const CategoryRemove = ({ kind, target, categories, onClose, onDone }: {
    kind: 'category' | 'subcategory';
    target: { id: number; name: string };
    categories: CategoryGroup[];
    onClose: () => void;
    onDone: () => void;
}) => {
    const [usage, setUsage] = useState<Usage | null>(null);
    const [choice, setChoice] = useState<Choice>(kind === 'category' ? 'move' : 'replace');
    const [replacement, setReplacement] = useState<number | null>(null);
    const [moveTo, setMoveTo] = useState('');
    const [error, setError] = useState('');
    const [busy, setBusy] = useState(false);
    const base = kind === 'category' ? `/categories/${target.id}` : `/subcategories/${target.id}`;

    useEffect(() => {
        api.get(`${base}/usage/`).then(res => setUsage(res.data)).catch(() => setError('Could not see what uses it.'));
    }, [base]);

    const used = usage ? LINES.filter(([key]) => Number(usage[key]) > 0) : [];
    const roles = usage ? (usage.roles || (usage.role ? [usage.role] : [])) : [];
    const nothingUsesIt = usage !== null && used.length === 0 && roles.length === 0;
    const emptyCategory = kind === 'category' && usage?.subcategories === 0;
    // The choices offered elsewhere, without the thing being deleted
    const others = kind === 'category'
        ? categories.filter(cat => cat.id !== target.id)
        : categories.map(cat => ({ ...cat, subcategories: cat.subcategories.filter(sub => sub.id !== target.id) }));

    const remove = async () => {
        setBusy(true);
        setError('');
        try {
            if (kind === 'category' && emptyCategory) {
                await api.delete(`${base}/`);
            } else if (kind === 'subcategory') {
                if (choice === 'replace' && !replacement && !nothingUsesIt) { setError('Choose where everything should go.'); setBusy(false); return; }
                await api.post(`${base}/remove/`, { replacement: nothingUsesIt ? null : choice === 'replace' ? replacement : null });
            } else if (nothingUsesIt) {
                await api.post(`${base}/remove/`, { uncategorize: true });     // its unused subcategories go with it
            } else {
                if (choice === 'move' && !moveTo) { setError('Choose a category for its subcategories.'); setBusy(false); return; }
                if (choice === 'replace' && !replacement) { setError('Choose where everything should go.'); setBusy(false); return; }
                await api.post(`${base}/remove/`, choice === 'move' ? { move_to: Number(moveTo) }
                    : choice === 'replace' ? { replacement } : { uncategorize: true });
            }
            onDone();
        } catch (err: any) {
            setError(err.response?.data?.error || 'Could not delete it.');
            setBusy(false);
        }
    };

    const radio = (value: Choice, label: string, disabledReason?: string | null) => (
        <label className={`flex items-start gap-2 text-sm ${disabledReason ? 'text-gray-500' : 'text-gray-200 cursor-pointer'}`}>
            <input type="radio" name="remove-choice" className="mt-1" checked={choice === value} disabled={!!disabledReason}
                onChange={() => setChoice(value)} />
            <span>{label}{disabledReason && <span className="block text-xs">{disabledReason}</span>}</span>
        </label>
    );

    return (
        <div className="fixed inset-0 z-[60] bg-black/80 backdrop-blur-sm flex items-center justify-center p-4" role="dialog" aria-modal="true" aria-labelledby="remove-title">
            <div className="bg-gray-900 border border-gray-700 rounded-2xl shadow-2xl w-full max-w-lg max-h-[90vh] flex flex-col overflow-hidden">
                <div className="p-5 border-b border-gray-800 bg-gray-950 flex justify-between items-center">
                    <h2 id="remove-title" className="text-lg font-bold text-white">Delete {kind === 'category' ? 'the category' : ''} "{target.name}"?</h2>
                    <button onClick={onClose} className="text-gray-500 hover:text-white" aria-label="Close"><XMarkIcon className="w-6 h-6" /></button>
                </div>
                <div className="p-5 space-y-4 overflow-y-auto">
                    {!usage && !error && <p className="text-sm text-gray-400">Checking what uses it...</p>}

                    {usage && (emptyCategory || nothingUsesIt) && (
                        <p className="text-sm text-gray-300">
                            {emptyCategory ? 'It has no subcategories.' : kind === 'category' ? 'Nothing uses its subcategories; they are deleted with it.' : 'Nothing uses it.'}
                            {' '}It can simply be deleted.
                        </p>
                    )}

                    {usage && !emptyCategory && !nothingUsesIt && <>
                        <div className="text-sm text-gray-300">
                            <p className="mb-1">{kind === 'category' ? `Its ${usage.subcategories} subcategor${usage.subcategories === 1 ? 'y is' : 'ies are'} used by:` : 'It is used by:'}</p>
                            <ul className="list-disc list-inside text-gray-400">
                                {used.map(([key, one, many]) => <li key={key}>{Number(usage[key])} {Number(usage[key]) === 1 ? one : many}</li>)}
                                {roles.map(role => <li key={role}>the app, for {role}</li>)}
                            </ul>
                        </div>

                        <div className="space-y-3">
                            {kind === 'category' && <>
                                {radio('move', 'Move its subcategories to another category')}
                                {choice === 'move' && (
                                    <select aria-label="Move the subcategories to" value={moveTo} onChange={e => setMoveTo(e.target.value)}
                                        className="ml-6 w-[calc(100%-1.5rem)] bg-gray-800 border border-gray-700 rounded-lg text-white p-2 text-sm">
                                        <option value="">Choose a category...</option>
                                        {others.map(cat => <option key={cat.id} value={cat.id}>{cat.name}</option>)}
                                    </select>
                                )}
                                {choice === 'move' && <p className="ml-6 text-xs text-gray-500">A subcategory with the same name as one already there is merged into it.</p>}
                            </>}

                            {radio('replace', kind === 'category' ? 'Delete its subcategories, and move everything to one subcategory' : 'Move everything to another subcategory')}
                            {choice === 'replace' && (
                                <div className="ml-6 space-y-1">
                                    <CategoryPicker value={replacement} onChange={setReplacement} categories={others} ariaLabel="Move everything to" />
                                    <p className="text-xs text-gray-500">Budgets are added into its budgets, month by month. This is also how to merge two subcategories.</p>
                                </div>
                            )}

                            {radio('uncategorize', kind === 'category' ? 'Delete its subcategories, and leave everything uncategorized' : 'Leave everything uncategorized',
                                kind === 'subcategory' ? usage.cannot_leave_uncategorized : roles.length ? 'Not possible: the app uses some of them. Choose where they go instead.' : null)}
                            {choice === 'uncategorize' && (Number(usage.rules) > 0 || Number(usage.budget_months) > 0) && (
                                <p className="ml-6 text-xs text-amber-300 flex gap-1">
                                    <ExclamationTriangleIcon className="w-4 h-4 shrink-0" />
                                    Its {usage.rules} rule{usage.rules === 1 ? '' : 's'} and {usage.budget_months} budget month{usage.budget_months === 1 ? '' : 's'} will be deleted.
                                </p>
                            )}
                        </div>
                    </>}
                    {error && <p className="text-sm text-red-400" role="alert">{error}</p>}
                </div>
                <div className="p-4 border-t border-gray-800 bg-gray-950 flex justify-end gap-3">
                    <button onClick={onClose} className="px-4 py-2 text-gray-400 hover:text-white">Cancel</button>
                    <button onClick={remove} disabled={busy || !usage} className="px-5 py-2 bg-red-600 hover:bg-red-500 disabled:opacity-50 text-white font-bold rounded-lg">
                        {busy ? 'Working...' : 'Delete'}
                    </button>
                </div>
            </div>
        </div>
    );
};

export default CategoryRemove;
