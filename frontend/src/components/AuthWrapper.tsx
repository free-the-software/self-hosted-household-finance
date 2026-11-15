// Copyright (C) 2026 free-the-software
// SPDX-License-Identifier: AGPL-3.0-or-later
// This file is part of Save More Money, under the GNU AGPL v3 or later: see LICENSE.
import { useEffect, useState } from 'react';
import { getSession, redirectToLogin } from '../api';
import IdleSignOut from './IdleSignOut';
import SignIn from './SignIn';
import ChooseNewPassword from './ChooseNewPassword';

type AuthState = 'loading' | 'authenticated' | 'new-password' | 'sign-in' | 'failed' | 'unreachable';

const AuthWrapper = ({ children }: { children: React.ReactNode }) => {
    const [state, setState] = useState<AuthState>('loading');
    const [loginUrl, setLoginUrl] = useState('');
    const [idleTimeout, setIdleTimeout] = useState(0);
    const [username, setUsername] = useState('');

    useEffect(() => {
        const checkAuth = async () => {
            try {
                const session = await getSession();
                if (session.authenticated) {
                    setIdleTimeout(session.idle_timeout_seconds || 0);
                    setUsername(session.username || '');
                    setState(session.must_change_password ? 'new-password' : 'authenticated');
                    return;
                }

                // No single sign-on: the app's own sign-in page, right here
                if (session.password_login) {
                    setState('sign-in');
                    return;
                }

                const url = session.login_url || '';
                setLoginUrl(url);

                // Django sends us back with ?auth_error=1 when the login was
                // rejected. Stop here instead of redirecting in a loop.
                if (new URLSearchParams(window.location.search).has('auth_error')) {
                    setState('failed');
                    return;
                }
                redirectToLogin(url);
            } catch (error) {
                console.error("Session check failed", error);
                setState('unreachable');
            }
        };
        checkAuth();
    }, []);

    if (state === 'authenticated') return <>{children}<IdleSignOut timeoutSeconds={idleTimeout} /></>;
    if (state === 'sign-in') return <SignIn />;
    if (state === 'new-password') return <><ChooseNewPassword username={username} /><IdleSignOut timeoutSeconds={idleTimeout} /></>;

    if (state === 'failed' || state === 'unreachable') {
        return (
            <div className="flex h-screen flex-col items-center justify-center gap-4 bg-black text-white">
                <p className="text-gray-300">
                    {state === 'failed'
                        ? "Sign-in failed, or this account isn't allowed to use this app."
                        : "Can't reach the server right now."}
                </p>
                <button
                    onClick={() => state === 'failed' ? window.location.assign(loginUrl) : window.location.reload()}
                    className="px-4 py-2 bg-blue-600 hover:bg-blue-500 rounded-md font-medium"
                >
                    Try again
                </button>
            </div>
        );
    }

    return <div className="flex h-screen items-center justify-center bg-black text-white">Loading...</div>;
};

export default AuthWrapper;
