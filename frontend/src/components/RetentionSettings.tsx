// Copyright (C) 2026 free-the-software
// SPDX-License-Identifier: AGPL-3.0-or-later
// This file is part of Save More Money, under the GNU AGPL v3 or later: see LICENSE.
import { useEffect, useState } from 'react';
import { ShieldCheckIcon, TrashIcon, ArchiveBoxIcon } from '@heroicons/react/24/outline';
import api from '../api';
import { askConfirm } from './Dialogs';

interface Usage {
    files: number;
    bytes: number;
    oldest: string | null;
    expired?: number;
}

interface Category {
    key: string;
    label: string;
    description: string;
    deletes: string;
    keeps: string;
    retention_days: number | null;
    usage: Usage;
}

interface RetentionState {
    can_edit: boolean;
    categories: Category[];
    last_run: { ran_at: string; trigger: string; files_deleted: number; bytes_freed: number } | null;
}

interface PlanCategory {
    key: string;
    label: string;
    retention_days: number | null;
    files: number;
    bytes: number;
    oldest: string | null;
    newest: string | null;
    sample: { name: string; bytes: number; date: string }[];
}

interface Plan {
    categories: PlanCategory[];
    files: number;
    bytes: number;
    errors?: string[];
}

// null = keep forever
type Policies = Record<string, number | null>;

const PRESETS: { label: string; days: number | null }[] = [
    { label: 'Keep forever', days: null },
    { label: '14 days', days: 14 },
    { label: '30 days', days: 30 },
    { label: '90 days', days: 90 },
    { label: '6 months', days: 180 },
    { label: '1 year', days: 365 },
    { label: '2 years', days: 730 },
    { label: '3 years', days: 1095 },
    { label: '7 years', days: 2555 },
    { label: '10 years', days: 3650 },
];

const formatBytes = (bytes: number) => {
    if (bytes < 1024) return `${bytes} B`;
    const units = ['KB', 'MB', 'GB', 'TB'];
    let value = bytes / 1024;
    let unit = 0;
    while (value >= 1024 && unit < units.length - 1) { value /= 1024; unit++; }
    return `${value.toFixed(value >= 10 ? 0 : 1)} ${units[unit]}`;
};

const formatPeriod = (days: number | null) => {
    if (days === null) return 'keep forever';
    const preset = PRESETS.find(p => p.days === days);
    return preset ? preset.label : `${days} days`;
};

const plural = (count: number, word: string) => `${count} ${word}${count === 1 ? '' : 's'}`;

