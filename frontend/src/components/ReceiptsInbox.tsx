// Copyright (C) 2026 free-the-software
// SPDX-License-Identifier: AGPL-3.0-or-later
// This file is part of Save More Money, under the GNU AGPL v3 or later: see LICENSE.
import { useState, useEffect, useRef } from 'react';
import FileDropzone from '../components/FileDropzone';
import { Link } from 'react-router';
import { apiFetch } from '../api';
import { askConfirm, notify } from './Dialogs';

// Configuration
// Same-origin: Vite (dev) and Traefik (prod) both route /api and /media to
// Django, so the session cookie rides along with every request.
const API_URL = '';

interface Receipt {
    id: number;
    image: string | null;
    image_expired_at?: string | null;
    total_amount: number;
    tax_amount?: number; 
    date: string;
    status: string;
    vendor_name?: string;
    approved: boolean;
    // The transaction this receipt is linked to, and whether its tax can be split out
    linked_transaction?: {
        id: number;
        date: string;
        description: string;
        amount: string;
        account_name: string | null;
        subcategory_name: string | null;
        already_split: boolean;
        split_blocked_by: string | null;
    } | null;
}

interface Transaction {
    id: number;
    date: string;
    description: string;
    amount: number;
    account_name?: string | null;
}

const ReceiptInbox = () => {
    const [receipts, setReceipts] = useState<Receipt[]>([]);
    const [loading, setLoading] = useState(true);
    const [selectedReceipt, setSelectedReceipt] = useState<Receipt | null>(null);
    const [error, setError] = useState<string | null>(null);
    
    // Linking State
    const [searchTerm, setSearchTerm] = useState('');
    const [potentialMatches, setPotentialMatches] = useState<Transaction[]>([]);
    // Transactions the server thinks could belong to the selected, unlinked receipt
    const [suggestions, setSuggestions] = useState<Transaction[]>([]);

    // --- Editing the amounts ---
    const [editTax, setEditTax] = useState<string>('');
    const [editTotal, setEditTotal] = useState<string>('');

    // Set when the receipt reader isn't answering: what to do about it, and
    // whether the add-on is installed at all
    const [readerOff, setReaderOff] = useState<string | null>(null);
    const [readerInstalled, setReaderInstalled] = useState(true);
    // Receipt photos in the dropzone that the reader hasn't got to yet
    const [waiting, setWaiting] = useState(0);

    useEffect(() => {
        fetchReceipts();
        apiFetch(`${API_URL}/api/receipts/reader/`)
            .then(res => res.ok ? res.json() : null)
            .then(state => {
                setReaderOff(state && !state.available ? state.how_to_turn_on : null);
                setReaderInstalled(state?.installed !== false);
                setWaiting(state?.waiting || 0);
            })
            .catch(() => undefined);
    }, []);

    // Receipts are matched to transactions when they are scanned; this only
    // reloads the list.
    const fetchReceipts = async () => {
        setLoading(true);
        try {
            const res = await apiFetch(`${API_URL}/api/receipts/`);
            if (!res.ok) throw new Error(`Server error: ${res.status}`);
            const data = await res.json();
            const results: Receipt[] = Array.isArray(data) ? data : data.results || [];
            setReceipts(results);
            return results;
        } catch (error) {
            console.error("Failed to fetch receipts:", error);
            setError("Failed to load receipts.");
        } finally {
            setLoading(false);
        }
    };

   // --- Save Edited Amounts ---
    const updateAmounts = async () => {
        if (!selectedReceipt) return;
        try {
            // 1. Save the new amounts to the database
            const res = await apiFetch(`${API_URL}/api/receipts/${selectedReceipt.id}/`, {
                method: 'PATCH',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    tax_amount: parseFloat(editTax) || 0,
                    total_amount: parseFloat(editTotal) || 0
                })
            });
            
            if (res.ok) {
                // 2. Pull the completely fresh list of receipts
                const freshRes = await apiFetch(`${API_URL}/api/receipts/`);
                const freshData = await freshRes.json();
                const results = Array.isArray(freshData) ? freshData : freshData.results || [];

                // 3. Update the left column list
                setReceipts(results);

                // 4. Update the right panel with the saved amounts
                const updatedReceipt = results.find((r: Receipt) => r.id === selectedReceipt.id);
                if (updatedReceipt) {
                    // Possible matches are looked up again with the corrected total
                    showReceipt(updatedReceipt);
                }
            } else {
                notify("Failed to update amounts.");
            }
        } catch (e) {
            console.error("Update failed", e);
        }
    };

    // Shows a receipt in the right-hand panel with its amounts in the boxes
    const showReceipt = (receipt: Receipt) => {
        setSelectedReceipt(receipt);
        setEditTax(receipt.tax_amount?.toString() || '');
        setEditTotal(receipt.total_amount?.toString() || '');
        setPotentialMatches([]);
        setSearchTerm('');
        loadSuggestions(receipt);
    };

    // For a receipt with no transaction yet: ask the server for likely ones.
    // The receipt's total only helps to find it; the bank's amount is what counts.
    const suggestionNumber = useRef(0);
    const loadSuggestions = async (receipt: Receipt) => {
        const thisRequest = ++suggestionNumber.current;
        setSuggestions([]);
        if (receipt.status === 'MATCHED') return;
        try {
            const res = await apiFetch(`${API_URL}/api/receipts/${receipt.id}/suggestions/`);
            if (!res.ok) return;
            const data = await res.json();
            // Ignore an answer for a receipt the user has already moved on from
            if (thisRequest === suggestionNumber.current) setSuggestions(Array.isArray(data) ? data : []);
        } catch (e) {
            console.error("Loading possible matches failed", e);
        }
    };

    // Search runs on the server and returns only the few rows shown here.
    // (It used to download every transaction on each keystroke.)
    const searchTimer = useRef<number | undefined>(undefined);
    const searchNumber = useRef(0);

    const searchTransactions = (term: string) => {
        setSearchTerm(term);
        window.clearTimeout(searchTimer.current);
        const query = term.trim();
        const thisSearch = ++searchNumber.current;
        if (query.length < 3) {
            setPotentialMatches([]);
            return;
        }
        // Wait for a pause in typing before asking the server
        searchTimer.current = window.setTimeout(async () => {
            try {
                const res = await apiFetch(`${API_URL}/api/transactions/?search=${encodeURIComponent(query)}&page=1&page_size=5`);
                if (!res.ok) return;
                const data = await res.json();
                // Ignore an answer that arrives after a newer search has started
                if (thisSearch !== searchNumber.current) return;
                setPotentialMatches(Array.isArray(data) ? data : data.results || []);
            } catch (e) {
                console.error("Search failed", e);
            }
        }, 250);
    };

    const linkReceipt = async (receiptId: number, transactionId: number) => {
        const res = await apiFetch(`${API_URL}/api/receipts/${receiptId}/link_transaction/`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ transaction_id: transactionId })
        });
        
        if (res.ok) {
            // Reload, so the panel shows the transaction that was just linked
            const results = await fetchReceipts();
            const linked = results?.find(r => r.id === receiptId);
            if (linked) showReceipt(linked);
        } else {
            notify("Failed to link.");
        }
    };

    // Approving never changes a transaction unless splitTax is true, and then
    // it uses the tax amount shown in the box, which the user has just reviewed.
    const approveReceipt = async (id: number, splitTax: boolean) => {
        try {
            const body = {
                split_tax: splitTax,
                // Whatever is in the boxes now is what gets approved and saved
                tax_amount: parseFloat(editTax) || 0,
                total_amount: parseFloat(editTotal) || 0,
            };
            const res = await apiFetch(`${API_URL}/api/receipts/${id}/approve/`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(body),
            });
            if (res.ok) {
                setReceipts(prev => prev.filter(r => r.id !== id));
                setSelectedReceipt(null);
            } else {
                const data = await res.json().catch(() => null);
                notify(data?.error || "Failed to approve receipt.");
            }
        } catch (e) {
            console.error("Approval failed", e);
        }
    };

    // Removes a receipt that is stuck or was never a real receipt. The server
    // only allows this for receipts still in the inbox, which have changed
    // nothing in the ledger.
    const deleteReceipt = async (receipt: Receipt) => {
        const linked = receipt.linked_transaction;
        const name = receipt.image ? receipt.image.split('/').pop() : `receipt ${receipt.id}`;
        if (!(await askConfirm({
            title: 'Delete this receipt permanently?',
            message: `${name}\n\n` +
                `Deleted: the receipt and its image in the app.\n` +
                `Not changed: ${linked ? `the transaction it is linked to (${linked.description})` : 'any transaction'}.\n\n` +
                `This cannot be undone. To scan it again, upload the image again.`,
            confirmLabel: 'Delete', danger: true,
        }))) return;
        try {
            const res = await apiFetch(`${API_URL}/api/receipts/${receipt.id}/`, { method: 'DELETE' });
            if (res.ok) {
                setReceipts(prev => prev.filter(r => r.id !== receipt.id));
                setSelectedReceipt(null);
            } else {
                const data = await res.json().catch(() => null);
                notify(data?.error || data?.detail || "Failed to delete receipt.");
            }
        } catch (e) {
            console.error("Delete failed", e);
            notify("Failed to delete receipt.");
        }
    };

    const getImageUrl = (path: string | null) => {
        if (!path) return '';
        // Django returns an absolute URL; keep only the path so the image
        // loads through the same origin as the app.
        if (path.startsWith('http')) path = new URL(path).pathname;

        let cleanPath = path.startsWith('/') ? path : `/${path}`;
        if (!cleanPath.startsWith('/media')) {
            cleanPath = `/media${cleanPath}`;
        }
        return `${API_URL}${cleanPath}`; 
    };

    if (error) return <div className="text-red-500 p-10">{error}</div>;

    return (
        <div className="space-y-4 h-screen flex flex-col p-4">
            <div className="flex justify-between items-end">
                <h1 className="text-3xl font-bold text-white">Receipt Inbox ({receipts.length})</h1>
                {/* Refresh */}
                <button 
                    onClick={fetchReceipts}
                    className="bg-gray-800 hover:bg-gray-700 text-gray-300 px-4 py-2 rounded-lg text-sm font-bold border border-gray-600 transition-colors"
                >
                    Refresh
                </button>
            </div>
            
            {waiting > 0 && (
                <p className="text-sm text-amber-200 bg-amber-900/20 border border-amber-500/40 rounded-lg px-3 py-2">
                    {waiting === 1 ? '1 receipt photo is' : `${waiting} receipt photos are`} waiting for the reader.{' '}
                    {!readerInstalled ? "They'll be read once the reader is added." : readerOff ? "They're read as soon as it answers again." : 'They should be read within a minute or two.'}
                </p>
            )}

            {readerOff && !readerInstalled && (
                <div className="bg-blue-900/20 border border-blue-500/40 rounded-xl p-4 text-sm text-blue-100 space-y-2">
                    <p className="font-bold text-blue-300">The receipt reader is an optional add-on, and isn't installed</p>
                    <p>
                        It reads photos of receipts (store, date, total, tax) and suggests the transaction each belongs to.
                        It's <span className="font-bold">in beta</span>: it often misreads receipts, so treat what it reads as a suggestion. Nothing changes until you approve it.
                    </p>
                    <p>
                        It needs about <span className="font-bold">2.5 GB more disk</span>, about <span className="font-bold">2 GB more memory</span> while
                        reading (released after 10 minutes without a receipt), and a first build of 10 minutes or more. It runs on your own computer: receipts are never sent anywhere.
                    </p>
                    <p>
                        To add it, an admin runs one command on the computer the app runs on: <Link to="/settings?tab=storage" className="underline text-blue-300 hover:text-white">Settings → Storage</Link> shows it, ready to copy.
                    </p>
                </div>
            )}

            {readerOff && readerInstalled && (
                <div className="bg-amber-900/20 border border-amber-500/40 rounded-xl p-4 text-sm text-amber-100">
                    <p className="font-bold text-amber-300 mb-1">The receipt reader isn't running</p>
                    <p>{readerOff.replace("The receipt reader isn't answering. ", '')}</p>
                    <p className="text-xs text-amber-200/70 mt-2">Receipts already here can still be matched and approved.</p>
                </div>
            )}

            {loading ? <p className="text-gray-500">Loading...</p> : (
                <div className="grid grid-cols-1 md:grid-cols-3 gap-6 flex-1 min-h-0">
                    
                    {/* LEFT COL: DROPZONE + LIST */}
                    <div className="md:col-span-1 flex flex-col h-full min-h-0">
                        <div data-tour="receipt-dropzone" className="mb-4 shrink-0">
                            <FileDropzone onUploadComplete={() => setTimeout(fetchReceipts, 2000)} />
                        </div>
                        <div className="space-y-4 overflow-y-auto pr-2 flex-1">
                            {receipts.length === 0 && <p className="text-gray-500 italic">Inbox zero! No pending receipts.</p>}
                            {receipts.map(r => (
                                <div 
                                    key={r.id} 
                                    onClick={() => showReceipt(r)}
                                    className={`p-4 rounded-xl border cursor-pointer transition-all ${selectedReceipt?.id === r.id ? 'bg-blue-900/30 border-blue-500' : 'bg-gray-800 border-gray-700 hover:border-gray-500'}`}
                                >
                                    <div className="flex justify-between items-start mb-2">
                                        <span className={`text-xs font-bold px-2 py-1 rounded ${r.status === 'MATCHED' ? 'bg-green-900 text-green-400 border border-green-700' : 'bg-yellow-900 text-yellow-500 border border-yellow-700'}`}>
                                            {r.status}
                                        </span>
                                        <span className="text-sm font-mono text-gray-300">{r.date || 'No Date'}</span>
                                    </div>
                                    <div className="flex justify-between items-center">
                                        <div className="font-bold text-white text-lg">${r.total_amount || '0.00'}</div>
                                    </div>
                                    <div className="text-sm text-gray-400 truncate mt-1">
                                        {r.image ? r.image.split('/').pop() : r.image_expired_at ? 'Image expired' : 'No Image'}
                                    </div>
                                </div>
                            ))}
                        </div>
                    </div>

                    {/* RIGHT COL: PREVIEW */}
                    <div className="md:col-span-2 bg-gray-900 border border-gray-800 rounded-xl p-6 flex flex-col h-full">
                        {selectedReceipt ? (
                            <div className="h-full flex flex-col">
                                
                                {/* Editable amounts */}
                                <div className="flex justify-between items-end mb-4 px-4 bg-gray-800 p-3 rounded-xl border border-gray-700 shrink-0">
                                    <div className="flex gap-6">
                                        <div>
                                            <label className="block text-gray-400 text-xs uppercase font-bold mb-1">Tax Amount</label>
                                            <div className="flex items-center bg-gray-900 border border-gray-600 rounded-lg px-3 focus-within:border-blue-500 transition-colors">
                                                <span className="text-gray-500">$</span>
                                                <input 
                                                    type="number" 
                                                    step="0.01"
                                                    value={editTax} 
                                                    onChange={(e) => setEditTax(e.target.value)} 
                                                    className="bg-transparent text-white w-20 p-2 outline-none font-mono" 
                                                />
                                            </div>
                                        </div>
                                        <div>
                                            <label className="block text-gray-400 text-xs uppercase font-bold mb-1">Total Amount</label>
                                            <div className="flex items-center bg-gray-900 border border-gray-600 rounded-lg px-3 focus-within:border-blue-500 transition-colors">
                                                <span className="text-gray-500 font-bold">$</span>
                                                <input 
                                                    type="number" 
                                                    step="0.01"
                                                    value={editTotal} 
                                                    onChange={(e) => setEditTotal(e.target.value)} 
                                                    className="bg-transparent text-white text-lg font-bold w-28 p-1.5 outline-none font-mono" 
                                                />
                                            </div>
                                        </div>
                                    </div>
                                    <div className="flex items-center gap-3">
                                        <button 
                                            onClick={updateAmounts} 
                                            className="bg-blue-600 hover:bg-blue-500 text-white px-5 py-2.5 rounded-lg font-bold text-sm transition-colors shadow-lg"
                                        >
                                            Save Amounts
                                        </button>
                                        <button
                                            onClick={() => deleteReceipt(selectedReceipt)}
                                            title="Delete this receipt from the inbox"
                                            className="bg-gray-700 hover:bg-red-700 text-gray-200 hover:text-white px-4 py-2.5 rounded-lg font-bold text-sm transition-colors"
                                        >
                                            Delete Receipt
                                        </button>
                                    </div>
                                </div>

                                {/* ACTION AREA: TOGGLES BASED ON STATUS */}
                                {selectedReceipt.status === 'MATCHED' ? ((() => {
                                    const linked = selectedReceipt.linked_transaction;
                                    const tax = parseFloat(editTax) || 0;
                                    const amount = linked ? Math.abs(parseFloat(linked.amount)) : 0;
                                    const money = (value: number) => value.toLocaleString('en-US', { style: 'currency', currency: 'USD' });
                                    // The server decides whether a split is possible; this is only what to show
                                    const blocked = !linked ? 'This receipt is not linked to a transaction.'
                                        : tax <= 0 ? null
                                        : tax >= amount ? 'The tax amount is as large as the whole transaction. Check the amounts.'
                                        : linked.already_split ? 'This transaction is already split.'
                                        : !linked.subcategory_name ? 'This transaction has no category yet. Give it one first.'
                                        : null;
                                    const canSplit = !!linked && tax > 0 && !blocked;
                                    return (
                                    <div className="bg-green-900/20 border border-green-700 p-5 rounded-xl shrink-0 mb-6 space-y-4">
                                        <div>
                                            <h3 className="text-green-400 font-bold text-lg">Review and approve</h3>
                                            {linked && (
                                                <p className="text-gray-300 text-sm">
                                                    Linked to <span className="font-bold text-white">{linked.description}</span>,{' '}
                                                    {money(parseFloat(linked.amount))} on {linked.date}
                                                    {linked.account_name && <> ({linked.account_name})</>}
                                                    , category <span className="text-white">{linked.subcategory_name || 'none'}</span>.
                                                </p>
                                            )}
                                            <p className="text-gray-400 text-xs mt-1">
                                                The total and tax above were read from the image and can be wrong. Check them against the receipt,
                                                correct them in the boxes if needed, then approve. Approving saves the amounts shown.
                                                Nothing changes in your ledger unless you choose to split the tax.
                                            </p>
                                            {linked && Math.abs((parseFloat(editTotal) || 0) - amount) > 0.005 && (
                                                <p className="text-yellow-400 text-xs mt-1">
                                                    The receipt shows {money(parseFloat(editTotal) || 0)} and the bank charged {money(amount)}.
                                                    The bank's amount is the one your ledger keeps. Check that this is the right transaction.
                                                </p>
                                            )}
                                        </div>

                                        {canSplit && linked && (
                                            <div className="bg-gray-900/60 border border-gray-700 rounded-lg p-3 text-sm text-gray-300">
                                                <p className="font-bold text-white mb-1">Splitting the tax would turn this one transaction into two parts:</p>
                                                <p>{money(tax)} to <span className="text-white">Sales Tax</span></p>
                                                <p>{money(amount - tax)} stays in <span className="text-white">{linked.subcategory_name}</span></p>
                                            </div>
                                        )}
                                        {tax > 0 && blocked && (
                                            <p className="text-sm text-yellow-400">The tax cannot be split out: {blocked}</p>
                                        )}

                                        <div className="flex flex-wrap gap-3">
                                            {canSplit && (
                                                <button
                                                    onClick={() => approveReceipt(selectedReceipt.id, true)}
                                                    className="bg-green-600 hover:bg-green-500 text-white font-bold py-3 px-6 rounded-lg shadow-lg transition-all"
                                                >
                                                    Approve & split {money(tax)} tax
                                                </button>
                                            )}
                                            <button
                                                onClick={() => approveReceipt(selectedReceipt.id, false)}
                                                className={`${canSplit ? 'bg-gray-700 hover:bg-gray-600' : 'bg-green-600 hover:bg-green-500'} text-white font-bold py-3 px-6 rounded-lg shadow-lg transition-all`}
                                            >
                                                {canSplit ? 'Approve without splitting' : 'Approve & Archive'}
                                            </button>
                                        </div>
                                    </div>
                                    );
                                })()
                                ) : (
                                    <div className="bg-gray-800 p-4 rounded-xl shrink-0 mb-6">
                                        <h3 className="text-sm font-bold text-gray-400 uppercase mb-2">Link to Transaction (Orphaned)</h3>
                                        {suggestions.length > 0 && (
                                            <div className="mb-4">
                                                <p className="text-xs text-gray-400 mb-2">
                                                    Possible matches: purchases near the receipt's date for its total or a little more. Click one to link it.
                                                </p>
                                                <div className="space-y-2">
                                                    {suggestions.map(tx => (
                                                        <div key={tx.id} className="flex justify-between items-center p-3 hover:bg-gray-700 rounded-lg cursor-pointer border border-blue-800" onClick={() => linkReceipt(selectedReceipt.id, tx.id)}>
                                                            <div>
                                                                <div className="text-white font-bold">{tx.description}</div>
                                                                <div className="text-xs text-gray-500">{tx.date}{tx.account_name && <> · {tx.account_name}</>}</div>
                                                            </div>
                                                            <div className="font-mono text-green-400">${tx.amount}</div>
                                                        </div>
                                                    ))}
                                                </div>
                                            </div>
                                        )}
                                        <input 
                                            type="text"
                                            className="w-full bg-gray-900 border border-gray-600 rounded-lg p-3 text-white focus:border-blue-500 outline-none"
                                            placeholder="Search by amount or merchant..."
                                            value={searchTerm}
                                            onChange={(e) => searchTransactions(e.target.value)}
                                        />
                                        
                                        <div className="mt-2 space-y-2 max-h-40 overflow-y-auto">
                                            {potentialMatches.map(tx => (
                                                <div key={tx.id} className="flex justify-between items-center p-3 hover:bg-gray-700 rounded-lg cursor-pointer border border-gray-700" onClick={() => linkReceipt(selectedReceipt.id, tx.id)}>
                                                    <div>
                                                        <div className="text-white font-bold">{tx.description}</div>
                                                        <div className="text-xs text-gray-500">{tx.date}</div>
                                                    </div>
                                                    <div className="font-mono text-green-400">${tx.amount}</div>
                                                </div>
                                            ))}
                                        </div>
                                    </div>
                                )}

                                {/* IMAGE VIEWER */}
                                <div className="flex-1 overflow-hidden relative rounded-lg border border-gray-700 bg-black flex items-center justify-center"> 
                                    {selectedReceipt.image ? (
                                        <img 
                                            src={getImageUrl(selectedReceipt.image)}
                                            className="max-w-full max-h-full object-contain"
                                            alt="Receipt"
                                        />
                                    ) : selectedReceipt.image_expired_at ? (
                                        <p className="text-gray-500 text-center px-6">
                                            Image expired on {new Date(selectedReceipt.image_expired_at).toLocaleDateString()} under your retention policy.
                                            <br />The receipt's details are kept.
                                        </p>
                                    ) : <p className="text-gray-500">No Image</p>}
                                </div>
                            </div>
                        ) : (
                            <div className="h-full flex items-center justify-center text-gray-600">
                                Select a receipt to review
                            </div>
                        )}
                    </div>
                </div>
            )}
        </div>
    );
};

export default ReceiptInbox;