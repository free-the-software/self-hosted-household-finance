// Copyright (C) 2026 free-the-software
// SPDX-License-Identifier: AGPL-3.0-or-later
// This file is part of Save More Money, under the GNU AGPL v3 or later: see LICENSE.
import axios from 'axios';

export interface Session {
    authenticated: boolean;
    login_url?: string;
    username?: string;
    email?: string;
    name?: string;
    // Signed out after this many seconds without activity (0 = never)
    idle_timeout_seconds?: number;
    // Admins also manage users, backups and storage
    is_admin?: boolean;
    // Signed in with this app's own username and password (no single sign-on)
    password_login?: boolean;
    // Signed in with a one-time password: choose your own before anything else
    must_change_password?: boolean;
}

const getCookie = (name: string): string | null => {
    const match = document.cookie.split('; ').find(row => row.startsWith(`${name}=`));
    return match ? decodeURIComponent(match.split('=').slice(1).join('=')) : null;
};

// Asks Django whether we have a session. Also sets the csrftoken cookie.
export const getSession = async (): Promise<Session> => {
    const res = await fetch('/api/me/', { credentials: 'same-origin' });
    if (!res.ok) throw new Error(`Session check failed: ${res.status}`);
    return res.json();
};

// Sends the browser to the login page and back to the current page afterwards
export const redirectToLogin = (loginUrl: string) => {
    const next = window.location.pathname + window.location.search;
    window.location.assign(`${loginUrl}?next=${encodeURIComponent(next)}`);
};

// A 401/403 usually means the session expired mid-use. Confirm that with
// Django before redirecting, so a genuine "forbidden" doesn't bounce the user.
let sessionCheck: Promise<void> | null = null;
const handleAuthFailure = () => {
    if (sessionCheck) return;
    sessionCheck = getSession()
        .then(session => {
            if (!session.authenticated && session.login_url) redirectToLogin(session.login_url);
        })
        .catch(() => undefined)
        .finally(() => { sessionCheck = null; });
};

const api = axios.create({
    baseURL: '/api', // Using relative URL which Vite proxies to Django
    withCredentials: true, // IMPORTANT: Sends the Session Cookie
    // Django's CSRF check: echo the csrftoken cookie back in this header
    xsrfCookieName: 'csrftoken',
    xsrfHeaderName: 'X-CSRFToken',
    headers: {
        'Content-Type': 'application/json',
    }
});

api.interceptors.response.use(
    response => response,
    error => {
        if (error.response?.status === 401 || error.response?.status === 403) handleAuthFailure();
        return Promise.reject(error);
    }
);

// Drop-in replacement for fetch() on /api and /media URLs: sends the session
// cookie, adds the CSRF header on writes, and handles an expired session.
export const apiFetch = async (input: string, init: RequestInit = {}): Promise<Response> => {
    const headers = new Headers(init.headers);
    const method = (init.method || 'GET').toUpperCase();
    if (!['GET', 'HEAD', 'OPTIONS'].includes(method)) {
        const token = getCookie('csrftoken');
        if (token) headers.set('X-CSRFToken', token);
    }

    const res = await fetch(input, { ...init, headers, credentials: 'same-origin' });
    if (res.status === 401 || res.status === 403) handleAuthFailure();
    return res;
};

export const logout = async () => {
    const res = await api.post('/logout/');
    window.location.assign(res.data.redirect_url || '/');
};

export default api;
