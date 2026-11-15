// Copyright (C) 2026 free-the-software
// SPDX-License-Identifier: AGPL-3.0-or-later
// This file is part of Save More Money, under the GNU AGPL v3 or later: see LICENSE.
import { useEffect, useState } from 'react';
import FileDropzone from '../components/FileDropzone';
import ParkedAccountsBanner from './ParkedAccountBanner';
import { 
    CheckIcon, 
    TrashIcon, 
    ArrowPathIcon, 
    MagnifyingGlassIcon,
    XMarkIcon,
    ExclamationTriangleIcon,
    LinkIcon,
    DocumentPlusIcon
} from '@heroicons/react/24/outline';
import { apiFetch } from '../api';
import CategoryAllocation, { isSplitValid, splitRemaining } from './CategoryAllocation';
import type { CategoryGroup, SplitLine } from './CategoryAllocation';
import { askConfirm, notify } from './Dialogs';

interface StagedTransaction {
    id: number;
    date: string;
    amount: number;
    name: string;
    fitid: string;
    match_type: 'NEW' | 'MATCH' | 'NEAR' | 'DUPE';
    // What the user may set before approving
    subcategory?: number | null;
    subcategory_name?: string | null;
    personal_memo?: string;
    bank_memo?: string;
    splits?: { subcategory: number; subcategory_name: string | null; amount: string; memo: string }[];
    // Edited in the inbox: a matched entry takes these details when approved
    edited?: boolean;
    match_reason?: string;
    account: number;
    account_name: string;
    match_proposal_name?: string | null;
    // The transaction you entered by hand that this one matches
    match_proposal_details?: {
        id: number;
        date: string;
        description: string;
        amount: string;
        subcategory: number | null;
        subcategory_name: string | null;
        personal_memo: string;
        is_split: boolean;
        splits: { subcategory: number; amount: string; memo: string }[];
    } | null;
}

type PayeeChoice = 'entry' | 'bank';

interface PendingTransaction {
    id: number;
    date: string;
    description: string;
    amount: number;
    name?: string;
}