const RetentionSettings = () => {
    const [state, setState] = useState<RetentionState | null>(null);
    const [draft, setDraft] = useState<Policies>({});
    // Categories where the user picked "Custom…" and is typing a number of days
    const [custom, setCustom] = useState<Record<string, boolean>>({});
    const [plan, setPlan] = useState<{ title: string; data: Plan; deleted: boolean } | null>(null);
    const [busy, setBusy] = useState(false);
    const [error, setError] = useState('');

    const adopt = (data: RetentionState) => {
        setState(data);
        setDraft(Object.fromEntries(data.categories.map(c => [c.key, c.retention_days])));
        setCustom(Object.fromEntries(data.categories.map(c => [
            c.key, c.retention_days !== null && !PRESETS.some(p => p.days === c.retention_days)
        ])));
    };

    useEffect(() => {
        api.get('/retention/')
            .then(res => adopt(res.data))
            .catch(() => setError('Could not load the retention settings.'));
    }, []);

    if (error && !state) return <div className="text-red-400">{error}</div>;
    if (!state) return <div className="text-gray-500">Loading...</div>;

    const saved: Policies = Object.fromEntries(state.categories.map(c => [c.key, c.retention_days]));
    const hasChanges = state.categories.some(c => draft[c.key] !== saved[c.key]);
    const invalid = state.categories.some(c => draft[c.key] !== null && !(Number.isInteger(draft[c.key]) && draft[c.key]! >= 1));

    const fail = (err: any, fallback: string) => setError(err.response?.data?.error || fallback);

    // Asks the server what the given policies would delete. Deletes nothing.
    const fetchPlan = async (policies?: Policies): Promise<Plan> => {
        const res = await api.post('/retention/preview/', policies ? { policies } : {});
        return res.data;
    };

    const describe = (data: Plan) => data.categories
        .filter(c => c.files > 0)
        .map(c => `  • ${c.label}: ${plural(c.files, 'file')} (${formatBytes(c.bytes)})`)
        .join('\n');

    const handlePreview = async () => {
        setBusy(true); setError('');
        try {
            const data = await fetchPlan(draft);
            setPlan({ title: hasChanges ? 'Preview of the settings shown above (not saved yet)' : 'Preview of your saved policies', data, deleted: false });
        } catch (err) { fail(err, 'Could not build the preview.'); }
        finally { setBusy(false); }
    };

    const handleSave = async () => {
        setBusy(true); setError('');
        try {
            // Show exactly what these settings will remove before committing to them
            const data = await fetchPlan(draft);
            const message = data.files > 0
                ? `The nightly clean-up will then permanently delete files past their period. ` +
                  `Right now that is ${plural(data.files, 'file')} (${formatBytes(data.bytes)}):\n${describe(data)}\n\n` +
                  `Receipt records and transactions are never deleted. Deleted files cannot be recovered.`
                : `Nothing is old enough to be deleted yet. ` +
                  `Files will be deleted automatically, each night, once they pass the period you set.`;
            if (!(await askConfirm({ title: 'Save these retention policies?', message, confirmLabel: 'Save', danger: data.files > 0 }))) return;

            const res = await api.put('/retention/', { policies: draft });
            adopt(res.data);
            setPlan(null);
        } catch (err) { fail(err, 'Could not save the policies.'); }
        finally { setBusy(false); }
    };

    const handleRunNow = async () => {
        setBusy(true); setError('');
        try {
            const data = await fetchPlan();
            if (data.files === 0) {
                setPlan({ title: 'Nothing to clean up: no file has passed its retention period', data, deleted: false });
                return;
            }
            if (!(await askConfirm({
                title: `Permanently delete ${plural(data.files, 'file')} (${formatBytes(data.bytes)}) now?`,
                message: `${describe(data)}\n\nReceipt records and transactions are kept. Deleted files cannot be recovered.`,
                confirmLabel: 'Delete files', danger: true,
            }))) return;

            const res = await api.post('/retention/run/');
            setPlan({ title: 'Clean-up finished', data: res.data, deleted: true });
            const fresh = await api.get('/retention/');
            adopt(fresh.data);
        } catch (err) { fail(err, 'The clean-up failed.'); }
        finally { setBusy(false); }
    };

    return (
        <div className="animate-in fade-in duration-300 space-y-4">

            {/* --- WHAT THIS DOES --- */}
            <div className="bg-gray-900 rounded-xl border border-gray-800 p-5 flex items-start gap-4">
                <ShieldCheckIcon className="w-6 h-6 text-green-400 flex-shrink-0 mt-0.5" />
                <div className="text-sm text-gray-300 space-y-1">
                    <p className="font-bold text-white">Receipts, archives and bank files are kept forever unless you set a period below.</p>
                    <p>
                        Each kind of stored file has its own policy. A file is deleted only after it is older than its period,
                        by the clean-up that runs every night. Your transactions and receipt records are never deleted by these policies.
                    </p>
                    <p>
                        Backup files are the one exception: old backups are removed after 30 days by default, so they do not fill the disk.
                        You can change that below as well.
                    </p>
                    {!state.can_edit && (
                        <p className="text-yellow-400">You can view these settings. Only an administrator can change them or delete files.</p>
                    )}
                </div>
            </div>

            {error && <div className="bg-red-900/30 border border-red-800 text-red-300 text-sm rounded-lg p-3">{error}</div>}

            {/* --- ONE CARD PER KIND OF FILE --- */}
            {state.categories.map(category => {
                const days = draft[category.key];
                const isCustom = custom[category.key];
                const changed = days !== saved[category.key];

                return (
                    <div key={category.key} className={`bg-gray-900 rounded-xl border p-5 shadow-xl ${changed ? 'border-blue-600' : 'border-gray-800'}`}>
                        <div className="flex flex-col lg:flex-row lg:items-start lg:justify-between gap-4">
                            <div className="flex-1 min-w-0">
                                <h3 className="text-lg font-bold text-white flex items-center">
                                    <ArchiveBoxIcon className="w-5 h-5 mr-2 text-gray-500" />
                                    {category.label}
                                    {changed && <span className="ml-3 text-[10px] font-black uppercase tracking-wider text-blue-400">Not saved</span>}
                                </h3>
                                <p className="text-sm text-gray-400 mt-1">{category.description}</p>

                                <dl className="mt-3 text-sm space-y-1.5">
                                    <div className="flex gap-3">
                                        <dt className="w-16 flex-shrink-0 text-xs font-bold uppercase tracking-wider text-red-400 pt-0.5">Deletes</dt>
                                        <dd className="text-gray-300">{category.deletes}</dd>
                                    </div>
                                    <div className="flex gap-3">
                                        <dt className="w-16 flex-shrink-0 text-xs font-bold uppercase tracking-wider text-green-400 pt-0.5">Keeps</dt>
                                        <dd className="text-gray-300">{category.keeps}</dd>
                                    </div>
                                </dl>

                                <p className="mt-3 text-xs text-gray-500">
                                    Stored now: <span className="text-gray-300">{plural(category.usage.files, 'file')}</span>
                                    {' · '}<span className="text-gray-300">{formatBytes(category.usage.bytes)}</span>
                                    {category.usage.oldest && <>{' · oldest from '}<span className="text-gray-300">{category.usage.oldest}</span></>}
                                    {!!category.usage.expired && <>{' · '}<span className="text-gray-300">{plural(category.usage.expired, 'image')} already expired</span></>}
                                </p>
                            </div>

                            <div className="lg:w-56 flex-shrink-0">
                                <label htmlFor={`keep-${category.key}`} className="block text-xs font-bold text-gray-400 mb-1">Keep for</label>
                                <select
                                    id={`keep-${category.key}`}
                                    disabled={!state.can_edit || busy}
                                    value={isCustom ? 'custom' : String(days)}
                                    onChange={e => {
                                        const value = e.target.value;
                                        if (value === 'custom') {
                                            setCustom({ ...custom, [category.key]: true });
                                            setDraft({ ...draft, [category.key]: days ?? 365 });
                                        } else {
                                            setCustom({ ...custom, [category.key]: false });
                                            setDraft({ ...draft, [category.key]: value === 'null' ? null : Number(value) });
                                        }
                                    }}
                                    className="w-full bg-gray-800 border border-gray-700 text-white text-sm rounded-lg p-2.5 focus:ring-blue-500 focus:border-blue-500 disabled:opacity-60"
                                >
                                    {PRESETS.map(preset => (
                                        <option key={String(preset.days)} value={String(preset.days)}>{preset.label}</option>
                                    ))}
                                    <option value="custom">Custom…</option>
                                </select>

                                {isCustom && (
                                    <div className="mt-2 flex items-center gap-2">
                                        <input
                                            type="number" min={1} step={1}
                                            aria-label={`Days to keep ${category.label}`}
                                            disabled={!state.can_edit || busy}
                                            value={days === null || Number.isNaN(days) ? '' : days}
                                            onChange={e => setDraft({ ...draft, [category.key]: e.target.value === '' ? NaN : Number(e.target.value) })}
                                            className="w-full bg-gray-800 border border-gray-700 text-white text-sm rounded-lg p-2.5"
                                        />
                                        <span className="text-sm text-gray-400">days</span>
                                    </div>
                                )}

                                <p className={`mt-2 text-xs ${days === null ? 'text-green-400' : 'text-yellow-400'}`}>
                                    {days === null ? 'Never deleted.' : 'Deleted automatically once older than this.'}
                                </p>
                            </div>
                        </div>
                    </div>
                );
            })}

            {/* --- ACTIONS --- */}
            <div className="flex flex-wrap items-center gap-3">
                <button onClick={handlePreview} disabled={busy || invalid}
                    className="bg-gray-700 hover:bg-gray-600 disabled:opacity-50 text-white px-4 py-2 rounded-lg text-sm font-bold transition-colors">
                    Preview what would be deleted
                </button>
                {state.can_edit && (
                    <>
                        <button onClick={handleSave} disabled={busy || invalid || !hasChanges}
                            className="bg-blue-600 hover:bg-blue-500 disabled:opacity-50 text-white px-4 py-2 rounded-lg text-sm font-bold transition-colors shadow-lg">
                            Save policies
                        </button>
                        <button onClick={handleRunNow} disabled={busy || hasChanges}
                            title={hasChanges ? 'Save or undo your changes first' : undefined}
                            className="bg-red-700 hover:bg-red-600 disabled:opacity-50 text-white px-4 py-2 rounded-lg text-sm font-bold transition-colors flex items-center">
                            <TrashIcon className="w-4 h-4 mr-2" /> Run clean-up now
                        </button>
                    </>
                )}
                {hasChanges && <span className="text-xs text-blue-400">You have unsaved changes. Nothing changes until you save.</span>}
                {invalid && <span className="text-xs text-red-400">A custom period must be a whole number of days, 1 or more.</span>}
            </div>

            <p className="text-xs text-gray-500">
                {state.last_run
                    ? `Last clean-up that deleted anything: ${new Date(state.last_run.ran_at).toLocaleString()} ` +
                      `(${state.last_run.trigger}), ${plural(state.last_run.files_deleted, 'file')}, ${formatBytes(state.last_run.bytes_freed)} freed.`
                    : 'No clean-up has deleted anything yet.'}
            </p>

            {/* --- PREVIEW / RESULT --- */}
            {plan && (
                <div className="bg-gray-900 rounded-xl border border-gray-800 overflow-hidden shadow-xl">
                    <div className="px-5 py-4 border-b border-gray-800 flex items-center justify-between">
                        <div>
                            <h3 className="font-bold text-white">{plan.title}</h3>
                            <p className="text-sm text-gray-400">
                                {plan.deleted ? 'Deleted' : 'Would delete'} {plural(plan.data.files, 'file')}, {formatBytes(plan.data.bytes)} in total.
                                {!plan.deleted && ' Nothing has been deleted.'}
                            </p>
                        </div>
                        <button onClick={() => setPlan(null)} className="text-sm text-gray-400 hover:text-white">Close</button>
                    </div>

                    {plan.data.errors && plan.data.errors.length > 0 && (
                        <div className="px-5 py-3 bg-red-900/30 text-red-300 text-sm">
                            Some files could not be deleted: {plan.data.errors.join('; ')}
                        </div>
                    )}

                    {plan.data.categories.map(category => (
                        <div key={category.key} className="px-5 py-4 border-b border-gray-800 last:border-b-0">
                            <div className="flex flex-wrap items-baseline justify-between gap-2">
                                <span className="font-medium text-white">{category.label}</span>
                                <span className="text-sm text-gray-400">
                                    {category.retention_days === null
                                        ? 'Keep forever: nothing deleted'
                                        : `${formatPeriod(category.retention_days)}: ${plural(category.files, 'file')}, ${formatBytes(category.bytes)}` +
                                          (category.oldest && !plan.deleted ? ` (dated ${category.oldest} to ${category.newest})` : '')}
                                </span>
                            </div>

                            {!plan.deleted && category.sample.length > 0 && (
                                <table className="mt-3 w-full text-left text-xs text-gray-400">
                                    <thead className="uppercase text-gray-500">
                                        <tr><th className="py-1 pr-4">File</th><th className="py-1 pr-4">Date</th><th className="py-1 text-right">Size</th></tr>
                                    </thead>
                                    <tbody>
                                        {category.sample.map((file, idx) => (
                                            <tr key={idx} className="border-t border-gray-800">
                                                <td className="py-1 pr-4 font-mono text-gray-300 break-all">{file.name}</td>
                                                <td className="py-1 pr-4 whitespace-nowrap">{file.date}</td>
                                                <td className="py-1 text-right whitespace-nowrap">{formatBytes(file.bytes)}</td>
                                            </tr>
                                        ))}
                                    </tbody>
                                </table>
                            )}
                            {!plan.deleted && category.files > category.sample.length && (
                                <p className="mt-2 text-xs text-gray-500">…and {category.files - category.sample.length} more. The oldest are listed above.</p>
                            )}
                        </div>
                    ))}
                </div>
            )}
        </div>
    );
};

export default RetentionSettings;
