// Copyright (C) 2026 free-the-software
// SPDX-License-Identifier: AGPL-3.0-or-later
// This file is part of Save More Money, under the GNU AGPL v3 or later: see LICENSE.
import { useState } from 'react';
import { ClipboardDocumentIcon, CheckIcon } from '@heroicons/react/24/outline';
import { notify } from './Dialogs';

// A command to paste on the server, with a Copy button. The app never runs
// these itself: it has no control over Docker, by design.
const CommandBox = ({ command, label = 'Copy the command' }: { command: string; label?: string }) => {
    const [copied, setCopied] = useState(false);

    const copy = async () => {
        try {
            await navigator.clipboard.writeText(command);
            setCopied(true);
            setTimeout(() => setCopied(false), 2000);
        } catch {
            notify('Copying was blocked by the browser: select the command and copy it yourself.');
        }
    };

    return (
        <div className="flex items-center gap-2">
            <code className="flex-1 bg-black/40 border border-gray-700 rounded-lg px-3 py-2 text-sm text-gray-200 font-mono overflow-x-auto whitespace-nowrap select-all">{command}</code>
            <button onClick={copy} className="flex items-center gap-1 px-3 py-2 text-sm font-bold text-white bg-gray-700 hover:bg-gray-600 rounded-lg" aria-label={label}>
                {copied ? <CheckIcon className="w-4 h-4" /> : <ClipboardDocumentIcon className="w-4 h-4" />}
                {copied ? 'Copied' : 'Copy'}
            </button>
        </div>
    );
};

export default CommandBox;
