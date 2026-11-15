// Copyright (C) 2026 free-the-software
// SPDX-License-Identifier: AGPL-3.0-or-later
// This file is part of Save More Money, under the GNU AGPL v3 or later: see LICENSE.
import { useEffect, useState } from 'react';
import api from '../api';
import { askConfirm, notify } from './Dialogs';

// Settings → Storage: the everyday settings an admin can change
// (finance/system.py). Each takes effect at once; an empty value, or
// "Restore defaults", goes back to the default from .env or the app.

type Key = 'update_checks' | 'disk_warn_gb' | 'idle_minutes' | 'upload_max_mb';
type Values = Record<Key, any>;
interface State { current: Values; defaults: Values; limits: Record<string, [number, number]>; can_edit: boolean; repo: string; }

const NUMBERS: [Exclude<Key, 'update_checks'>, string, string, string][] = [
    ['disk_warn_gb', 'Warn when free disk is below', 'GB', '0 never warns'],
    ['idle_minutes', 'Sign out after idle for', 'minutes', '0 never signs out'],
    ['upload_max_mb', 'Largest file upload', 'MB', ''],
];

const SystemSettingsPanel = () => {
    const [state, setState] = useState<State | null>(null);
    const [draft, setDraft] = useState<Values | null>(null);
    const [busy, setBusy] = useState(false);

    const adopt = (data: State) => { setState(data); setDraft({ ...data.current }); };
    useEffect(() => { api.get('/system/settings/').then(res => adopt(res.data)).catch(() => undefined); }, []);
    if (!state || !draft) return null;

    const send = async (body: Partial<Values>, done: string) => {
        setBusy(true);
        try {
            adopt((await api.put('/system/settings/', body)).data);
            notify(done, 'info');
        } catch (err: any) {
            notify(err.response?.data?.error || 'Could not save the settings.');
        } finally {
            setBusy(false);
        }
    };
    const save = () => send(draft, 'Settings saved. They apply straight away.');
    const restore = async () => {
        if (!(await askConfirm({ title: 'Restore the default settings?', message: 'Update checks on, a 5 GB disk warning, sign-out after 10 idle minutes and 25 MB uploads, or whatever .env sets for them.', confirmLabel: 'Restore defaults' }))) return;
        send({ update_checks: null, disk_warn_gb: null, idle_minutes: null, upload_max_mb: null }, 'Defaults restored.');
    };
    const changed = (Object.keys(draft) as Key[]).some(key => String(draft[key]) !== String(state.current[key]));
    const edit = state.can_edit;
    const input = 'w-24 bg-gray-800 border border-gray-700 rounded-lg text-white p-1.5 text-sm text-right disabled:opacity-50';

    return (
        <div className="bg-gray-900 rounded-xl border border-gray-800 p-5 mb-6 space-y-4">
            <div className="flex justify-between items-center">
                <h2 className="text-sm font-bold text-gray-300">System settings</h2>
                {!edit && <span className="text-xs text-gray-500">Only an admin can change these.</span>}
            </div>

            <label className="flex items-start gap-3 text-sm text-gray-200 cursor-pointer">
                <input type="checkbox" className="mt-1" disabled={!edit} checked={!!draft.update_checks}
                    onChange={e => setDraft({ ...draft, update_checks: e.target.checked })} />
                <span>
                    Check for new releases
                    <span className="block text-xs text-gray-500">
                        {state.repo
                            ? 'Once a day the server asks GitHub for the latest release, and Settings says when one is out. Nothing is installed by the app.'
                            : 'There is no release to check yet: this starts working once the app has a public home on GitHub.'}
                        {' '}Default: on.
                    </span>
                </span>
            </label>

            {NUMBERS.map(([key, label, unit, hint]) => {
                const [low, high] = state.limits[key];
                return (
                    <div key={key} className="flex flex-wrap items-center gap-3 text-sm">
                        <label htmlFor={`sys-${key}`} className="flex-1 min-w-[14rem] text-gray-200">
                            {label}
                            <span className="block text-xs text-gray-500">Default: {state.defaults[key]} {unit}{hint && `; ${hint}`}.</span>
                        </label>
                        <input id={`sys-${key}`} type="number" min={low} max={high} disabled={!edit} value={draft[key] ?? ''}
                            onChange={e => setDraft({ ...draft, [key]: e.target.value === '' ? null : Number(e.target.value) })} className={input} />
                        <span className="w-16 text-gray-400">{unit}</span>
                    </div>
                );
            })}

            {edit && (
                <div className="flex justify-end gap-3 pt-2 border-t border-gray-800">
                    <button onClick={restore} disabled={busy} className="px-4 py-2 text-sm text-gray-300 hover:text-white">Restore defaults</button>
                    <button onClick={save} disabled={busy || !changed} className="px-5 py-2 text-sm font-bold text-white bg-blue-600 hover:bg-blue-500 disabled:opacity-50 rounded-lg">
                        {busy ? 'Saving...' : 'Save'}
                    </button>
                </div>
            )}
        </div>
    );
};

export default SystemSettingsPanel;
