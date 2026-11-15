// Copyright (C) 2026 free-the-software
// SPDX-License-Identifier: AGPL-3.0-or-later
// This file is part of Save More Money, under the GNU AGPL v3 or later: see LICENSE.
import { useEffect, useState } from 'react';
import api from '../api';
import CommandBox from './CommandBox';

// Settings → Storage: the version, whether a newer release is out, and the
// command to update with, ready to paste on the server (finance/system.py).
// The app never updates itself: it has no control over Docker.

export interface SystemStatus {
    disk: { known: boolean; low: boolean; free_gb?: number; total_gb?: number; message?: string };
    update: null | {
        enabled: boolean; current: string; command: string; in_folder: boolean;
        latest?: string; newer?: boolean; url?: string; notes?: string;
    };
}

const UpdatesPanel = () => {
    const [status, setStatus] = useState<SystemStatus | null>(null);

    useEffect(() => {
        api.get('/system/status/').then(res => setStatus(res.data)).catch(() => undefined);
    }, []);

    if (!status) return null;
    const { disk, update } = status;

    return (
        <div className="bg-gray-900 rounded-xl border border-gray-800 p-5 mb-6 space-y-4">
            <div className="flex flex-wrap gap-x-8 gap-y-2 text-sm">
                <p className="text-gray-400">Version: <span className="text-white font-mono">{update?.current || 'built from source'}</span></p>
                {disk.known && (
                    <p className={disk.low ? 'text-red-300' : 'text-gray-400'}>
                        Free disk: <span className="font-mono text-white">{disk.free_gb} GB</span> of {disk.total_gb} GB
                    </p>
                )}
            </div>

            {update && (
                <div className="space-y-3">
                    {update.newer ? (
                        <div className="bg-blue-900/20 border border-blue-500/40 rounded-lg p-3 text-sm space-y-2">
                            <p className="text-blue-200 font-bold">Version {update.latest} is available.</p>
                            {update.notes && <p className="text-gray-300 whitespace-pre-line max-h-40 overflow-y-auto text-xs">{update.notes}</p>}
                            {update.url && <a href={update.url} target="_blank" rel="noreferrer" className="text-xs text-blue-400 underline">What's new, in full</a>}
                        </div>
                    ) : update.latest ? (
                        <p className="text-sm text-gray-400">You're on the latest release ({update.latest}).</p>
                    ) : !update.enabled ? (
                        <p className="text-xs text-gray-500">Checking for new releases is off (System settings, below).</p>
                    ) : null}

                    <div>
                        <p className="text-xs font-bold text-gray-400 uppercase mb-1">To update, run this on the server</p>
                        <CommandBox command={update.command} />
                        <p className="text-xs text-gray-500 mt-1">
                            {!update.in_folder && 'From the folder the app is installed in. '}
                            It backs up the database first, then updates, restarts and tidies up. <code>--check</code> only says what's new.
                        </p>
                    </div>
                </div>
            )}
        </div>
    );
};

export default UpdatesPanel;
