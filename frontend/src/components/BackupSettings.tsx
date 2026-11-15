// Copyright (C) 2026 free-the-software
// SPDX-License-Identifier: AGPL-3.0-or-later
// This file is part of Save More Money, under the GNU AGPL v3 or later: see LICENSE.
import { useEffect, useRef, useState } from 'react';
import {
    ShieldCheckIcon, ShieldExclamationIcon, LockClosedIcon, LockOpenIcon,
    ArrowDownTrayIcon, TrashIcon, KeyIcon, CircleStackIcon, PhotoIcon,
} from '@heroicons/react/24/outline';
import api from '../api';
import { askConfirm } from './Dialogs';

type Kind = 'db' | 'full';
type Frequency = 'off' | 'daily' | 'weekly' | 'monthly';

interface Schedule {
    frequency: Frequency;
    hour: number;
    weekday: number;   // 0 = Monday
    monthday: number;  // 1-28
}

interface BackupRow {
    id: number;
    kind: Kind;
    status: 'running' | 'ok' | 'failed';
    trigger: string;
    started_at: string;
    finished_at: string | null;
    filename: string;
    size: number;
    encrypted: boolean;
    error: string;
}

interface BackupState {
    can_edit: boolean;
    health: { state: 'setup' | 'off' | 'ok' | 'failed' | 'overdue'; message: string };
    folder: string;
    timezone: string;
    server_time: string;
    encryption: { enabled: boolean; disabled: boolean; public_key: string };
    schedule: Record<Kind, Schedule>;
    backups: BackupRow[];
}

const KINDS: { key: Kind; label: string; contains: string }[] = [
    { key: 'db', label: 'Database backup', contains: 'Every transaction, account, budget, rule and receipt record. Small and quick.' },
    { key: 'full', label: 'Full backup', contains: 'The database plus every receipt image, in one file. Larger and slower.' },
];
const WEEKDAYS = ['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday', 'Sunday'];

const formatBytes = (bytes: number) => {
    if (bytes < 1024) return `${bytes} B`;
    const units = ['KB', 'MB', 'GB', 'TB'];
    let value = bytes / 1024;
    let unit = 0;
    while (value >= 1024 && unit < units.length - 1) { value /= 1024; unit++; }
    return `${value.toFixed(value >= 10 ? 0 : 1)} ${units[unit]}`;
};

const selectClass = "bg-gray-800 border border-gray-700 text-white text-sm rounded-lg p-2 focus:ring-blue-500 focus:border-blue-500 disabled:opacity-60";
const cardClass = "bg-gray-900 rounded-xl border border-gray-800 p-5 shadow-xl";

