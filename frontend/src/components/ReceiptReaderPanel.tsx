// Copyright (C) 2026 free-the-software
// SPDX-License-Identifier: AGPL-3.0-or-later
// This file is part of Save More Money, under the GNU AGPL v3 or later: see LICENSE.
import { useEffect, useState } from 'react';
import { ExclamationTriangleIcon } from '@heroicons/react/24/outline';
import api from '../api';
import CommandBox from './CommandBox';

// Settings → Storage: the receipt reader, an optional add-on (beta). Whether
// it's installed, and the command to add or remove it, ready to paste on the
// server (scripts/receipt-reader.sh). Docker does the installing, not the app.

interface ReaderState {
    installed: boolean; available: boolean; in_folder: boolean;
    command_on: string; command_off: string;
}

const ReceiptReaderPanel = () => {
    const [state, setState] = useState<ReaderState | null>(null);

    useEffect(() => {
        api.get('/receipts/reader/').then(res => setState(res.data)).catch(() => undefined);
    }, []);

    if (!state) return null;
    const where = state.in_folder ? '' : 'From the folder the app is installed in. ';

    return (
        <div className="bg-gray-900 rounded-xl border border-gray-800 p-5 mb-6 space-y-4">
            <div className="flex flex-wrap items-baseline justify-between gap-2">
                <h2 className="text-lg font-bold text-white">
                    Receipt reader <span className="ml-1 align-middle text-xs font-bold uppercase tracking-wide text-amber-300 bg-amber-900/40 border border-amber-500/40 rounded px-1.5 py-0.5">Beta</span>
                </h2>
                <p className={`text-sm font-bold ${!state.installed ? 'text-gray-400' : state.available ? 'text-green-400' : 'text-amber-300'}`}>
                    {!state.installed ? 'Not installed' : state.available ? 'Installed and ready' : 'Installed, not answering'}
                </p>
            </div>

            <p className="text-sm text-gray-300">
                An optional add-on that reads photos of receipts (store, date, total, tax) and suggests the transaction each belongs to.
                It runs on your own computer: receipts are never sent anywhere.
            </p>

            <div className="flex gap-3 bg-amber-900/20 border border-amber-500/40 rounded-lg p-3 text-sm text-amber-100">
                <ExclamationTriangleIcon className="w-5 h-5 text-amber-400 shrink-0 mt-0.5" />
                <div className="space-y-1">
                    <p><span className="font-bold">In beta:</span> it often misreads receipts, especially long, creased or faded ones. Treat what it reads as a suggestion; nothing changes until you approve it.</p>
                    <p><span className="font-bold">Needs:</span> about 2.5 GB more disk, about 2 GB more memory while reading (released after 10 minutes without a receipt), and 10 minutes or more to build the first time. Reading takes tens of seconds per receipt.</p>
                </div>
            </div>

            {state.installed && !state.available && (
                <p className="text-sm text-amber-200">
                    It's switched on but isn't answering. If the app has only just started, wait a minute. Otherwise start it again with the command to add it, below: it rebuilds and restarts it without removing anything.
                </p>
            )}

            <div>
                <p className="text-xs font-bold text-gray-400 uppercase mb-1">
                    {state.installed ? 'To remove it, run this on the server' : 'To add it, run this on the server'}
                </p>
                <CommandBox command={state.installed ? state.command_off : state.command_on} />
                <p className="text-xs text-gray-500 mt-1">
                    {where}
                    {state.installed
                        ? 'It frees the reader\'s disk space. Receipts already read stay in the app.'
                        : 'It switches the reader on in .env, builds it and restarts the app. Receipt photos waiting to be read are read once it\'s running.'}
                </p>
                {state.installed && !state.available && (
                    <div className="mt-3">
                        <p className="text-xs font-bold text-gray-400 uppercase mb-1">To start it again</p>
                        <CommandBox command={state.command_on} />
                    </div>
                )}
            </div>
        </div>
    );
};

export default ReceiptReaderPanel;
