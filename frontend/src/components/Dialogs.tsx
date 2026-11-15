// Copyright (C) 2026 free-the-software
// SPDX-License-Identifier: AGPL-3.0-or-later
// This file is part of Save More Money, under the GNU AGPL v3 or later: see LICENSE.
import { useEffect, useRef, useState } from 'react';
import type { ReactNode } from 'react';
import { ExclamationTriangleIcon, XMarkIcon } from '@heroicons/react/24/outline';

// The app's own "are you sure?" window and notices, used in place of the
// browser's confirm() and alert(). A browser lets anyone tick "don't allow
// this site to prompt again", after which confirm() answers No without
// asking (so deletes silently did nothing) and alert() shows nothing.
//
//   if (!(await askConfirm({ title: 'Delete this backup?', confirmLabel: 'Delete', danger: true }))) return;
//   notify('Could not save.');
//
// <Dialogs /> is mounted once (App.tsx); these work from anywhere after that.
//
// dontAskAgain: a name for this question. The window then has a "Don't ask me
// again" box; once it's ticked and confirmed, this browser answers Yes without
// asking. Settings → My account lists these and can turn them back on.

export interface ConfirmOptions {
    title: string;
    message?: ReactNode;
    confirmLabel?: string;
    cancelLabel?: string;
    // Can't be undone: the button is red, and Cancel has the focus
    danger?: boolean;
    dontAskAgain?: string;
}
type Request = ConfirmOptions & { resolve: (ok: boolean) => void };
type Notice = { id: number; text: string; kind: 'error' | 'info' };

let showConfirm: ((request: Request) => void) | null = null;
let showNotice: ((notice: Notice) => void) | null = null;
let nextId = 1;

// The questions not to ask again on this browser: { name: the question's title }
const SKIP_KEY = 'dont-ask-again';
export const skippedQuestions = (): Record<string, string> => {
    try { return JSON.parse(localStorage.getItem(SKIP_KEY) || '{}') || {}; } catch { return {}; }
};
const skipFromNowOn = (name: string, title: string) => {
    try { localStorage.setItem(SKIP_KEY, JSON.stringify({ ...skippedQuestions(), [name]: title })); } catch { /* can't remember: it asks again next time */ }
};
export const askAgain = () => {
    try { localStorage.removeItem(SKIP_KEY); } catch { /* nothing stored */ }
};

export const askConfirm = (options: ConfirmOptions): Promise<boolean> =>
    new Promise(resolve => {
        if (options.dontAskAgain && skippedQuestions()[options.dontAskAgain]) resolve(true);
        else if (showConfirm) showConfirm({ ...options, resolve });
        else resolve(window.confirm(options.title));    // only before <Dialogs /> is mounted
    });

export const notify = (text: string, kind: Notice['kind'] = 'error') => {
    if (showNotice) showNotice({ id: nextId++, text, kind });
    else console.warn(text);
};

const NOTICE_SECONDS = { error: 10, info: 5 };

const Dialogs = () => {
    const [request, setRequest] = useState<Request | null>(null);
    const [notices, setNotices] = useState<Notice[]>([]);
    const [skip, setSkip] = useState(false);
    const cancelRef = useRef<HTMLButtonElement>(null);
    const confirmRef = useRef<HTMLButtonElement>(null);

    useEffect(() => {
        showConfirm = next => setRequest(current => {
            current?.resolve(false);            // one at a time: a newer question replaces an older one
            return next;
        });
        showNotice = notice => {
            setNotices(current => [...current.slice(-3), notice]);
            setTimeout(() => setNotices(current => current.filter(n => n.id !== notice.id)), NOTICE_SECONDS[notice.kind] * 1000);
        };
        return () => { showConfirm = null; showNotice = null; };
    }, []);

    useEffect(() => {
        if (!request) return;
        setSkip(false);
        (request.danger ? cancelRef : confirmRef).current?.focus();
        const keys = (e: KeyboardEvent) => { if (e.key === 'Escape') answer(false); };
        window.addEventListener('keydown', keys);
        return () => window.removeEventListener('keydown', keys);
    }, [request]);

    const answer = (ok: boolean) => {
        if (ok && skip && request?.dontAskAgain) skipFromNowOn(request.dontAskAgain, request.title);
        request?.resolve(ok);
        setRequest(null);
    };

    return (
        <>
            {request && (
                <div className="fixed inset-0 z-[200] bg-black/80 backdrop-blur-sm flex items-center justify-center p-4"
                    role="alertdialog" aria-modal="true" aria-labelledby="confirm-title">
                    <div className="bg-gray-900 border border-gray-700 rounded-2xl shadow-2xl w-full max-w-md p-6 space-y-4">
                        <div className="flex gap-3">
                            {request.danger && <ExclamationTriangleIcon className="w-6 h-6 text-red-400 shrink-0 mt-0.5" />}
                            <div className="space-y-2">
                                <h2 id="confirm-title" className="text-lg font-bold text-white">{request.title}</h2>
                                {request.message && <div className="text-sm text-gray-300 whitespace-pre-line">{request.message}</div>}
                            </div>
                        </div>
                        {request.dontAskAgain && (
                            <label className="flex items-center gap-2 text-sm text-gray-400 cursor-pointer select-none">
                                <input type="checkbox" checked={skip} onChange={e => setSkip(e.target.checked)}
                                    className="w-4 h-4 rounded border-gray-600 bg-gray-800 text-blue-600 focus:ring-blue-500" />
                                Don't ask me again
                            </label>
                        )}
                        <div className="flex justify-end gap-3 pt-2">
                            <button ref={cancelRef} onClick={() => answer(false)}
                                className="px-4 py-2 text-gray-300 hover:text-white rounded-lg focus:outline-none focus:ring-2 focus:ring-gray-500">
                                {request.cancelLabel || 'Cancel'}
                            </button>
                            <button ref={confirmRef} onClick={() => answer(true)}
                                className={`px-5 py-2 text-white font-bold rounded-lg focus:outline-none focus:ring-2 ${
                                    request.danger ? 'bg-red-600 hover:bg-red-500 focus:ring-red-400' : 'bg-blue-600 hover:bg-blue-500 focus:ring-blue-400'}`}>
                                {request.confirmLabel || 'OK'}
                            </button>
                        </div>
                    </div>
                </div>
            )}

            <div className="fixed bottom-4 right-4 left-4 sm:left-auto z-[210] flex flex-col gap-2 sm:w-96" aria-live="polite">
                {notices.map(n => (
                    <div key={n.id} role={n.kind === 'error' ? 'alert' : 'status'}
                        className={`flex items-start gap-3 rounded-xl border p-3 shadow-2xl text-sm ${
                            n.kind === 'error' ? 'bg-red-950 border-red-700 text-red-100' : 'bg-gray-900 border-gray-700 text-gray-100'}`}>
                        <span className="flex-1 whitespace-pre-line break-words">{n.text}</span>
                        <button onClick={() => setNotices(current => current.filter(x => x.id !== n.id))}
                            className="text-gray-400 hover:text-white" aria-label="Close">
                            <XMarkIcon className="w-4 h-4" />
                        </button>
                    </div>
                ))}
            </div>
        </>
    );
};

export default Dialogs;
