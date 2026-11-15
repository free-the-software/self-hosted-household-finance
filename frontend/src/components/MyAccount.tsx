// Copyright (C) 2026 free-the-software
// SPDX-License-Identifier: AGPL-3.0-or-later
// This file is part of Save More Money, under the GNU AGPL v3 or later: see LICENSE.
import { useState } from 'react';
import type { FormEvent } from 'react';
import api from '../api';
import type { Session } from '../api';
import { askAgain, skippedQuestions } from './Dialogs';

// Settings → My account: who you're signed in as, and your password. With
// single sign-on the password belongs to the identity provider.

export const PasswordFields = ({ value, onChange, idPrefix }: {
    value: { password: string; repeat: string }; onChange: (v: { password: string; repeat: string }) => void; idPrefix: string;
}) => {
    const input = 'w-full bg-gray-800 border border-gray-700 rounded-lg text-white p-2.5 text-sm focus:border-blue-500 outline-none';
    return (
        <>
            <div>
                <label htmlFor={`${idPrefix}-new`} className="block text-xs font-bold text-gray-400 uppercase mb-1">New password</label>
                <input id={`${idPrefix}-new`} type="password" autoComplete="new-password" required value={value.password}
                    onChange={e => onChange({ ...value, password: e.target.value })} className={input} />
                <p className="text-xs text-gray-500 mt-1">At least 8 characters, not a common password, and not like the username.</p>
            </div>
            <div>
                <label htmlFor={`${idPrefix}-repeat`} className="block text-xs font-bold text-gray-400 uppercase mb-1">New password again</label>
                <input id={`${idPrefix}-repeat`} type="password" autoComplete="new-password" required value={value.repeat}
                    onChange={e => onChange({ ...value, repeat: e.target.value })} className={input} />
            </div>
        </>
    );
};

const MyAccount = ({ session }: { session: Session | null }) => {
    const [current, setCurrent] = useState('');
    const [fresh, setFresh] = useState({ password: '', repeat: '' });
    const [message, setMessage] = useState<{ ok: boolean; text: string } | null>(null);
    const [busy, setBusy] = useState(false);
    const [skipped, setSkipped] = useState(skippedQuestions);

    const save = async (e: FormEvent) => {
        e.preventDefault();
        if (fresh.password !== fresh.repeat) { setMessage({ ok: false, text: "The two new passwords don't match." }); return; }
        setBusy(true);
        try {
            await api.post('/account/password/', { current_password: current, new_password: fresh.password });
            setMessage({ ok: true, text: 'Password changed. Other devices signed in as you have been signed out.' });
            setCurrent('');
            setFresh({ password: '', repeat: '' });
        } catch (err: any) {
            setMessage({ ok: false, text: err.response?.data?.error || err.response?.data?.detail || 'Could not change the password.' });
        } finally {
            setBusy(false);
        }
    };

    if (!session) return <p className="text-gray-400">Loading...</p>;
    const input = 'w-full bg-gray-800 border border-gray-700 rounded-lg text-white p-2.5 text-sm focus:border-blue-500 outline-none';
    return (
        <div className="animate-in fade-in duration-300 grid grid-cols-1 lg:grid-cols-2 gap-6 max-w-4xl">
            <div className="bg-gray-900 rounded-xl border border-gray-800 p-6 space-y-2 h-fit">
                <h2 className="text-lg font-bold text-white">Signed in as</h2>
                <p className="text-white font-mono">{session.username}</p>
                {session.name && <p className="text-sm text-gray-400">{session.name}</p>}
                {session.email && <p className="text-sm text-gray-400">{session.email}</p>}
                <p className="text-xs text-gray-500 pt-2">{session.is_admin ? 'Admin: can also manage users, backups and storage.' : 'User'}</p>
            </div>

            {session.password_login ? (
                <form onSubmit={save} className="bg-gray-900 rounded-xl border border-gray-800 p-6 space-y-4">
                    <h2 className="text-lg font-bold text-white">Change your password</h2>
                    <div>
                        <label htmlFor="pw-current" className="block text-xs font-bold text-gray-400 uppercase mb-1">Current password</label>
                        <input id="pw-current" type="password" autoComplete="current-password" required value={current} onChange={e => setCurrent(e.target.value)} className={input} />
                    </div>
                    <PasswordFields value={fresh} onChange={setFresh} idPrefix="pw" />
                    {message && <p className={`text-sm ${message.ok ? 'text-green-400' : 'text-red-400'}`} role="status">{message.text}</p>}
                    <button type="submit" disabled={busy} className="px-5 py-2 bg-blue-600 hover:bg-blue-500 disabled:opacity-50 text-white font-bold rounded-lg">
                        {busy ? 'Saving...' : 'Change password'}
                    </button>
                </form>
            ) : (
                <div className="bg-gray-900 rounded-xl border border-gray-800 p-6 h-fit">
                    <h2 className="text-lg font-bold text-white mb-2">Password</h2>
                    <p className="text-sm text-gray-400">You sign in through single sign-on, so your password and second factor are changed there, not here.</p>
                </div>
            )}

            <div className="bg-gray-900 rounded-xl border border-gray-800 p-6 space-y-3 h-fit">
                <h2 className="text-lg font-bold text-white">Questions turned off</h2>
                {Object.keys(skipped).length ? (
                    <>
                        <p className="text-sm text-gray-400">On this browser, these questions are no longer asked:</p>
                        <ul className="text-sm text-gray-300 list-disc pl-5">
                            {Object.entries(skipped).map(([name, title]) => <li key={name}>{title}</li>)}
                        </ul>
                        <button onClick={() => { askAgain(); setSkipped({}); }}
                            className="px-4 py-2 text-sm font-bold text-white bg-gray-700 hover:bg-gray-600 rounded-lg">
                            Ask me again
                        </button>
                    </>
                ) : (
                    <p className="text-sm text-gray-400">None. Ticking "Don't ask me again" in a question adds it here, for this browser.</p>
                )}
            </div>
        </div>
    );
};

export default MyAccount;