const ImportReview = () => {
    const [inbox, setInbox] = useState<StagedTransaction[]>([]);
    const [loading, setLoading] = useState(false);
    
    // --- Refresh trigger ---
    // We use this to force child components (like the Banner) to remount and fetch fresh data
    const [refreshKey, setRefreshKey] = useState(0);
    // Bank files that couldn't be imported, and why
    const [problems, setProblems] = useState<{ id: number; filename: string; when: string; problem: string }[]>([]);
    // The files with rows waiting, and whether to ask which way their amounts go
    const [files, setFiles] = useState<{
        id: number; filename: string; account: string | null; rows: number; ask: boolean;
        example: { name: string; amount: string } | null; card_mostly_money_in: boolean;
    }[]>([]);

    // --- MODAL STATE ---
    const [isLinkModalOpen, setIsLinkModalOpen] = useState(false);
    const [activeStagedTx, setActiveStagedTx] = useState<StagedTransaction | null>(null);
    const [pendingCandidates, setPendingCandidates] = useState<PendingTransaction[]>([]);
    // The payee each match will keep: your own entry's, unless you pick the
    // bank's on a near match (a full match always keeps yours)
    const [payeeChoice, setPayeeChoice] = useState<Record<number, PayeeChoice>>({});
    const [linkPayee, setLinkPayee] = useState<PayeeChoice>('entry');

    // --- EDIT BEFORE IMPORTING (payee, category or split, memo) ---
    const [categories, setCategories] = useState<CategoryGroup[]>([]);
    const [editing, setEditing] = useState<StagedTransaction | null>(null);
    const [editName, setEditName] = useState('');
    const [editSubcategory, setEditSubcategory] = useState<number | null>(null);
    const [editMemo, setEditMemo] = useState('');
    const [editSplitMode, setEditSplitMode] = useState(false);
    const [editSplitRows, setEditSplitRows] = useState<SplitLine[]>([]);

    const openEdit = (staged: StagedTransaction) => {
        setEditing(staged);
        // A match not edited yet starts from your entry as it is now
        const entry = (staged.match_type === 'MATCH' || staged.match_type === 'NEAR') && !staged.edited
            ? staged.match_proposal_details : null;
        setEditName(entry ? entry.description : staged.name);
        setEditSubcategory(entry ? entry.subcategory : staged.subcategory ?? null);
        setEditMemo(entry ? entry.personal_memo || '' : staged.personal_memo || '');
        const lines = entry
            ? entry.splits.map(line => ({ ...line, subcategory_name: null as string | null }))
            : staged.splits || [];
        setEditSplitMode(lines.length > 0);
        setEditSplitRows(lines.map((line, index) => ({
            id: index + 1, subcategory: line.subcategory, amount: Number(line.amount), memo: line.memo || '', subcategory_name: line.subcategory_name || '',
        })));
    };

    const saveEdit = async () => {
        if (!editing) return;
        const amount = Number(editing.amount);
        if (editSplitMode) {
            if (editSplitRows.some(row => !row.subcategory)) { notify('Choose a category for every split line.'); return; }
            if (!isSplitValid(amount, editSplitRows)) { notify(`The split lines do not add up to the transaction. Remaining: ${formatCurrency(splitRemaining(amount, editSplitRows))}`); return; }
        }
        const body = {
            name: editName,
            personal_memo: editMemo,
            subcategory: editSplitMode ? null : editSubcategory,
            splits: editSplitMode ? editSplitRows.map(row => ({ subcategory: row.subcategory, amount: Number(row.amount).toFixed(2), memo: row.memo || '' })) : [],
        };
        const res = await apiFetch(`/api/import/inbox/${editing.id}/`, {
            method: 'PATCH',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(body),
        });
        const data = await res.json().catch(() => null);
        if (res.ok && data) {
            setInbox(prev => prev.map(tx => (tx.id === editing.id ? { ...tx, ...data } : tx)));
            setEditing(null);
        } else {
            const reason = data?.splits || data?.error || data?.name || data?.detail;
            notify(`Could not save: ${Array.isArray(reason) ? reason.join(' ') : reason || `error ${res.status}`}`);
        }
    };

    // 1. Fetch Inbox
    const fetchInbox = async () => {
        setLoading(true);
        try {
            const res = await apiFetch('/api/import/inbox/');
            if (res.ok) {
                const data = await res.json();
                const sorted = data.sort((a: StagedTransaction, b: StagedTransaction) => {
                    const priority = { 'NEW': 1, 'MATCH': 2, 'NEAR': 3, 'DUPE': 4 };
                    return (priority[a.match_type] ?? 9) - (priority[b.match_type] ?? 9);
                });
                setInbox(sorted);
            }
        } catch (err) { console.error("Failed to load inbox", err); } 
        finally { setLoading(false); }
    };

    const fetchProblems = () => {
        apiFetch('/api/import/problems/').then(res => (res.ok ? res.json() : [])).then(setProblems).catch(console.error);
        apiFetch('/api/import/files/').then(res => (res.ok ? res.json() : [])).then(setFiles).catch(console.error);
    };

    // "right" as shown, or "reverse" a file's amounts; kept with its account
    const answerSigns = async (id: number, action: 'right' | 'reverse') => {
        await apiFetch(`/api/import/files/${id}/signs/`, {
            method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ action }),
        });
        handleMasterRefresh();
    };

    const dismissProblem = async (id: number) => {
        await apiFetch(`/api/import/problems/${id}/dismiss/`, { method: 'POST' });
        fetchProblems();
    };

    // --- Refresh everything on the page ---
    const handleMasterRefresh = () => {
        fetchInbox();
        fetchProblems();
        setRefreshKey(prev => prev + 1); // Forces the ParkedAccountsBanner to remount
    };

    // 2. Clear All Duplicates
    const handleClearDuplicates = async () => {
        if (!(await askConfirm({
            title: 'Clear all duplicates?',
            message: "They're already in your ledger; only these copies waiting here are removed.",
            confirmLabel: 'Clear duplicates',
        }))) return;
        
        try {
            const res = await apiFetch('/api/import/inbox/clear-duplicates/', { 
                method: 'POST', 
            });
            
            if (res.ok) {
                handleMasterRefresh();
            }
        } catch (err) { notify("Failed to clear duplicates"); }
    };

    // 3. Approve
    // asNew: add the bank's transaction as a new one, ignoring a proposed match
    const handleApprove = async (stagedId: number, manualMatchId?: number, asNew = false) => {
        try {
            const payee = manualMatchId ? linkPayee : (payeeChoice[stagedId] ?? 'entry');
            const payload = manualMatchId ? { manual_match_id: manualMatchId, payee } : asNew ? { as_new: true } : { payee };
            const res = await apiFetch(`/api/import/inbox/${stagedId}/approve/`, { 
                method: 'POST', 
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify(payload),
            });
            if (res.ok) {
                setInbox(prev => prev.filter(tx => tx.id !== stagedId));
                setIsLinkModalOpen(false); 
            } else {
                // Say why, then reload so a row the server re-flagged
                // (e.g. as a duplicate) shows its new status
                const data = await res.json().catch(() => null);
                notify(data?.error || `Could not approve this transaction (error ${res.status}).`);
                fetchInbox();
            }
        } catch (err) { notify("Error approving transaction"); }
    };

    // 4. Reject
    const handleReject = async (id: number) => {
        if (!(await askConfirm({
            title: 'Discard this row?',
            message: "It won't be added to your ledger. Importing the same file again would bring it back.",
            confirmLabel: 'Discard', danger: true, dontAskAgain: 'import-discard',
        }))) return;
        try {
            const res = await apiFetch(`/api/import/inbox/${id}/reject/`, { method: 'POST' });
            if (res.ok) setInbox(prev => prev.filter(tx => tx.id !== id));
        } catch (err) { notify("Error rejecting"); }
    };

    const openLinkModal = async (staged: StagedTransaction) => {
        setActiveStagedTx(staged);
        setLinkPayee('entry');
        setIsLinkModalOpen(true);
        setPendingCandidates([]);
        try {
            const res = await apiFetch(`/api/transactions/?account=${staged.account}&status=P`);
            if (res.ok) setPendingCandidates(await res.json());
        } catch (err) { console.error("Failed to load candidates", err); }
    };

    useEffect(() => {
        fetchInbox();
        fetchProblems();
        apiFetch('/api/categories/').then(res => (res.ok ? res.json() : [])).then(setCategories).catch(console.error);
    }, []);

    const formatCurrency = (val: number) => new Intl.NumberFormat('en-US', { style: 'currency', currency: 'USD' }).format(val);
    
    // Check if we have duplicates to show the button
    const hasDuplicates = inbox.some(tx => tx.match_type === 'DUPE');

    return (
        <div className="space-y-6 h-full">
            {/* --- HEADER --- */}
            <div className="flex justify-between items-end border-b border-gray-800 pb-4">
                {/* Left Side: Title & Stats */}
                <div>
                    <h1 className="text-3xl font-bold text-white tracking-tight">Import Inbox</h1>
                    <div className="flex items-center gap-3 mt-1">
                        <p className="text-gray-400 text-sm">
                            {loading ? "Refreshing..." : `${inbox.length} items waiting.`}
                        </p>
                        <button 
                            onClick={handleMasterRefresh} 
                            className={`text-gray-400 hover:text-white p-1.5 bg-gray-800 rounded-lg border border-gray-700 transition-colors ${loading ? 'animate-spin text-white' : ''}`}
                            title="Refresh Inbox"
                        >
                            <ArrowPathIcon className="w-4 h-4" />
                        </button>
                    </div>
                </div>

                {/* Right Side: Actions */}
                <div className="flex space-x-3">
                    {hasDuplicates && (
                        <button 
                            onClick={handleClearDuplicates}
                            className="flex items-center px-4 py-2 bg-red-900/30 text-red-400 border border-red-800 rounded-lg hover:bg-red-900/50 transition-colors text-sm font-medium"
                        >
                            <TrashIcon className="w-4 h-4 mr-2" />
                            Clear Duplicates
                        </button>
                    )}
                </div>
            </div>

            {/* Files held for an account the app does not know yet */}
            {/* We pass the refreshKey here. When it changes, React completely rebuilds the component! */}
            <ParkedAccountsBanner key={refreshKey} onResolved={handleMasterRefresh} />

            {/* The files waiting: which way their amounts go */}
            {files.length > 0 && (
                <div className="bg-gray-900 rounded-xl border border-gray-800 p-4 space-y-3">
                    <h2 className="text-sm font-bold text-gray-300">Files waiting</h2>
                    {files.map(f => (
                        <div key={f.id} className="space-y-2">
                            <div className="flex flex-wrap items-center gap-3 text-sm">
                                <span className="text-white font-medium">{f.filename}</span>
                                <span className="text-gray-500">{f.account || 'account not picked yet'} · {f.rows} row{f.rows === 1 ? '' : 's'}</span>
                                <button onClick={() => answerSigns(f.id, 'reverse')} className="ml-auto text-xs font-bold px-2 py-1 rounded border border-gray-700 text-gray-300 hover:text-white"
                                    title="Money in becomes money out and the other way round, for every row of this file">
                                    Reverse signs
                                </button>
                            </div>
                            {f.ask && f.example ? (
                                <div className="bg-amber-900/20 border border-amber-500/40 rounded-lg p-3 text-sm text-amber-100 flex flex-wrap items-center gap-3">
                                    <span className="flex-1 min-w-[16rem]">
                                        Check one row: <span className="font-bold text-white">{f.example.name}</span> shows as{' '}
                                        <span className={`font-mono font-bold ${Number(f.example.amount) < 0 ? 'text-red-300' : 'text-green-300'}`}>{formatCurrency(Number(f.example.amount))}</span>
                                        {' '}(money {Number(f.example.amount) < 0 ? 'out' : 'in'}). Is that right? Your answer is kept for this account's next files.
                                    </span>
                                    <button onClick={() => answerSigns(f.id, 'right')} className="px-3 py-1.5 bg-green-700 hover:bg-green-600 text-white text-xs font-bold rounded-lg">Yes, that's right</button>
                                    <button onClick={() => answerSigns(f.id, 'reverse')} className="px-3 py-1.5 bg-gray-700 hover:bg-gray-600 text-white text-xs font-bold rounded-lg">No, reverse the signs</button>
                                </div>
                            ) : f.card_mostly_money_in && (
                                <p className="text-xs text-amber-300">Most of this card file is money in, which is unusual for a card. If purchases show as money in, reverse the signs.</p>
                            )}
                        </div>
                    ))}
                </div>
            )}

            {/* Files that couldn't be imported (moved aside, so they aren't tried again) */}
            {problems.length > 0 && (
                <div className="bg-amber-900/20 border border-amber-500/40 rounded-xl p-4 space-y-3">
                    <h2 className="font-bold text-amber-300 flex items-center">
                        <ExclamationTriangleIcon className="w-5 h-5 mr-2" />
                        {problems.length === 1 ? "A file couldn't be imported" : `${problems.length} files couldn't be imported`}
                    </h2>
                    {problems.map(p => (
                        <div key={p.id} className="flex items-start gap-3 text-sm">
                            <div className="flex-1">
                                <p className="text-white font-medium">{p.filename} <span className="text-xs text-gray-500 font-normal">{new Date(p.when).toLocaleString()}</span></p>
                                <p className="text-amber-100/90">{p.problem}</p>
                            </div>
                            <button onClick={() => dismissProblem(p.id)} className="text-gray-400 hover:text-white p-1" title="Put away" aria-label={`Put away the message about ${p.filename}`}>
                                <XMarkIcon className="w-5 h-5" />
                            </button>
                        </div>
                    ))}
                </div>
            )}

            {/* --- DROPZONE --- */}
            <div className="w-full" data-tour="import-dropzone">
                <FileDropzone onUploadComplete={() => {
                    // Changed from 2000 to 7000 to outlast the watcher.py's 5 second sleep
                    setTimeout(() => {
                        console.log("File uploaded and processed, refreshing UI...");
                        handleMasterRefresh(); 
                    }, 7000);
                }} />
            </div>

            {/* --- TABLE --- */}
            <div data-tour="import-table" className="bg-gray-900 rounded-xl border border-gray-800 overflow-hidden shadow-xl">
                {inbox.length === 0 ? (
                    <div className="p-12 text-center text-gray-500">All caught up!</div>
                ) : (
                    <div className="overflow-x-auto">
                        <table className="w-full text-left text-sm text-gray-400">
                            <thead className="bg-gray-950 text-xs uppercase text-gray-500 font-bold">
                                <tr>
                                    <th className="px-6 py-4">Date</th>
                                    <th className="px-6 py-4">Account</th>
                                    <th className="px-6 py-4">Bank Description</th>
                                    <th className="px-6 py-4 text-right whitespace-nowrap w-px">Amount</th>
                                    <th className="px-6 py-4">Status</th>
                                    <th className="px-6 py-4 text-center">Actions</th>
                                </tr>
                            </thead>
                            <tbody className="divide-y divide-gray-800">
                                {inbox.map((tx) => (
                                    <tr
                                        key={tx.id}
                                        // Click a row to edit it before importing (not duplicates)
                                        onClick={tx.match_type !== 'DUPE' ? () => openEdit(tx) : undefined}
                                        onKeyDown={tx.match_type !== 'DUPE' ? (e) => { if (e.key === 'Enter') openEdit(tx); } : undefined}
                                        tabIndex={tx.match_type !== 'DUPE' ? 0 : undefined}
                                        title={tx.match_type !== 'DUPE' ? 'Click to edit or split before importing' : undefined}
                                        className={`hover:bg-gray-800/40 transition-colors group ${tx.match_type !== 'DUPE' ? 'cursor-pointer outline-none focus:bg-gray-800/60' : ''}`}
                                    >
                                        <td className="px-6 py-4 font-mono whitespace-nowrap">{tx.date}</td>
                                        <td className="px-6 py-4"><span className="bg-gray-800 text-gray-300 px-2 py-1 rounded text-xs border border-gray-700">{tx.account_name}</span></td>
                                        <td className="px-6 py-4 text-white font-medium">
                                            {tx.name}
                                            {(tx.match_type === 'NEW' || ((tx.match_type === 'MATCH' || tx.match_type === 'NEAR') && tx.edited)) && (
                                                <div className="text-xs mt-1 font-normal">
                                                    {tx.match_type !== 'NEW' && <span className="text-purple-300 font-bold">Your edits, applied to your entry: {tx.name} · </span>}
                                                    {(tx.splits?.length ?? 0) > 0
                                                        ? <span className="text-purple-400">Split: {tx.splits!.map(line => `${line.subcategory_name} ${formatCurrency(Number(line.amount))}`).join(', ')}</span>
                                                        : tx.subcategory_name
                                                            ? <span className="text-gray-400">{tx.subcategory_name}</span>
                                                            : <span className="text-amber-300">Uncategorized</span>}
                                                    {tx.personal_memo && <span className="text-gray-500"> · {tx.personal_memo}</span>}
                                                </div>
                                            )}
                                            {tx.match_type === 'DUPE' && tx.match_reason && (
                                                <div className="text-xs text-gray-500 mt-1">{tx.match_reason}</div>
                                            )}
                                            {tx.match_type === 'MATCH' && (
                                                <div className="text-xs text-blue-400 mt-1 flex items-center">
                                                    <LinkIcon className="w-3 h-3 mr-1" /> Matches: "{tx.match_proposal_name}"
                                                    {tx.match_reason && <span className="text-gray-500 ml-1">({tx.match_reason})</span>}
                                                </div>
                                            )}
                                            {tx.match_type === 'NEAR' && tx.match_proposal_details && (() => {
                                                const entry = tx.match_proposal_details;
                                                const keep = payeeChoice[tx.id] ?? 'entry';
                                                // A payee cell is also the switch for which payee to keep
                                                const payeeCell = (choice: PayeeChoice, name: string) => tx.edited ? <span className="text-gray-300">{name}</span> : (
                                                    <button
                                                        type="button"
                                                        onClick={(e) => { e.stopPropagation(); setPayeeChoice(prev => ({ ...prev, [tx.id]: choice })); }}
                                                        title={keep === choice ? 'This payee will be kept' : 'Keep this payee instead'}
                                                        className={`flex items-center gap-1.5 text-left px-1.5 py-0.5 -mx-1.5 rounded border ${keep === choice ? 'border-blue-500 bg-blue-900/30 text-white' : 'border-transparent text-gray-400 hover:border-gray-600'}`}
                                                    >
                                                        <span className={`inline-block w-2.5 h-2.5 rounded-full border shrink-0 ${keep === choice ? 'bg-blue-500 border-blue-400' : 'border-gray-500'}`} />
                                                        {name}
                                                    </button>
                                                );
                                                const differs = (a: string | number, b: string | number) => String(a) !== String(b) ? 'text-yellow-300 font-bold' : '';
                                                return (
                                                    <div className="mt-2 text-xs font-normal border border-yellow-800/60 bg-yellow-900/10 rounded-lg p-3 max-w-xl">
                                                        <p className="text-yellow-400 mb-2">
                                                            Possibly the same as a transaction you entered: {tx.match_reason}.
                                                            Matching keeps your entry's category and memo, and takes the bank's date and amount.
                                                        </p>
                                                        <table className="w-full">
                                                            <thead className="text-gray-500 uppercase">
                                                                <tr><th className="text-left pr-3"></th><th className="text-left pr-3">Your entry</th><th className="text-left">From the bank</th></tr>
                                                            </thead>
                                                            <tbody className="text-gray-300">
                                                                <tr><td className="text-gray-500 pr-3">Date</td><td className={`pr-3 font-mono whitespace-nowrap ${differs(tx.date, entry.date)}`}>{entry.date}</td><td className={`font-mono whitespace-nowrap ${differs(tx.date, entry.date)}`}>{tx.date}</td></tr>
                                                                <tr><td className="text-gray-500 pr-3 align-top pt-1">Payee{!tx.edited && <div className="text-[10px] normal-case text-gray-600">pick one to keep</div>}</td><td className="pr-3 py-1 align-top">{payeeCell('entry', entry.description)}</td><td className="py-1 align-top">{payeeCell('bank', tx.name)}</td></tr>
                                                                <tr><td className="text-gray-500 pr-3">Amount</td><td className={`pr-3 font-mono whitespace-nowrap ${differs(Number(tx.amount).toFixed(2), Number(entry.amount).toFixed(2))}`}>{formatCurrency(Number(entry.amount))}</td><td className={`font-mono whitespace-nowrap ${differs(Number(tx.amount).toFixed(2), Number(entry.amount).toFixed(2))}`}>{formatCurrency(tx.amount)}</td></tr>
                                                                <tr><td className="text-gray-500 pr-3">Category</td><td className="pr-3">{entry.subcategory_name || 'none'}{entry.is_split && ' (split)'}</td><td className="text-gray-500">-</td></tr>
                                                                {entry.personal_memo && <tr><td className="text-gray-500 pr-3">Memo</td><td className="pr-3">{entry.personal_memo}</td><td className="text-gray-500">-</td></tr>}
                                                            </tbody>
                                                        </table>
                                                    </div>
                                                );
                                            })()}
                                        </td>
                                        <td className={`px-6 py-4 text-right font-mono font-bold whitespace-nowrap w-px ${tx.amount < 0 ? 'text-white' : 'text-green-400'}`}>{formatCurrency(tx.amount)}</td>
                                        <td className="px-6 py-4">
                                            {tx.match_type === 'MATCH' ? (
                                                <span className="inline-flex items-center px-2 py-1 rounded-md text-xs font-bold bg-blue-900/30 text-blue-400 border border-blue-800">Match Found</span>
                                            ) : tx.match_type === 'NEAR' ? (
                                                <span className="inline-flex items-center px-2 py-1 rounded-md text-xs font-bold bg-yellow-900/30 text-yellow-400 border border-yellow-700">Near Match</span>
                                            ) : tx.match_type === 'NEW' ? (
                                                <span className="inline-flex items-center px-2 py-1 rounded-md text-xs font-bold bg-green-900/30 text-green-400 border border-green-800">New Entry</span>
                                            ) : (
                                                <span className="inline-flex items-center px-2 py-1 rounded-md text-xs font-bold bg-gray-700/30 text-gray-400 border border-gray-600">
                                                    <ExclamationTriangleIcon className="w-3 h-3 mr-1" /> Duplicate
                                                </span>
                                            )}
                                        </td>
                                        <td className="px-6 py-4 text-center">
                                            <div className="flex justify-center items-center space-x-2" onClick={(e) => e.stopPropagation()}>
                                                <button onClick={() => openLinkModal(tx)} className="p-2 text-gray-500 hover:text-blue-400 hover:bg-blue-900/20 rounded-lg transition-colors" title="Manually Link">
                                                    <MagnifyingGlassIcon className="w-5 h-5" />
                                                </button>
                                                <button onClick={() => handleReject(tx.id)} className="p-2 text-gray-500 hover:text-red-400 hover:bg-red-900/20 rounded-lg">
                                                    <TrashIcon className="w-5 h-5" />
                                                </button>
                                                {(tx.match_type === 'MATCH' || tx.match_type === 'NEAR') && (
                                                    <button onClick={() => handleApprove(tx.id, undefined, true)} className="p-2 text-gray-500 hover:text-green-400 hover:bg-green-900/20 rounded-lg" title="Not the same: add the bank's transaction as a new one">
                                                        <DocumentPlusIcon className="w-5 h-5" />
                                                    </button>
                                                )}
                                                {tx.match_type !== 'DUPE' && (
                                                    <button
                                                        onClick={() => handleApprove(tx.id)}
                                                        title={tx.match_type === 'NEAR' ? 'Match these two' : tx.match_type === 'MATCH' ? 'Accept the match' : 'Add to your ledger'}
                                                        className={`p-2 rounded-lg text-white shadow-lg ${tx.match_type === 'MATCH' ? 'bg-blue-600 hover:bg-blue-500' : tx.match_type === 'NEAR' ? 'bg-yellow-600 hover:bg-yellow-500' : 'bg-green-600 hover:bg-green-500'}`}
                                                    >
                                                        {tx.match_type === 'NEAR' ? <LinkIcon className="w-5 h-5" /> : <CheckIcon className="w-5 h-5" />}
                                                    </button>
                                                )}
                                            </div>
                                        </td>
                                    </tr>
                                ))}
                            </tbody>
                        </table>
                    </div>
                )}
            </div>

            {/* --- EDIT BEFORE IMPORTING --- */}
            {editing && (
                <div className="fixed inset-0 bg-black/80 backdrop-blur-sm flex items-center justify-center z-50 p-4">
                    <div className="bg-gray-900 border border-gray-700 rounded-2xl shadow-2xl w-full max-w-lg overflow-hidden flex flex-col max-h-[90vh]">
                        <div className="p-6 border-b border-gray-800 flex justify-between items-center bg-gray-950">
                            <div>
                                <h3 className="text-lg font-bold text-white">Edit Before Importing</h3>
                                <p className="text-xs text-gray-400 mt-1">
                                    <span className="font-mono">{editing.date}</span> · {editing.account_name} · <span className="font-mono text-white">{formatCurrency(Number(editing.amount))}</span>
                                </p>
                            </div>
                            <button onClick={() => setEditing(null)} className="text-gray-500 hover:text-white"><XMarkIcon className="w-6 h-6" /></button>
                        </div>
                        <div className="p-6 space-y-4 overflow-y-auto">
                            <div>
                                <label htmlFor="stagedPayee" className="block text-xs font-bold text-gray-500 uppercase mb-1">Payee</label>
                                <input id="stagedPayee" type="text" value={editName} onChange={e => setEditName(e.target.value)} className="w-full bg-gray-800 border-gray-700 rounded-lg text-white p-2" />
                                {editing.bank_memo && <p className="text-xs text-gray-500 mt-1">Bank memo: {editing.bank_memo}</p>}
                            </div>
                            <CategoryAllocation
                                amount={Number(editing.amount)}
                                subcategory={editSubcategory}
                                onSubcategoryChange={setEditSubcategory}
                                splitMode={editSplitMode}
                                onSplitModeChange={setEditSplitMode}
                                splitRows={editSplitRows}
                                onSplitRowsChange={setEditSplitRows}
                                categories={categories}
                                selectId="stagedSubcategory"
                            />
                            <div>
                                <label htmlFor="stagedMemo" className="block text-xs font-bold text-gray-500 uppercase mb-1">Memo</label>
                                <textarea id="stagedMemo" value={editMemo} onChange={e => setEditMemo(e.target.value)} rows={2} className="w-full bg-gray-800 border-gray-700 rounded-lg text-white p-2" />
                            </div>
                            <p className="text-xs text-gray-500">
                                The date and amount are the bank's and cannot be changed here.
                                {editing.match_type === 'NEW'
                                    ? ' Your changes are used when you approve this transaction.'
                                    : ' When you approve the match, these replace your entry\'s payee, category, memo and split lines.'}
                            </p>
                        </div>
                        <div className="p-4 border-t border-gray-800 bg-gray-950 flex justify-end gap-3">
                            <button onClick={() => setEditing(null)} className="px-4 py-2 text-gray-400 hover:text-white">Cancel</button>
                            <button onClick={saveEdit} className="px-5 py-2 bg-blue-600 hover:bg-blue-500 text-white font-bold rounded-lg">Save</button>
                        </div>
                    </div>
                </div>
            )}

            {/* --- MANUAL MATCH MODAL (Unchanged) --- */}
            {isLinkModalOpen && activeStagedTx && (
                <div className="fixed inset-0 bg-black/80 backdrop-blur-sm flex items-center justify-center z-50 p-4">
                    <div className="bg-gray-900 border border-gray-700 rounded-2xl shadow-2xl w-full max-w-2xl overflow-hidden flex flex-col max-h-[80vh]">
                        <div className="p-6 border-b border-gray-800 flex justify-between items-center bg-gray-950">
                            <div>
                                <h3 className="text-lg font-bold text-white">Link Transaction</h3>
                                <p className="text-xs text-gray-400 mt-1">
                                    Select the pending transaction that matches: <span className="text-white font-mono">{formatCurrency(activeStagedTx.amount)}</span> at <span className="text-white">{activeStagedTx.name}</span>
                                </p>
                                <div className="flex flex-wrap items-center gap-2 mt-2 text-xs">
                                    <span className="text-gray-500">Payee to keep:</span>
                                    {([['entry', "Your entry's"], ['bank', `The bank's (${activeStagedTx.name})`]] as const).map(([choice, label]) => (
                                        <button key={choice} type="button" onClick={() => setLinkPayee(choice)}
                                            className={`px-2 py-0.5 rounded border max-w-[20rem] truncate ${linkPayee === choice ? 'bg-blue-600 border-blue-500 text-white' : 'bg-gray-800 border-gray-700 text-gray-400 hover:text-white'}`}>
                                            {label}
                                        </button>
                                    ))}
                                </div>
                            </div>
                            <button onClick={() => setIsLinkModalOpen(false)} className="text-gray-500 hover:text-white">
                                <XMarkIcon className="w-6 h-6" />
                            </button>
                        </div>
                        <div className="overflow-y-auto flex-1 p-2">
                            {pendingCandidates.length === 0 ? (
                                <div className="p-8 text-center text-gray-500"><p>No pending transactions found.</p></div>
                            ) : (
                                <table className="w-full text-left text-sm text-gray-400">
                                    <thead className="text-xs uppercase bg-gray-800 text-gray-500 sticky top-0">
                                        <tr><th className="px-4 py-3">Date</th><th className="px-4 py-3">Description</th><th className="px-4 py-3 text-right whitespace-nowrap w-px">Amount</th><th className="px-4 py-3 text-center">Action</th></tr>
                                    </thead>
                                    <tbody className="divide-y divide-gray-800">
                                        {pendingCandidates.map(candidate => (
                                            <tr key={candidate.id} className="hover:bg-blue-900/10 group transition-colors">
                                                <td className="px-4 py-3 font-mono whitespace-nowrap">{candidate.date}</td>
                                                <td className="px-4 py-3 text-white">{candidate.description || candidate.name}</td>
                                                <td className="px-4 py-3 text-right font-mono whitespace-nowrap w-px">{formatCurrency(candidate.amount)}</td>
                                                <td className="px-4 py-3 text-center">
                                                    <button onClick={() => handleApprove(activeStagedTx.id, candidate.id)} className="bg-blue-600 hover:bg-blue-500 text-white px-3 py-1 rounded text-xs font-bold shadow-lg shadow-blue-900/20">Link</button>
                                                </td>
                                            </tr>
                                        ))}
                                    </tbody>
                                </table>
                            )}
                        </div>
                    </div>
                </div>
            )}
        </div>
    );
};

export default ImportReview;