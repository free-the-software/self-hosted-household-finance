// Copyright (C) 2026 free-the-software
// SPDX-License-Identifier: AGPL-3.0-or-later
// This file is part of Save More Money, under the GNU AGPL v3 or later: see LICENSE.
import { useState } from 'react';
import type { FormEvent } from 'react';
import api, { logout } from '../api';
import { PasswordFields } from './MyAccount';

// After signing in with a one-time password (the first admin's, or one an
// admin set): nothing else works until you choose your own
// (finance/auth.py PasswordChangeRequired).

const ChooseNewPassword = ({ username }: { username?: string }) => {
    const [current, setCurrent] = useState('');
    const [fresh, setFresh] = useState({ password: '', repeat: '' });
    const [error, setError] = useState('');
    const [busy, setBusy] = useState(false);

    const submit = async (e: FormEvent) => {
        e.preventDefault();
        if (fresh.password !== fresh.repeat) { setError("The two new passwords don't match."); return; }
        setBusy(true);
        setError('');
        try {
            await api.post('/account/password/', { current_password: current, new_password: fresh.password });
            window.location.reload();
        } catch (err: any) {
            setError(err.response?.data?.error || err.response?.data?.detail || 'Could not change the password.');
            setBusy(false);
        }
    };

    const input = 'w-full bg-gray-800 border border-gray-700 rounded-lg text-white p-2.5 text-sm focus:border-blue-500 outline-none';
    return (
        <div className="min-h-screen flex items-center justify-center bg-black text-white p-4">
            <form onSubmit={submit} className="w-full max-w-sm bg-gray-900 border border-gray-800 rounded-2xl shadow-2xl p-8 space-y-5">
                <div>
                    <h1 className="text-xl font-bold">Choose your password</h1>
                    <p className="text-sm text-gray-400 mt-1">
                        {username ? <>You signed in as <span className="text-white font-mono">{username}</span> with </> : 'You signed in with '}
                        a one-time password. Choose your own to carry on.
                    </p>
                </div>
                <div>
                    <label htmlFor="cp-current" className="block text-xs font-bold text-gray-400 uppercase mb-1">One-time password</label>
                    <input id="cp-current" type="password" autoComplete="current-password" autoFocus required value={current}
                        onChange={e => setCurrent(e.target.value)} className={input} />
                </div>
                <PasswordFields value={fresh} onChange={setFresh} idPrefix="cp" />
                {error && <p className="text-sm text-red-400" role="alert">{error}</p>}
                <button type="submit" disabled={busy} className="w-full py-2.5 bg-blue-600 hover:bg-blue-500 disabled:opacity-50 rounded-lg font-bold">
                    {busy ? 'Saving...' : 'Save and continue'}
                </button>
                <button type="button" onClick={() => logout().catch(() => window.location.assign('/'))} className="w-full text-sm text-gray-400 hover:text-white">
                    Sign out
                </button>
            </form>
        </div>
    );
};

export default ChooseNewPassword;
