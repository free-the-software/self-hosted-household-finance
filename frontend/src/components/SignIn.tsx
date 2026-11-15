// Copyright (C) 2026 free-the-software
// SPDX-License-Identifier: AGPL-3.0-or-later
// This file is part of Save More Money, under the GNU AGPL v3 or later: see LICENSE.
import { useState } from 'react';
import type { FormEvent } from 'react';
import api from '../api';
import Logo from './Logo';

// The app's own sign-in, when there is no single sign-on (finance/auth.py).
// Shown in place of the app; afterwards the browser goes back to the page
// it was on (?next= when the session ran out somewhere else).

const nextPage = () => {
    const next = new URLSearchParams(window.location.search).get('next') || '';
    // Only pages of this app. The address is worked out the way the browser
    // would, so tricks like "//other.site" or "/\other.site" (browsers read
    // the backslash as a slash) can't send someone elsewhere.
    if (next.startsWith('/')) {
        try {
            const url = new URL(next, window.location.origin);
            if (url.origin === window.location.origin) return url.pathname + url.search + url.hash;
        } catch {
            // not an address at all
        }
    }
    return window.location.pathname === '/sign-in' ? '/' : window.location.pathname + window.location.search;
};

const SignIn = () => {
    const [username, setUsername] = useState('');
    const [password, setPassword] = useState('');
    const [error, setError] = useState('');
    const [busy, setBusy] = useState(false);

    const submit = async (e: FormEvent) => {
        e.preventDefault();
        setBusy(true);
        setError('');
        try {
            await api.post('/sign-in/', { username, password });
            window.location.assign(nextPage());
        } catch (err: any) {
            setError(err.response?.data?.error || err.response?.data?.detail || 'Could not sign in. Try again.');
            setBusy(false);
        }
    };

    const input = 'w-full bg-gray-800 border border-gray-700 rounded-lg text-white p-2.5 text-sm focus:border-blue-500 outline-none';
    return (
        <div className="min-h-screen flex items-center justify-center bg-black text-white p-4">
            <form onSubmit={submit} className="w-full max-w-sm bg-gray-900 border border-gray-800 rounded-2xl shadow-2xl p-8 space-y-5">
                <h1 className="flex justify-center pb-2" aria-label="Save More Money"><Logo size="lg" /></h1>
                <div>
                    <label htmlFor="si-user" className="block text-xs font-bold text-gray-400 uppercase mb-1">Username</label>
                    <input id="si-user" autoComplete="username" autoFocus required value={username} onChange={e => setUsername(e.target.value)} className={input} />
                </div>
                <div>
                    <label htmlFor="si-pass" className="block text-xs font-bold text-gray-400 uppercase mb-1">Password</label>
                    <input id="si-pass" type="password" autoComplete="current-password" required value={password} onChange={e => setPassword(e.target.value)} className={input} />
                </div>
                {error && <p className="text-sm text-red-400" role="alert">{error}</p>}
                <button type="submit" disabled={busy} className="w-full py-2.5 bg-blue-600 hover:bg-blue-500 disabled:opacity-50 rounded-lg font-bold">
                    {busy ? 'Signing in...' : 'Sign in'}
                </button>
                <p className="text-xs text-gray-500">
                    Forgot your password? An admin can set a new one in Settings → Users. If you're the only admin, run this on the
                    computer the app runs on, and sign in with the one-time password it prints: <code className="text-gray-400">docker compose exec backend python manage.py reset_password your-username</code>
                </p>
            </form>
        </div>
    );
};

export default SignIn;