const BackupSettings = () => {
    const [state, setState] = useState<BackupState | null>(null);
    const [draft, setDraft] = useState<Record<Kind, Schedule> | null>(null);
    // Set only straight after a key is generated; this is the one time it is shown
    const [privateKey, setPrivateKey] = useState('');
    const [keySaved, setKeySaved] = useState(false);
    const [busy, setBusy] = useState(false);
    const [error, setError] = useState('');
    const draftTouched = useRef(false);

    const adopt = (data: BackupState) => {
        setState(data);
        // Keep the user's unsaved schedule edits when a background refresh arrives
        if (!draftTouched.current) setDraft(data.schedule);
    };

    // background: a refresh the page makes by itself, which must not keep an
    // idle session alive
    const load = (background = false) => api.get('/backups/', background ? { headers: { 'X-Activity': 'background' } } : undefined)
        .then(res => adopt(res.data));

    useEffect(() => {
        load().catch(() => setError('Could not load the backup settings.'));
    }, []);

    // While a backup is running, refresh until it finishes
    const running = !!state?.backups.some(b => b.status === 'running');
    useEffect(() => {
        if (!running) return;
        const timer = setInterval(() => { load(true).catch(() => undefined); }, 3000);
        return () => clearInterval(timer);
    }, [running]);

    if (error && !state) return <div className="text-red-400">{error}</div>;
    if (!state || !draft) return <div className="text-gray-500">Loading...</div>;

    const fail = (err: any, fallback: string) => setError(err.response?.data?.error || fallback);
    const ready = state.encryption.enabled || state.encryption.disabled;
    const scheduleChanged = JSON.stringify(draft) !== JSON.stringify(state.schedule);

    const act = async (work: () => Promise<void>, fallback: string) => {
        setBusy(true); setError('');
        try { await work(); } catch (err) { fail(err, fallback); } finally { setBusy(false); }
    };

    const handleGenerateKey = async () => {
        if (state.encryption.enabled && !(await askConfirm({
            title: 'Replace the encryption key?',
            message: 'New backups will need the NEW private key. ' +
                'Backups you already have can still only be opened with the OLD private key, so keep both.',
            confirmLabel: 'Replace the key', danger: true,
        }))) return;
        act(async () => {
            const res = await api.post('/backups/encryption/', { action: 'generate' });
            setPrivateKey(res.data.private_key);
            setKeySaved(false);
            adopt(res.data);
        }, 'Could not set up encryption.');
    };

    const handleDisableEncryption = async () => {
        if (!(await askConfirm({
            title: 'Turn backup encryption off?',
            message: 'Backups will be written as plain files. Anyone who gets a copy of one ' +
                'can read your entire financial history. Only do this if the place you keep backups is itself protected.',
            confirmLabel: 'Turn it off', danger: true,
        }))) return;
        act(async () => {
            const res = await api.post('/backups/encryption/', { action: 'disable', confirm: true });
            adopt(res.data);
        }, 'Could not turn encryption off.');
    };

    const handleSaveSchedule = () => act(async () => {
        const res = await api.put('/backups/schedule/', { schedule: draft });
        draftTouched.current = false;
        adopt(res.data);
    }, 'Could not save the schedule.');

    const handleBackupNow = (kind: Kind) => act(async () => {
        await api.post('/backups/run/', { kind });
        await load();
    }, 'Could not start the backup.');

    const handleDelete = async (backup: BackupRow) => {
        if (!(await askConfirm({
            title: 'Delete this backup permanently?',
            message: `${backup.filename || 'Failed backup record'}\n\nThis cannot be undone.`,
            confirmLabel: 'Delete', danger: true,
        }))) return;
        act(async () => {
            const res = await api.delete(`/backups/${backup.id}/`);
            adopt(res.data);
        }, 'Could not delete the backup.');
    };

    const editSchedule = (kind: Kind, change: Partial<Schedule>) => {
        draftTouched.current = true;
        setDraft({ ...draft, [kind]: { ...draft[kind], ...change } });
    };

    const copyKey = () => navigator.clipboard?.writeText(privateKey).catch(() => undefined);
    const downloadKey = () => {
        const blob = new Blob([`# Finance backup key. Keep this file private and safe.\n# public key: ${state.encryption.public_key}\n${privateKey}\n`], { type: 'text/plain' });
        const link = document.createElement('a');
        link.href = URL.createObjectURL(blob);
        link.download = 'finance-backup-key.txt';
        link.click();
        URL.revokeObjectURL(link.href);
    };

    const healthStyle = {
        ok: 'border-green-800 text-green-300',
        off: 'border-gray-700 text-gray-300',
        setup: 'border-yellow-700 text-yellow-300',
        overdue: 'border-red-800 text-red-300',
        failed: 'border-red-800 text-red-300',
    }[state.health.state];

    return (
        <div className="animate-in fade-in duration-300 space-y-4">

            {/* --- OVERALL STATUS --- */}
            <div className={`bg-gray-900 rounded-xl border p-5 flex items-start gap-4 ${healthStyle}`}>
                {state.health.state === 'ok'
                    ? <ShieldCheckIcon className="w-6 h-6 flex-shrink-0 mt-0.5" />
                    : <ShieldExclamationIcon className="w-6 h-6 flex-shrink-0 mt-0.5" />}
                <div className="text-sm space-y-1">
                    <p className="font-bold text-white">{state.health.message}</p>
                    <p className="text-gray-400">
                        A backup is one file you can restore everything from. Backups are written to a folder on the server;
                        copy them somewhere else as well, because a backup on the same disk does not survive that disk failing.
                    </p>
                    {!state.can_edit && (
                        <p className="text-yellow-400">You can view these settings. Only an administrator can change them or make, download or delete backups.</p>
                    )}
                </div>
            </div>

            {error && <div className="bg-red-900/30 border border-red-800 text-red-300 text-sm rounded-lg p-3">{error}</div>}

            {/* --- THE PRIVATE KEY, SHOWN ONCE --- */}
            {privateKey && (
                <div className="bg-yellow-900/20 border border-yellow-600 rounded-xl p-5 space-y-3">
                    <h3 className="text-lg font-bold text-white flex items-center">
                        <KeyIcon className="w-5 h-5 mr-2 text-yellow-400" /> Save your private key now
                    </h3>
                    <p className="text-sm text-yellow-100">
                        This key is the only thing that can open your encrypted backups. It is shown this one time and is not stored
                        on the server. If you lose it, your backups cannot be opened by anyone, including you.
                        Keep it in a password manager, and not in the same place as the backups.
                    </p>
                    <textarea
                        readOnly value={privateKey} rows={2} aria-label="Private key"
                        onFocus={e => e.target.select()}
                        className="w-full bg-black border border-gray-700 text-green-300 font-mono text-xs rounded-lg p-3"
                    />
                    <div className="flex flex-wrap items-center gap-3">
                        <button onClick={copyKey} className="bg-gray-700 hover:bg-gray-600 text-white px-3 py-2 rounded-lg text-sm font-bold">Copy</button>
                        <button onClick={downloadKey} className="bg-gray-700 hover:bg-gray-600 text-white px-3 py-2 rounded-lg text-sm font-bold">Download as a file</button>
                        <label className="flex items-center text-sm text-gray-200 ml-auto">
                            <input type="checkbox" checked={keySaved} onChange={e => setKeySaved(e.target.checked)} className="mr-2" />
                            I have saved this key somewhere safe
                        </label>
                        <button onClick={() => setPrivateKey('')} disabled={!keySaved}
                            className="bg-blue-600 hover:bg-blue-500 disabled:opacity-50 text-white px-4 py-2 rounded-lg text-sm font-bold">
                            Done
                        </button>
                    </div>
                </div>
            )}

            {/* --- ENCRYPTION --- */}
            <div className={cardClass}>
                <h3 className="text-lg font-bold text-white flex items-center">
                    {state.encryption.enabled
                        ? <LockClosedIcon className="w-5 h-5 mr-2 text-green-400" />
                        : <LockOpenIcon className="w-5 h-5 mr-2 text-yellow-400" />}
                    Encryption
                </h3>

                {state.encryption.enabled && (
                    <>
                        <p className="text-sm text-gray-300 mt-2">
                            <span className="text-green-400 font-bold">On.</span> Every backup is encrypted before it is written.
                            A backup file is unreadable without your private key, even to someone with access to this server.
                        </p>
                        <p className="text-xs text-gray-500 mt-2 break-all">
                            Public key (safe to share; it can only encrypt): <span className="font-mono text-gray-400">{state.encryption.public_key}</span>
                        </p>
                    </>
                )}
                {!state.encryption.enabled && state.encryption.disabled && (
                    <p className="text-sm text-yellow-300 mt-2">
                        <span className="font-bold">Off, by choice.</span> Backups are written as plain files. Anyone who gets a copy can read all of your data.
                    </p>
                )}
                {!ready && (
                    <p className="text-sm text-gray-300 mt-2">
                        Not set up yet. Backups start once you set up encryption. You will be given a private key to keep;
                        the server keeps only the half that can encrypt.
                    </p>
                )}

                {state.can_edit && (
                    <div className="flex flex-wrap items-center gap-3 mt-4">
                        <button onClick={handleGenerateKey} disabled={busy}
                            className="bg-blue-600 hover:bg-blue-500 disabled:opacity-50 text-white px-4 py-2 rounded-lg text-sm font-bold shadow-lg">
                            {state.encryption.enabled ? 'Replace key' : 'Set up encryption'}
                        </button>
                        {!state.encryption.disabled && (
                            <button onClick={handleDisableEncryption} disabled={busy} className="text-sm text-gray-400 hover:text-white underline">
                                {state.encryption.enabled ? 'Turn encryption off' : 'Back up without encryption instead'}
                            </button>
                        )}
                    </div>
                )}
            </div>

            {/* --- SCHEDULE --- */}
            <div className={cardClass}>
                <h3 className="text-lg font-bold text-white">Schedule</h3>
                <p className="text-sm text-gray-400 mt-1">
                    Each kind of backup has its own schedule. Times are in the server's time zone ({state.timezone});
                    the server's clock now reads {new Date(state.server_time).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', timeZone: state.timezone })}.
                </p>

                <div className="mt-4 space-y-4">
                    {KINDS.map(kind => {
                        const schedule = draft[kind.key];
                        const Icon = kind.key === 'db' ? CircleStackIcon : PhotoIcon;
                        return (
                            <div key={kind.key} className="flex flex-col lg:flex-row lg:items-center gap-3 border-t border-gray-800 pt-4 first:border-t-0 first:pt-0">
                                <div className="flex-1 min-w-0">
                                    <p className="font-medium text-white flex items-center"><Icon className="w-5 h-5 mr-2 text-gray-500" />{kind.label}</p>
                                    <p className="text-sm text-gray-400">{kind.contains}</p>
                                </div>
                                <div className="flex flex-wrap items-center gap-2">
                                    <select aria-label={`${kind.label} frequency`} className={selectClass} disabled={!state.can_edit || busy}
                                        value={schedule.frequency} onChange={e => editSchedule(kind.key, { frequency: e.target.value as Frequency })}>
                                        <option value="off">Off</option>
                                        <option value="daily">Every day</option>
                                        <option value="weekly">Every week</option>
                                        <option value="monthly">Every month</option>
                                    </select>
                                    {schedule.frequency === 'weekly' && (
                                        <select aria-label={`${kind.label} day of the week`} className={selectClass} disabled={!state.can_edit || busy}
                                            value={schedule.weekday} onChange={e => editSchedule(kind.key, { weekday: Number(e.target.value) })}>
                                            {WEEKDAYS.map((day, idx) => <option key={idx} value={idx}>on {day}</option>)}
                                        </select>
                                    )}
                                    {schedule.frequency === 'monthly' && (
                                        <select aria-label={`${kind.label} day of the month`} className={selectClass} disabled={!state.can_edit || busy}
                                            value={schedule.monthday} onChange={e => editSchedule(kind.key, { monthday: Number(e.target.value) })}>
                                            {Array.from({ length: 28 }, (_, i) => i + 1).map(day => <option key={day} value={day}>on day {day}</option>)}
                                        </select>
                                    )}
                                    {schedule.frequency !== 'off' && (
                                        <select aria-label={`${kind.label} time`} className={selectClass} disabled={!state.can_edit || busy}
                                            value={schedule.hour} onChange={e => editSchedule(kind.key, { hour: Number(e.target.value) })}>
                                            {Array.from({ length: 24 }, (_, hour) => <option key={hour} value={hour}>at {String(hour).padStart(2, '0')}:00</option>)}
                                        </select>
                                    )}
                                </div>
                            </div>
                        );
                    })}
                </div>

                {state.can_edit && (
                    <div className="flex flex-wrap items-center gap-3 mt-4">
                        <button onClick={handleSaveSchedule} disabled={busy || !scheduleChanged}
                            className="bg-blue-600 hover:bg-blue-500 disabled:opacity-50 text-white px-4 py-2 rounded-lg text-sm font-bold shadow-lg">
                            Save schedule
                        </button>
                        {scheduleChanged && <span className="text-xs text-blue-400">You have unsaved changes.</span>}
                        {!ready && <span className="text-xs text-yellow-400">Scheduled backups start once encryption is set up.</span>}
                    </div>
                )}
            </div>

            {/* --- BACK UP NOW --- */}
            {state.can_edit && (
                <div className={cardClass}>
                    <h3 className="text-lg font-bold text-white">Back up now</h3>
                    <p className="text-sm text-gray-400 mt-1">
                        Makes a backup straight away, in addition to the schedule. It appears in the list below when it finishes.
                    </p>
                    <div className="flex flex-wrap items-center gap-3 mt-4">
                        <button onClick={() => handleBackupNow('db')} disabled={busy || running || !ready}
                            className="bg-gray-700 hover:bg-gray-600 disabled:opacity-50 text-white px-4 py-2 rounded-lg text-sm font-bold flex items-center">
                            <CircleStackIcon className="w-4 h-4 mr-2" /> Back up database now
                        </button>
                        <button onClick={() => handleBackupNow('full')} disabled={busy || running || !ready}
                            className="bg-gray-700 hover:bg-gray-600 disabled:opacity-50 text-white px-4 py-2 rounded-lg text-sm font-bold flex items-center">
                            <PhotoIcon className="w-4 h-4 mr-2" /> Run full backup now
                        </button>
                        {running && <span className="text-xs text-blue-400">A backup is running…</span>}
                        {!ready && <span className="text-xs text-yellow-400">Set up encryption first.</span>}
                    </div>
                </div>
            )}

            {/* --- EXISTING BACKUPS --- */}
            <div className="bg-gray-900 rounded-xl border border-gray-800 overflow-hidden shadow-xl">
                <div className="px-5 py-4 border-b border-gray-800">
                    <h3 className="text-lg font-bold text-white">Backups</h3>
                    <p className="text-sm text-gray-400">
                        All stored in <span className="font-mono text-gray-300">{state.folder}</span> on the server. A name starting
                        with <span className="font-mono text-gray-300">finance-db-</span> is a database backup;{' '}
                        <span className="font-mono text-gray-300">finance-full-</span> is a full backup.
                        How long they are kept is set under the Storage tab.
                    </p>
                </div>
                {state.backups.length === 0 ? (
                    <p className="px-5 py-6 text-sm text-gray-500">No backups yet.</p>
                ) : (
                    <div className="overflow-x-auto">
                        <table className="w-full text-left text-sm text-gray-400">
                            <thead className="bg-gray-950 text-xs uppercase text-gray-500 font-bold border-b border-gray-800">
                                <tr>
                                    <th className="px-5 py-3">Made</th>
                                    <th className="px-5 py-3">Type</th>
                                    <th className="px-5 py-3">Size</th>
                                    <th className="px-5 py-3">Status</th>
                                    <th className="px-5 py-3 text-right">Actions</th>
                                </tr>
                            </thead>
                            <tbody className="divide-y divide-gray-800">
                                {state.backups.map(backup => (
                                    <tr key={backup.id}>
                                        <td className="px-5 py-3 whitespace-nowrap text-gray-300">
                                            {new Date(backup.started_at).toLocaleString()}
                                            <span className="block text-xs text-gray-500">{backup.trigger === 'scheduled' ? 'Scheduled' : 'Made by hand'}</span>
                                            {backup.filename && <span className="block text-xs font-mono text-gray-500 break-all">{backup.filename}</span>}
                                        </td>
                                        <td className="px-5 py-3 whitespace-nowrap">{backup.kind === 'db' ? 'Database' : 'Full'}</td>
                                        <td className="px-5 py-3 whitespace-nowrap">{backup.status === 'ok' ? formatBytes(backup.size) : '-'}</td>
                                        <td className="px-5 py-3">
                                            {backup.status === 'ok' && (
                                                <span className="text-green-400 flex items-center">
                                                    {backup.encrypted
                                                        ? <><LockClosedIcon className="w-4 h-4 mr-1" /> Completed, encrypted</>
                                                        : <><LockOpenIcon className="w-4 h-4 mr-1 text-yellow-400" /><span className="text-yellow-400">Completed, not encrypted</span></>}
                                                </span>
                                            )}
                                            {backup.status === 'running' && <span className="text-blue-400">Running…</span>}
                                            {backup.status === 'failed' && <span className="text-red-400">Failed: {backup.error}</span>}
                                        </td>
                                        <td className="px-5 py-3 whitespace-nowrap text-right">
                                            {state.can_edit && backup.status === 'ok' && (
                                                <a href={`/api/backups/${backup.id}/`} title="Download this backup"
                                                    className="inline-flex items-center text-blue-400 hover:text-blue-300 mr-4">
                                                    <ArrowDownTrayIcon className="w-4 h-4 mr-1" /> Download
                                                </a>
                                            )}
                                            {state.can_edit && backup.status !== 'running' && (
                                                <button onClick={() => handleDelete(backup)} disabled={busy} title="Delete this backup"
                                                    className="inline-flex items-center text-gray-500 hover:text-red-400">
                                                    <TrashIcon className="w-4 h-4" />
                                                </button>
                                            )}
                                        </td>
                                    </tr>
                                ))}
                            </tbody>
                        </table>
                    </div>
                )}
            </div>

            {/* --- RESTORING --- */}
            <div className={cardClass}>
                <h3 className="text-lg font-bold text-white">Restoring from a backup</h3>
                <p className="text-sm text-gray-400 mt-1">
                    Restoring replaces everything in the app with the backup's contents, so it is done from the server's command line
                    and not from this page. The command is the same for a database backup and a full backup: use the file name
                    shown in the list above. Put your key file in the backups folder for the restore, then run:
                </p>
                <pre className="mt-3 bg-black border border-gray-800 rounded-lg p-3 text-xs text-gray-300 overflow-x-auto">
{`docker compose exec -u app backend python manage.py restore_backup \\
    ${state.folder}/<backup file name> --identity ${state.folder}/<your key file>`}
                </pre>
                <p className="text-xs text-gray-500 mt-2">
                    A backup is a standard tar archive, encrypted in the open "age" format. It can also be opened without this app,
                    using the age, tar and pg_restore tools. Remove the key file from the server again afterwards.
                </p>
            </div>
        </div>
    );
};

export default BackupSettings;
