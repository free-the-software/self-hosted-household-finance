// Copyright (C) 2026 free-the-software
// SPDX-License-Identifier: AGPL-3.0-or-later
// This file is part of Save More Money, under the GNU AGPL v3 or later: see LICENSE.
import { useEffect, useRef, useState } from 'react';
import { getSession, logout } from '../api';

// Signs the user out after a period without activity, with a warning first.
// The server enforces the same limit (finance/idle.py), so it also holds when
// the tab is closed or the computer sleeps.

// Shared by every tab of the app, so working in one keeps them all signed in
const STORAGE_KEY = 'finance:lastActivity';
const WARN_SECONDS = 60;
// While the user is active, tell the server at most this often
const PING_EVERY_MS = 60_000;
const EVENTS = ['mousemove', 'mousedown', 'keydown', 'wheel', 'touchstart', 'scroll'] as const;

const readShared = () => {
    try { return Number(localStorage.getItem(STORAGE_KEY)) || 0; } catch { return 0; }
};
const writeShared = (time: number) => {
    try { localStorage.setItem(STORAGE_KEY, String(time)); } catch { /* private window: this tab only */ }
};

const IdleSignOut = ({ timeoutSeconds }: { timeoutSeconds: number }) => {
    const [secondsLeft, setSecondsLeft] = useState<number | null>(null);
    const lastActivity = useRef(Date.now());
    const lastPing = useRef(Date.now());
    const signingOut = useRef(false);

    const idleSeconds = () => (Date.now() - Math.max(lastActivity.current, readShared())) / 1000;

    const signOut = () => {
        if (signingOut.current) return;
        signingOut.current = true;
        // Ends the app's session and the identity provider's; if the server
        // has already ended it, starting over at the app does the same
        logout().catch(() => window.location.assign('/'));
    };

    const recordActivity = () => {
        // Waking a computer that slept past the limit is not activity
        if (idleSeconds() >= timeoutSeconds) { signOut(); return; }
        const now = Date.now();
        lastActivity.current = now;
        writeShared(now);
        setSecondsLeft(null);
        if (now - lastPing.current > PING_EVERY_MS) {
            lastPing.current = now;
            getSession().catch(() => undefined);   // counts as activity on the server
        }
    };

    useEffect(() => {
        if (!timeoutSeconds) return;
        lastActivity.current = Date.now();
        writeShared(lastActivity.current);

        let lastEvent = 0;
        const onActivity = () => {
            const now = Date.now();
            if (now - lastEvent < 1000) return;   // mousemove fires constantly
            lastEvent = now;
            recordActivity();
        };
        EVENTS.forEach(name => window.addEventListener(name, onActivity, { passive: true, capture: true }));

        const timer = window.setInterval(() => {
            const idle = idleSeconds();
            if (idle >= timeoutSeconds) signOut();
            else if (idle >= timeoutSeconds - WARN_SECONDS) setSecondsLeft(Math.ceil(timeoutSeconds - idle));
            else setSecondsLeft(null);
        }, 1000);

        return () => {
            EVENTS.forEach(name => window.removeEventListener(name, onActivity, { capture: true }));
            window.clearInterval(timer);
        };
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [timeoutSeconds]);

    if (secondsLeft === null) return null;
    return (
        <div className="fixed inset-0 z-[100] bg-black/70 flex items-center justify-center p-4">
            <div role="alertdialog" aria-labelledby="idle-title" className="bg-gray-900 border border-gray-700 rounded-2xl shadow-2xl p-6 max-w-sm w-full space-y-4">
                <h2 id="idle-title" className="text-lg font-bold text-white">Still there?</h2>
                <p className="text-gray-300 text-sm">
                    To protect your finances, you'll be signed out in <span className="font-mono font-bold text-white">{secondsLeft}</span> second{secondsLeft === 1 ? '' : 's'}.
                </p>
                <div className="flex justify-end gap-3">
                    <button onClick={signOut} className="px-4 py-2 text-gray-400 hover:text-white">Sign out now</button>
                    <button onClick={() => { lastPing.current = 0; recordActivity(); }} className="px-4 py-2 bg-blue-600 hover:bg-blue-500 text-white font-bold rounded-lg">Stay signed in</button>
                </div>
            </div>
        </div>
    );
};

export default IdleSignOut;
