// Copyright (C) 2026 free-the-software
// SPDX-License-Identifier: AGPL-3.0-or-later
// This file is part of Save More Money, under the GNU AGPL v3 or later: see LICENSE.
import { Fragment, useEffect, useState, useRef, useCallback } from 'react';
import { useNavigate } from 'react-router';
import { 
    PaperClipIcon, 
    PlusCircleIcon, 
    TrashIcon, 
    LinkIcon, 
    XMarkIcon, 
    MagnifyingGlassIcon,
    ExclamationTriangleIcon
} from '@heroicons/react/24/outline';
import api from '../api';
import { useTour } from './Tour';
import CategoryAllocation, { splitRemaining as splitRemainingOf, isSplitValid as splitAddsUp } from './CategoryAllocation';
import { askConfirm, notify } from './Dialogs';

// --- INTERFACES ---
interface SubCategory { id: number; name: string; }
interface Category { id: number; name: string; subcategories: SubCategory[]; }
interface Account { account_id: number; name: string; }
interface Split { id: number; amount: number; subcategory_name: string; memo: string; subcategory?: number; }
interface Transaction {
    id: number; date: string; description: string; personal_memo: string; amount: number; status: string;
    account_name: string; subcategory_name: string; type: string; account?: number; subcategory?: number | null;
    is_manual?: boolean; fitid?: string; splits: Split[]; receipt_image?: string | null; receipt_expired?: { expired_at: string; date: string | null; total_amount: string | null; tax_amount: string | null } | null; linked_transaction?: number | null; linked_transaction_account?: string | null;
}
interface RuleCondition { field: string; operator: string; value: string; }

const STATUS_LABELS: Record<string, string> = { P: 'Pending', C: 'Cleared', R: 'Reconciled' };
const STATUS_COLOURS: Record<string, string> = { P: 'text-yellow-500', C: 'text-green-400', R: 'text-blue-400' };

const TransactionList = () => {
    const navigate = useNavigate();

    const [transactions, setTransactions] = useState<Transaction[]>([]);
    const [categories, setCategories] = useState<Category[]>([]);
    const [subcategories, setSubcategories] = useState<SubCategory[]>([]);
    const tour = useTour();
    const [accounts, setAccounts] = useState<Account[]>([]);
    
    // --- PAGINATION STATES ---
    const [loading, setLoading] = useState(true);
    const [isFetchingMore, setIsFetchingMore] = useState(false);
    const [page, setPage] = useState(1);
    const [hasMore, setHasMore] = useState(true);
    const [searchTerm, setSearchTerm] = useState('');
    
    // --- FILTER STATES (Pre-filled from BudgetView if applicable) ---
    // A subcategory's id, or 'none' for uncategorized transactions
    const [filterSubcatId, setFilterSubcatId] = useState<number | 'none' | null>(() => {
        const val = sessionStorage.getItem('pendingFilterSubcat');
        return val ? Number(val) : null;
    });
    const [filterAccountId, setFilterAccountId] = useState<number | null>(null);
    // 'P' shows only pending transactions, wherever they sit in the register
    const [filterStatus, setFilterStatus] = useState<string>('');
    const [totalCount, setTotalCount] = useState<number | null>(null);
    const [filterMonth, setFilterMonth] = useState<string | null>(() => {
        return sessionStorage.getItem('pendingFilterMonth') || null;
    });
    const [filterYear, setFilterYear] = useState<string | null>(() => {
        return sessionStorage.getItem('pendingFilterYear') || null;
    });
    
    // Modal States
    const [isRecurring, setIsRecurring] = useState(false);
    // New recurring entries made from this form repeat monthly
    const frequency = 'MONTHLY';
    const [isModalOpen, setIsModalOpen] = useState(false);
    const [isDeleteConfirm, setIsDeleteConfirm] = useState(false);
    const [editingTx, setEditingTx] = useState<Transaction | null>(null);
    const [formData, setFormData] = useState<Partial<Transaction>>({});
    
    // Rule Wizard State
    const [isRuleModalOpen, setIsRuleModalOpen] = useState(false);
    const [isPreviewModalOpen, setIsPreviewModalOpen] = useState(false);
    const [matches, setMatches] = useState<Transaction[]>([]);
    const [selectedMatchIds, setSelectedMatchIds] = useState<number[]>([]);
    const [ruleFormData, setRuleFormData] = useState({
        rule_name: '', conditions: [] as RuleCondition[], logic_operator: 'AND', clean_merchant_name: '', subcategory: 0, apply_to_history: false
    });

    // --- LINK MODAL STATE ---
    const [isLinkModalOpen, setIsLinkModalOpen] = useState(false);
    const [isLoadingCandidates, setIsLoadingCandidates] = useState(false);
    const [linkCandidates, setLinkCandidates] = useState<Transaction[]>([]);

    // Split State
    const [isSplitMode, setIsSplitMode] = useState(false);
    const [splitRows, setSplitRows] = useState<Split[]>([]);
    const splitRemaining = splitRemainingOf(Number(formData.amount) || 0, splitRows);
    const isSplitValid = splitAddsUp(Number(formData.amount) || 0, splitRows);

    // --- HELPERS ---
    
    const addRuleCondition = () => setRuleFormData(prev => ({ ...prev, conditions: [...prev.conditions, { field: 'name', operator: 'contains', value: '' }] }));
    const removeRuleCondition = (index: number) => { const newConds = [...ruleFormData.conditions]; newConds.splice(index, 1); setRuleFormData(prev => ({ ...prev, conditions: newConds })); };
    const updateRuleCondition = (index: number, key: keyof RuleCondition, val: string) => {
        const newConds = [...ruleFormData.conditions];
        const updated = { ...newConds[index], [key]: val };
        // Amounts and text have different comparisons: keep one that fits the field
        if (key === 'field') {
            const allowed = val === 'amount' ? ['exact', 'gt', 'lt'] : ['contains', 'exact', 'startswith', 'endswith'];
            if (!allowed.includes(updated.operator)) updated.operator = val === 'amount' ? 'exact' : 'contains';
        }
        newConds[index] = updated;
        setRuleFormData(prev => ({ ...prev, conditions: newConds }));
    };

    // --- INITIAL LOAD ---
    useEffect(() => {
        api.get('/subcategories/').then(res => setSubcategories(res.data)).catch(console.error);
        api.get('/accounts/').then(res => setAccounts(res.data)).catch(console.error);
        api.get('/categories/').then(res => setCategories(res.data)).catch(console.error);
    }, []);

    // --- BULLETPROOF SESSION INTERCEPTOR ---
    useEffect(() => {
        const pendingTxRaw = sessionStorage.getItem('pendingEditTx');
        
        if (pendingTxRaw) {
            try {
                const tx = JSON.parse(pendingTxRaw);
                console.log("🚦 [Session Interceptor] Opening modal for:", tx.id);
                
                // Immediately wipe the storage so it doesn't reopen on a page refresh
                sessionStorage.removeItem('pendingEditTx');
                sessionStorage.removeItem('pendingFilterMonth');
                sessionStorage.removeItem('pendingFilterYear');
                sessionStorage.removeItem('pendingFilterSubcat');
                
                // Hydrate the modal
                setEditingTx(tx);
                setFormData(tx);
                
                if ((tx.splits?.length ?? 0) > 0) {
                    setIsSplitMode(true);
                    setSplitRows(tx.splits);
                } else {
                    setIsSplitMode(false);
                    setSplitRows([]);
                }
                
                setIsModalOpen(true);
            } catch (e) {
                console.error("Failed to parse pending transaction", e);
            }
        }
    }, []); 

    // --- OBSERVER ---
    const observer = useRef<IntersectionObserver | null>(null);
    const lastRowRef = useCallback((node: HTMLTableRowElement) => {
        if (loading || isFetchingMore) return;
        if (observer.current) observer.current.disconnect();
        observer.current = new IntersectionObserver(entries => {
            if (entries[0].isIntersecting && hasMore) setPage(prevPage => prevPage + 1);
        }, { threshold: 0.1, rootMargin: '400px' });
        if (node) observer.current.observe(node);
    }, [loading, isFetchingMore, hasMore]);

    // --- FETCH DATA ---
    const fetchData = async (pageNum = 1) => {
        if (pageNum === 1) setLoading(true);
        else setIsFetchingMore(true);
        try {
            const txRes = await api.get('/transactions/', {
                params: { 
                    page: pageNum, 
                    account: filterAccountId || undefined, 
                    subcategory: filterSubcatId || undefined, 
                    search: searchTerm || undefined,
                    status: filterStatus || undefined,
                    month: filterMonth || undefined,
                    year: filterYear || undefined
                }
            });
            setTotalCount(txRes.data.count ?? null);
            if (pageNum === 1) setTransactions(txRes.data.results);
            else setTransactions(prev => [...prev, ...txRes.data.results]);
            setHasMore(txRes.data.next !== null);
        } catch (err) { 
            console.error("Error loading data:", err); 
        } finally { 
            setLoading(false); 
            setIsFetchingMore(false); 
        }
    };

    // Refetch when filters change
    useEffect(() => {
        const delay = setTimeout(() => { setPage(1); fetchData(1); }, 300);
        return () => clearTimeout(delay);
    }, [searchTerm, filterAccountId, filterSubcatId, filterStatus, filterMonth, filterYear]);

    useEffect(() => { if (page > 1) fetchData(page); }, [page]);

    // --- SAFE CLOSE MODAL ---
    const closeModal = () => {
        setIsModalOpen(false); setIsDeleteConfirm(false); setIsRuleModalOpen(false); setIsPreviewModalOpen(false);
        setEditingTx(null); setFormData({}); setRuleFormData(prev => ({ ...prev, apply_to_history: false }));
        setIsSplitMode(false); setSplitRows([]); setIsRecurring(false);
        setIsLinkModalOpen(false);
    };

    // --- ROW CLICK HANDLER ---
    const handleRowClick = (tx: Transaction) => {
        setEditingTx(tx); 
        setFormData(tx); 
        if ((tx.splits?.length ?? 0) > 0) { 
            setIsSplitMode(true); 
            setSplitRows(tx.splits); 
        } else { 
            setIsSplitMode(false); 
            setSplitRows([]); 
        } 
        setIsModalOpen(true);
    };

    // --- AUTO-PROPOSE LINK LOGIC ---
    const openLinkModal = async () => {
        if (!editingTx) return;
        setIsLinkModalOpen(true);
        setIsLoadingCandidates(true);
        setLinkCandidates([]);
        
        try {
            const res = await api.get(`/transactions/${editingTx.id}/proposed_links/`);
            setLinkCandidates(res.data);
        } catch (err) {
            console.error("Failed to load candidates", err);
        } finally {
            setIsLoadingCandidates(false);
        }
    };

    const handleManualLink = async (targetId: number) => {
        try {
            await api.post('/transactions/link/', { source_id: editingTx?.id, target_id: targetId });
            setIsLinkModalOpen(false);
            closeModal();
            fetchData(1);
        } catch (err: any) { 
            notify(`Failed to link: ${err.response?.data?.error || err.message}`); 
        }
    };

    const handleUnlink = async (id: number) => {
        if (!(await askConfirm({
            title: 'Unlink this transfer?',
            message: 'The two transactions stay in your ledger, but no longer count as one transfer between your accounts.',
            confirmLabel: 'Unlink',
        }))) return;
        try {
            await api.post(`/transactions/${id}/unlink/`);
            fetchData(1);
        } catch (err) { 
            console.error("Unlink error:", err);
            notify("Failed to unlink."); 
        }
    };

    // --- RULE LOGIC ---
    const handlePreviewMatches = async () => {
        try {
            const response = await api.post('/transactions/preview-rule/', { conditions: ruleFormData.conditions, logic_operator: ruleFormData.logic_operator });
            setMatches(response.data); 
            setSelectedMatchIds(response.data.map((m: any) => m.id)); 
            setIsPreviewModalOpen(true);
        } catch (err) { 
            console.error("Preview match error:", err);
            notify("Server error. Check Django logs."); 
        }
    };

    const submitFinalRule = async () => {
        try {
            await api.post('/transactions/create-rule/', { ...ruleFormData, selected_ids: selectedMatchIds });
            closeModal(); 
            fetchData(1);
        } catch (err: any) { 
            notify(`Error: ${JSON.stringify(err.response?.data || err.message)}`); 
        }
    };

    const toggleMatchSelection = (id: number) => setSelectedMatchIds(prev => prev.includes(id) ? prev.filter(i => i !== id) : [...prev, id]);

    // --- SAVE / DELETE LOGIC ---
    const handleSave = async () => {
        if (!formData.account) { notify("Please select an Account."); return; }
        if (isSplitMode && !isSplitValid) { notify(`Split total does not match transaction amount! Remaining: ${formatCurrency(splitRemaining)}`); return; }
        const url = editingTx ? `/transactions/${editingTx.id}/` : `/transactions/`;
        const txnPayload = { ...formData, is_manual: editingTx ? undefined : true, splits: isSplitMode ? splitRows : [] };
        
        try {
            if (editingTx) {
                await api.patch(url, txnPayload);
            } else {
                await api.post(url, txnPayload);
            }

            if (isRecurring) {
                try { 
                    await api.post('/recurring/', { name: formData.description, amount: formData.amount, account: formData.account, subcategory: formData.subcategory, frequency: frequency, start_date: formData.date, create_days_before: 7, is_active: true }); 
                } catch (recErr) {
                    console.error("Failed to save recurring:", recErr);
                }
            }
            closeModal(); fetchData(1);
        } catch (err: any) { notify(`Failed to save: ${JSON.stringify(err.response?.data || err.message)}`); }
    };

    const handleDelete = async () => {
        if (!editingTx) return;
        try { 
            await api.delete(`/transactions/${editingTx.id}/`); 
            closeModal(); 
            fetchData(1); 
        } catch (err) { 
            console.error("Delete error:", err);
            notify("Failed to delete transaction."); 
        }
    };

    const formatCurrency = (val: number) => new Intl.NumberFormat('en-US', { style: 'currency', currency: 'USD' }).format(val);

    // Today's date where the user is, as YYYY-MM-DD (toISOString would give UTC's date)
    const now = new Date();
    const today = `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, '0')}-${String(now.getDate()).padStart(2, '0')}`;

    // --- RENDER HELPERS ---
    // No category, no split lines and not a transfer: needs one
    const isUncategorized = (tx: Transaction) =>
        !tx.linked_transaction && !tx.subcategory_name && (tx.splits?.length ?? 0) === 0;

    const renderSubcategoryCell = (tx: Transaction) => {
        if (tx.linked_transaction) {
            return (
                <button 
                    onClick={(e) => { e.stopPropagation(); handleUnlink(tx.id); }}
                    className="bg-blue-900/30 hover:bg-red-900/30 text-blue-400 hover:text-red-400 border border-blue-500/30 hover:border-red-500/50 px-2 py-1 rounded text-xs font-bold inline-flex items-center transition-colors group"
                    title="Unlink Transfer"
                >
                    <LinkIcon className="w-3 h-3 mr-1 stroke-[3] group-hover:hidden" />
                    <XMarkIcon className="w-3 h-3 mr-1 stroke-[3] hidden group-hover:block" />
                    {tx.linked_transaction_account}
                </button>
            );
        }
        if ((tx.splits?.length ?? 0) > 0) {
            return <span className="text-purple-400 text-xs font-bold">Split ({tx.splits.length})</span>;
        }
        if (isUncategorized(tx)) {
            return (
                <span className="inline-flex items-center bg-amber-500/15 text-amber-300 border border-amber-500/60 px-2 py-0.5 rounded text-xs font-bold whitespace-nowrap">
                    <ExclamationTriangleIcon className="w-3.5 h-3.5 mr-1" /> Uncategorized
                </span>
            );
        }
        return <span className="text-gray-400">{tx.subcategory_name}</span>;
    };

    const renderModalContent = () => {
        if (isDeleteConfirm) {
            return (
                <div className="p-8 text-center space-y-4">
                    <h3 className="text-xl font-bold text-white">Are you sure?</h3>
                    <div className="flex justify-center space-x-4 mt-8 pt-4">
                        <button onClick={() => setIsDeleteConfirm(false)} className="px-6 py-2 bg-green-600 rounded-lg text-white font-bold">Cancel</button>
                        <button onClick={handleDelete} className="px-6 py-2 bg-red-600 rounded-lg text-white font-bold">Yes, Delete</button>
                    </div>
                </div>
            );
        }

        return (
            <>
                <div className="p-6 space-y-4 text-left">
                    <div className="grid grid-cols-2 gap-4">
                        <div>
                            <label htmlFor="txDate" className="block text-xs font-bold text-gray-500 uppercase mb-1">Date</label>
                            <input id="txDate" type="date" value={formData.date?.split('T')[0] || ''} onChange={e => setFormData({ ...formData, date: e.target.value })} className="w-full bg-gray-800 border-gray-700 rounded-lg text-white p-2" />
                        </div>
                        <div>
                            <label htmlFor="txAmount" className="block text-xs font-bold text-gray-500 uppercase mb-1">Amount</label>
                            <input id="txAmount" type="number" step="0.01" value={formData.amount || ''} onChange={e => setFormData({ ...formData, amount: Number.parseFloat(e.target.value) })} className="w-full bg-gray-800 border-gray-700 rounded-lg text-white p-2" />
                        </div>
                    </div>
                    <div className="grid grid-cols-2 gap-4">
                        <div>
                            <label htmlFor="txAccount" className="block text-xs font-bold text-gray-500 uppercase mb-1">Account</label>
                            <select id="txAccount" value={formData.account?.toString() || ''} onChange={e => setFormData({ ...formData, account: Number(e.target.value) })} className="w-full bg-gray-800 border-gray-700 rounded-lg text-white p-2">
                                <option value="">Select Account</option>
                                {accounts.map(acc => <option key={acc.account_id} value={acc.account_id}>{acc.name}</option>)}
                            </select>
                        </div>
                        <div>
                            <label htmlFor="txStatus" className="block text-xs font-bold text-gray-500 uppercase mb-1">Status</label>
                            <select id="txStatus" value={formData.status || 'P'} onChange={e => setFormData({ ...formData, status: e.target.value })} className="w-full bg-gray-800 border-gray-700 rounded-lg text-white p-2">
                                {Object.entries(STATUS_LABELS).map(([code, label]) => <option key={code} value={code}>{label}</option>)}
                            </select>
                        </div>
                    </div>
                    {/* Only pending ones are matched when the bank file comes in */}
                    {editingTx?.status === 'P' && formData.status !== 'P' && !editingTx.fitid && (
                        <p className="text-sm text-amber-300">
                            <span className="font-bold">WARNING!</span> Entered by hand and not yet matched to your bank's copy. Once it's no longer pending, the bank's copy will import as a new transaction instead of matching this one.
                        </p>
                    )}
                    
                    <CategoryAllocation
                        amount={Number(formData.amount) || 0}
                        subcategory={formData.subcategory}
                        onSubcategoryChange={(id) => setFormData({ ...formData, subcategory: id })}
                        splitMode={isSplitMode}
                        onSplitModeChange={setIsSplitMode}
                        splitRows={splitRows}
                        onSplitRowsChange={setSplitRows}
                        categories={categories}
                    />
                    <div>
                        <label htmlFor="txMerchant" className="block text-xs font-bold text-gray-500 uppercase mb-1">Merchant</label>
                        <input id="txMerchant" type="text" value={formData.description || ''} onChange={e => setFormData({ ...formData, description: e.target.value })} className="w-full bg-gray-800 border-gray-700 rounded-lg text-white p-2" />
                    </div>
                    <div>
                        <label htmlFor="txMemo" className="block text-xs font-bold text-gray-500 uppercase mb-1">Memo</label>
                        <textarea id="txMemo" value={formData.personal_memo || ''} onChange={e => setFormData({ ...formData, personal_memo: e.target.value })} className="w-full bg-gray-800 border-gray-700 rounded-lg text-white p-2 h-20" />
                    </div>
                    
                    {editingTx?.receipt_image && (
                        <div className="mt-4 p-4 bg-gray-800/50 rounded-lg border border-gray-700">
                            <span className="block text-xs font-bold text-gray-500 uppercase mb-2">Attached Receipt</span>
                            <div className="relative group cursor-pointer" onClick={() => window.open(editingTx.receipt_image!, '_blank')}>
                                <img src={editingTx.receipt_image!.replace('/app/data/media/', '/media/')} alt="Receipt" className="w-full h-48 object-cover rounded-lg border border-gray-600 group-hover:border-blue-500 transition-all" />
                            </div>
                        </div>
                    )}
                    {!editingTx?.receipt_image && editingTx?.receipt_expired && (
                        <div className="mt-4 p-4 bg-gray-800/50 rounded-lg border border-gray-700">
                            <span className="block text-xs font-bold text-gray-500 uppercase mb-2">Attached Receipt</span>
                            <p className="text-sm text-gray-300">
                                Receipt on record
                                {editingTx.receipt_expired.date && <> from {editingTx.receipt_expired.date}</>}
                                {editingTx.receipt_expired.total_amount && <>, total ${editingTx.receipt_expired.total_amount}</>}
                                {editingTx.receipt_expired.tax_amount && <>, tax ${editingTx.receipt_expired.tax_amount}</>}.
                            </p>
                            <p className="text-xs text-gray-500 mt-1">
                                Image expired on {new Date(editingTx.receipt_expired.expired_at).toLocaleDateString()} under your retention policy. The receipt's details are kept.
                            </p>
                        </div>
                    )}
                </div>
                <div className="p-6 bg-gray-800/30 flex justify-between items-center">
                    <div className="flex flex-wrap gap-2">
                        {editingTx && (
                            <>
                                <button onClick={() => setIsDeleteConfirm(true)} className="px-4 py-2 bg-red-900/50 text-red-400 border border-red-900/50 rounded-lg text-sm transition-colors hover:bg-red-900/80">Delete</button>
                                <button onClick={() => { setRuleFormData({ rule_name: formData.description || '', conditions: [{ field: 'name', operator: 'contains', value: formData.description || '' }], logic_operator: 'AND', clean_merchant_name: formData.description || '', subcategory: formData.subcategory || 0, apply_to_history: false }); setIsRuleModalOpen(true); }} className="px-4 py-2 bg-purple-900/30 text-purple-400 border border-purple-900/50 rounded-lg text-sm transition-colors hover:bg-purple-900/50">Create Rule</button>
                                
                                {!editingTx.linked_transaction && (
                                    <button 
                                        onClick={(e) => { e.preventDefault(); openLinkModal(); }} 
                                        className="px-4 py-2 bg-blue-900/30 text-blue-400 border border-blue-900/50 rounded-lg text-sm transition-colors flex items-center hover:bg-blue-900/60"
                                    >
                                        <MagnifyingGlassIcon className="w-4 h-4 mr-2 stroke-[2]" /> Find Match
                                    </button>
                                )}
                            </>
                        )}
                    </div>
                    <div className="flex space-x-3 shrink-0">
                        <button onClick={closeModal} className="px-4 py-2 text-gray-400 font-medium hover:text-white">Cancel</button>
                        <button onClick={handleSave} className="px-6 py-2 bg-blue-600 text-white font-bold rounded-lg hover:bg-blue-500 transition-colors">{editingTx ? 'Update' : 'Save'}</button>
                    </div>
                </div>
            </>
        );
    };

    return (
        <div className="space-y-6">
            <div className="flex justify-between items-end">
                <div><h1 className="text-3xl font-bold text-white tracking-tight">Transaction Register</h1><p className="text-gray-400 mt-1 text-sm">Overview of your financial activity</p></div>
                <div className="flex gap-3">
                    <button onClick={() => { 
                        setEditingTx(null);
                        setFormData({ date: new Date().toISOString().split('T')[0], amount: 0, description: '', account: accounts.length > 0 ? accounts[0].account_id : undefined, status: 'P' });
                        setIsSplitMode(false); setSplitRows([]); setIsModalOpen(true);
                    }} data-tour="add-transaction" className="bg-blue-600 hover:bg-blue-500 text-white px-4 py-2 rounded-lg text-sm font-bold transition-colors shadow-lg">+ Add Transaction</button>
                </div>
            </div>
            
            {/* A brand-new install: nothing to show yet, so say where to start */}
            {!loading && transactions.length === 0 && accounts.length === 0 && !searchTerm && !filterAccountId && !filterSubcatId && !filterStatus && (
                <div className="bg-blue-900/20 border border-blue-500/40 rounded-xl p-6 space-y-3">
                    <h2 className="text-xl font-bold text-white">Welcome! Let's get your first transactions in.</h2>
                    <ol className="list-decimal list-inside space-y-2 text-gray-300 text-sm">
                        <li>
                            <button onClick={() => navigate('/settings')} className="text-blue-400 hover:text-blue-300 font-bold underline">Add your accounts</button>
                            {' '}in Settings. For bank imports, give each the account number your bank puts in its download files.
                        </li>
                        <li>
                            Download a statement from your bank (.qfx, .ofx or .csv) and drop it in the{' '}
                            <button onClick={() => navigate('/import')} className="text-blue-400 hover:text-blue-300 font-bold underline">Import Inbox</button>.
                            A file for an account you haven't added yet is held there until you say which account it belongs to.
                        </li>
                        <li>Review and approve the imported transactions. A starter set of categories is ready in Settings; rename or add to it freely.</li>
                    </ol>
                    <p className="text-xs text-gray-500">You can also add transactions by hand with "+ Add Transaction".</p>
                    <button onClick={tour.start} className="bg-blue-600 hover:bg-blue-500 text-white px-4 py-2 rounded-lg text-sm font-bold">Take the tour</button>
                </div>
            )}

            {/* Filters */}
            <div data-tour="tx-filters" className="grid grid-cols-1 md:grid-cols-12 gap-4">
                <div className="md:col-span-3">
                    <label htmlFor="accountFilter" className="block text-xs font-medium text-gray-400 mb-1 ml-1">Account Filter</label>
                    <select id="accountFilter" value={filterAccountId || ''} onChange={(e) => setFilterAccountId(e.target.value ? Number(e.target.value) : null)} className="w-full bg-gray-800 border border-gray-700 text-gray-300 text-sm rounded-lg p-2.5 focus:ring-blue-500">
                        <option value="">All Accounts</option>
                        {accounts.map(acc => <option key={acc.account_id} value={acc.account_id}>{acc.name}</option>)}
                    </select>
                </div>
                <div className="md:col-span-4">
                    <label htmlFor="searchProduct" className="block text-xs font-medium text-gray-400 mb-1 ml-1">Search Product</label>
                    <input id="searchProduct" type="text" placeholder="Search Merchant or memo..." value={searchTerm} onChange={(e) => setSearchTerm(e.target.value)} className="w-full bg-gray-800 border border-gray-700 text-gray-300 text-sm rounded-lg p-2.5 focus:ring-blue-500" />
                </div>
                <div className="md:col-span-3">
                    <label htmlFor="subcatFilter" className="block text-xs font-medium text-gray-400 mb-1 ml-1">Subcategory Filter</label>
                    <select id="subcatFilter" value={filterSubcatId ?? ''} onChange={(e) => setFilterSubcatId(e.target.value === 'none' ? 'none' : e.target.value ? Number(e.target.value) : null)} className="w-full bg-gray-800 border border-gray-700 text-gray-300 text-sm rounded-lg p-2.5 focus:ring-blue-500">
                        <option value="">All Subcategories</option>
                        <option value="none" className="text-amber-300 bg-gray-800 font-bold">Uncategorized</option>
                        {categories.map(cat => (<optgroup key={cat.id} label={cat.name} className="text-gray-400 font-bold bg-gray-900">{cat.subcategories.map(sub => (<option key={sub.id} value={sub.id} className="text-white bg-gray-800 font-normal">{sub.name}</option>))}</optgroup>))}
                    </select>
                </div>
                <div className="md:col-span-2">
                    <label htmlFor="statusFilter" className="block text-xs font-medium text-gray-400 mb-1 ml-1">Status</label>
                    <select id="statusFilter" value={filterStatus} onChange={(e) => setFilterStatus(e.target.value)} className="w-full bg-gray-800 border border-gray-700 text-gray-300 text-sm rounded-lg p-2.5 focus:ring-blue-500">
                        <option value="">All</option>
                        <option value="P">Pending only</option>
                        <option value="C">Cleared only</option>
                        <option value="R">Reconciled only</option>
                    </select>
                </div>
            </div>

            {filterStatus === 'P' && (
                <div className="bg-yellow-900/20 border border-yellow-500/30 rounded-lg p-3 flex justify-between items-center">
                    <span className="text-sm text-yellow-400 font-medium">
                        Showing pending transactions only{totalCount !== null && !loading ? <>: <b>{totalCount}</b> found</> : null}
                    </span>
                    <button
                        onClick={() => setFilterStatus('')}
                        className="text-xs font-bold bg-yellow-600 hover:bg-yellow-500 text-white px-3 py-1 rounded"
                    >
                        Show All
                    </button>
                </div>
            )}

            {/* Timeframe Filter Indicators */}
            {(filterMonth || filterYear) && (
                <div className="bg-blue-900/20 border border-blue-500/30 rounded-lg p-3 flex justify-between items-center">
                    <span className="text-sm text-blue-400 font-medium">
                        Showing filtered view for: <b>{filterMonth ? new Date(0, Number(filterMonth) - 1).toLocaleString('default', { month: 'long' }) : ''} {filterYear}</b>
                    </span>
                    <button 
                        onClick={() => { setFilterMonth(null); setFilterYear(null); }}
                        className="text-xs font-bold bg-blue-600 hover:bg-blue-500 text-white px-3 py-1 rounded"
                    >
                        Clear Date Filter
                    </button>
                </div>
            )}

            {/* Table */}
            <div className="relative overflow-x-auto shadow-md sm:rounded-lg border border-gray-800">
                <table className="w-full text-sm text-left text-gray-400">
                    <thead className="text-xs text-gray-400 uppercase bg-gray-900/50 border-b border-gray-800">
                        <tr><th className="px-6 py-4 font-medium w-px whitespace-nowrap">Date</th><th className="px-6 py-4 font-medium w-px whitespace-nowrap">Account</th><th className="px-6 py-4 font-medium">Merchant</th><th className="px-6 py-4 font-medium text-right w-px whitespace-nowrap">Amount</th><th className="px-6 py-4 font-medium w-px whitespace-nowrap">Status</th><th className="px-6 py-4 font-medium w-px whitespace-nowrap">Type</th><th className="px-6 py-4 font-medium w-px whitespace-nowrap">Subcategory</th><th className="px-6 py-4 font-medium">Personal Memo</th><th className="px-6 py-4 font-medium text-center w-px whitespace-nowrap">Doc</th></tr>
                    </thead>
                    <tbody>
                        {loading && page === 1 ? (<tr><td colSpan={9} className="text-center py-8 text-gray-500">Loading...</td></tr>) : 
                         transactions.map((tx, index) => (
                            <Fragment key={tx.id}>
                            {/* The register runs newest first: a red line where the dates
                                drop from tomorrow-or-later to today-or-earlier */}
                            {index > 0 && transactions[index - 1].date.split('T')[0] > today && tx.date.split('T')[0] <= today && (
                                <tr aria-label="Today" className="pointer-events-none">
                                    <td colSpan={9} className="p-0">
                                        <div className="relative h-0 border-t-2 border-red-500">
                                            <span className="absolute left-4 -top-2.5 bg-red-500 text-white text-[10px] font-bold uppercase tracking-wider px-2 rounded">Today</span>
                                        </div>
                                    </td>
                                </tr>
                            )}
                            <tr 

                                onClick={() => handleRowClick(tx)} 
                                role="button"
                                tabIndex={0}
                                onKeyDown={(e) => { if (e.key === 'Enter' || e.key === ' ') handleRowClick(tx); }}
                                className={`border-b transition-colors cursor-pointer outline-none focus:ring-2 focus:ring-blue-500 focus:bg-gray-700 inset-0 ${isUncategorized(tx) ? 'bg-yellow-400/25 border-yellow-700/60 hover:bg-yellow-400/35 shadow-[inset_4px_0_0_0_rgb(250_204_21)]' : 'bg-gray-800/50 border-gray-800 hover:bg-gray-700'}`}
                            >
                                <td className="px-6 py-4 font-mono text-gray-300 whitespace-nowrap">{tx.date ? tx.date.split('T')[0] : 'N/A'}</td>
                                <td className="px-6 py-4 whitespace-nowrap"><span className="bg-gray-700 text-gray-300 text-xs font-medium px-2.5 py-0.5 rounded border border-gray-600">{tx.account_name || '—'}</span></td>
                                <td className="px-6 py-4 text-white font-medium">{tx.description || 'No Description'}</td>
                                <td className={`px-6 py-4 font-bold font-mono text-right whitespace-nowrap ${tx.amount < 0 ? 'text-red-400' : 'text-green-400'}`}>{formatCurrency(tx.amount || 0)}</td>
                                <td className="px-6 py-4 whitespace-nowrap"><span className={`${STATUS_COLOURS[tx.status] || 'text-gray-400'} text-xs font-medium`}>{STATUS_LABELS[tx.status] || tx.status}</span></td>
                                <td className={`px-6 py-4 capitalize text-xs font-bold whitespace-nowrap ${isUncategorized(tx) ? 'text-amber-300' : 'text-gray-400'}`}>{isUncategorized(tx) ? 'Uncategorized' : tx.type === 'Uncategorized' ? 'Split' : tx.type}</td>
                                <td className="px-6 py-4 whitespace-nowrap">
                                    {renderSubcategoryCell(tx)}
                                </td>
                                <td className="px-6 py-4 text-gray-500 text-xs italic truncate max-w-xs">{tx.personal_memo}</td>
                                <td className="px-6 py-4 text-center whitespace-nowrap">{tx.receipt_image ? (<PaperClipIcon className="h-5 w-5 text-indigo-400" />) : tx.receipt_expired ? (<span title="Receipt on record. Its image was removed by your retention policy."><PaperClipIcon className="h-5 w-5 text-gray-600" /></span>) : ('-')}</td>
                            </tr>
                            </Fragment>
                        ))}
                        
                        {isFetchingMore && (
                            <tr><td colSpan={9} className="text-center py-4 text-gray-500">Loading more transactions...</td></tr>
                        )}
                        
                        {hasMore && (
                            <tr ref={lastRowRef}>
                                <td colSpan={9} style={{ height: '20px' }}></td>
                            </tr>
                        )}
                    </tbody>
                </table>
            </div>

            {/* MODAL POPUP - Standard Edit/Create Transaction */}
            {isModalOpen && (
                <div className="fixed inset-0 bg-black/70 backdrop-blur-sm flex items-center justify-center z-50 p-4">
                    <div className="bg-gray-900 border border-gray-800 rounded-xl shadow-2xl w-full max-w-lg overflow-hidden max-h-[90vh] overflow-y-auto">
                        <div className="p-6 border-b border-gray-800 flex justify-between items-center sticky top-0 bg-gray-900 z-10">
                            <h2 className="text-xl font-bold text-white">{isDeleteConfirm ? 'Confirm Deletion' : (editingTx ? 'Edit Transaction' : 'New Transaction')}</h2>
                            <button onClick={closeModal} className="text-gray-500 hover:text-white">&times;</button>
                        </div>
                        {renderModalContent()}
                    </div>
                </div>
            )}

            {/* --- PROPOSED MATCHES MODAL --- */}
            {isLinkModalOpen && editingTx && (
                <div className="fixed inset-0 bg-black/80 backdrop-blur-sm flex items-center justify-center z-[80] p-4">
                    <div className="bg-gray-900 border border-blue-500/30 rounded-2xl shadow-2xl w-full max-w-2xl overflow-hidden animate-in fade-in zoom-in duration-200">
                        <div className="p-6 border-b border-gray-800 bg-gray-950 flex justify-between items-center">
                            <div>
                                <h3 className="text-lg font-bold text-white">Proposed Matches</h3>
                                <p className="text-xs text-gray-400 mt-1">Found within 5 days for the inverse of <span className="text-white font-bold">{formatCurrency(editingTx.amount)}</span></p>
                            </div>
                            <button onClick={() => setIsLinkModalOpen(false)} className="text-gray-500 hover:text-white transition-colors"><XMarkIcon className="w-6 h-6" /></button>
                        </div>
                        
                        <div className="overflow-y-auto max-h-[50vh]">
                            {isLoadingCandidates ? (
                                <div className="p-12 text-center text-blue-400 font-bold animate-pulse tracking-widest uppercase text-xs">Scanning Ledger...</div>
                            ) : linkCandidates.length === 0 ? (
                                <div className="p-12 text-center text-gray-500">
                                    <p className="font-bold text-white mb-1">No automated matches found.</p>
                                    <p className="text-sm">No inverse transactions exist within a 5-day window.</p>
                                </div>
                            ) : (
                                <table className="w-full text-left text-sm text-gray-400 relative">
                                    <thead className="text-[10px] uppercase bg-gray-950 text-gray-500 sticky top-0 shadow-sm z-10">
                                        <tr>
                                            <th className="px-6 py-3">Date</th>
                                            <th className="px-6 py-3">Account</th>
                                            <th className="px-6 py-3">Merchant</th>
                                            <th className="px-6 py-3 text-center">Action</th>
                                        </tr>
                                    </thead>
                                    <tbody className="divide-y divide-gray-800">
                                        {linkCandidates.map(c => (
                                            <tr key={c.id} className="hover:bg-blue-900/10 transition-colors">
                                                <td className="px-6 py-4 font-mono text-white">{c.date.split('T')[0]}</td>
                                                <td className="px-6 py-4">
                                                    <span className="bg-gray-800 text-gray-300 text-[10px] font-bold px-2 py-1 rounded border border-gray-700">{c.account_name}</span>
                                                </td>
                                                <td className="px-6 py-4 text-gray-300">{c.description}</td>
                                                <td className="px-6 py-4 text-center">
                                                    <button onClick={() => handleManualLink(c.id)} className="bg-blue-600 hover:bg-blue-500 text-white px-4 py-2 rounded-lg text-xs font-bold shadow-lg transition-colors">Select Match</button>
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

            {/* --- RULE MODAL (STREAMLINED) --- */}
            {isRuleModalOpen && (
                <div className="fixed inset-0 bg-black/80 backdrop-blur-md flex items-center justify-center z-[60] p-4">
                    <div className="bg-gray-900 border border-purple-500/30 rounded-2xl shadow-2xl w-full max-w-lg overflow-hidden animate-in fade-in zoom-in duration-200">
                        <div className="p-6 border-b border-gray-800 bg-purple-500/5 flex justify-between items-center">
                            <h2 className="text-xl font-bold text-white flex items-center">
                                <span className="bg-purple-500/20 p-2 rounded-lg mr-3">
                                    <PlusCircleIcon className="w-5 h-5 text-purple-400" />
                                </span>
                                Create Rule
                            </h2>
                            <div className="flex bg-gray-950 rounded-lg p-1 border border-gray-800">
                                <button onClick={() => setRuleFormData({...ruleFormData, logic_operator: 'AND'})} className={`px-3 py-1 rounded text-xs font-bold transition-colors ${ruleFormData.logic_operator === 'AND' ? 'bg-purple-600 text-white' : 'text-gray-500'}`}>AND</button>
                                <button onClick={() => setRuleFormData({...ruleFormData, logic_operator: 'OR'})} className={`px-3 py-1 rounded text-xs font-bold transition-colors ${ruleFormData.logic_operator === 'OR' ? 'bg-orange-600 text-white' : 'text-gray-500'}`}>OR</button>
                            </div>
                        </div>
                        <div className="p-6 space-y-5 text-left max-h-[60vh] overflow-y-auto">
                            <div className="grid grid-cols-2 gap-4">
                                <div>
                                    <label htmlFor="ruleName" className="text-[10px] font-black text-gray-500 uppercase tracking-widest mb-2 block">Rule Name</label>
                                    <input id="ruleName" type="text" className="w-full bg-gray-950 border border-gray-800 p-2 rounded-lg text-white text-sm" value={ruleFormData.rule_name} onChange={e => setRuleFormData({ ...ruleFormData, rule_name: e.target.value })} />
                                </div>
                                <div>
                                    <label htmlFor="ruleSubcategory" className="text-[10px] font-black text-gray-500 uppercase tracking-widest mb-2 block">Assign Category</label>
                                    <select id="ruleSubcategory" className="w-full bg-gray-950 border border-gray-800 p-2 rounded-lg text-white text-sm" value={ruleFormData.subcategory || ''} onChange={e => setRuleFormData({ ...ruleFormData, subcategory: Number(e.target.value) })}>
                                        <option value="">Select...</option>
                                        {categories.map(cat => (<optgroup key={cat.id} label={cat.name}>{cat.subcategories.map(sub => <option key={sub.id} value={sub.id}>{sub.name}</option>)}</optgroup>))}
                                    </select>
                                </div>
                            </div>
                            <div>
                                <span className="text-[10px] font-black text-gray-500 uppercase tracking-widest mb-2 block">Conditions</span>
                                <div className="space-y-2">
                                    {ruleFormData.conditions.map((cond, index) => (
                                        <div key={index} className="flex gap-2 items-center">
                                            <select aria-label="Condition Field" value={cond.field} onChange={(e) => updateRuleCondition(index, 'field', e.target.value)} className="bg-gray-800 border-gray-700 text-white text-xs rounded p-2 w-1/4">
                                                <option value="name">Merchant</option>
                                                <option value="amount">Amount</option>
                                                <option value="personal_memo">Memo</option>
                                                <option value="account">Account Name</option>
                                            </select>
                                            <select aria-label="Condition Operator" value={cond.operator} onChange={(e) => updateRuleCondition(index, 'operator', e.target.value)} className="bg-gray-800 border-gray-700 text-white text-xs rounded p-2 w-1/4">
                                                {cond.field === 'amount' ? (<>
                                                    <option value="exact">Exactly</option>
                                                    <option value="gt">Over</option>
                                                    <option value="lt">Under</option>
                                                </>) : (<>
                                                    <option value="contains">Contains</option>
                                                    <option value="exact">Exact Match</option>
                                                    <option value="startswith">Starts With</option>
                                                    <option value="endswith">Ends With</option>
                                                </>)}
                                            </select>
                                            <input aria-label="Condition Value" type="text" value={cond.value} onChange={(e) => updateRuleCondition(index, 'value', e.target.value)} className="bg-gray-800 border-gray-700 text-white text-xs rounded p-2 flex-1" placeholder="Value..." />
                                            <button aria-label="Remove Condition" onClick={() => removeRuleCondition(index)} className="text-red-400 hover:text-red-300 p-1"><TrashIcon className="w-4 h-4" /></button>
                                        </div>
                                    ))}
                                    <button onClick={addRuleCondition} className="text-xs font-bold text-purple-400 hover:text-purple-300 flex items-center mt-2">
                                        <PlusCircleIcon className="w-4 h-4 mr-1" /> Add Condition
                                    </button>
                                </div>
                            </div>
                            <div>
                                <label htmlFor="ruleRename" className="text-[10px] font-black text-gray-500 uppercase tracking-widest mb-2 block">Rename Merchant To (Optional)</label>
                                <input id="ruleRename" type="text" className="w-full bg-gray-950 border border-gray-800 p-2 rounded-lg text-green-400 font-bold text-sm" value={ruleFormData.clean_merchant_name} onChange={e => setRuleFormData({ ...ruleFormData, clean_merchant_name: e.target.value })} placeholder="Leave blank to keep original" />
                            </div>
                        </div>
                        
                        <div className="p-6 bg-gray-950/50 flex justify-between items-center border-t border-gray-800">
                            <button onClick={() => setIsRuleModalOpen(false)} className="text-gray-400 font-medium text-sm hover:text-white transition-colors">Cancel</button>
                            <div className="flex gap-3">
                                <button 
                                    onClick={submitFinalRule} 
                                    className="bg-gray-800 hover:bg-gray-700 px-4 py-2 rounded-lg text-gray-300 font-bold text-sm border border-gray-700 transition-all"
                                >
                                    Create Rule
                                </button>
                                <button 
                                    onClick={handlePreviewMatches} 
                                    className="bg-purple-600 hover:bg-purple-500 px-4 py-2 rounded-lg text-white font-bold text-sm shadow-lg transition-all"
                                >
                                    Apply to History
                                </button>
                            </div>
                        </div>
                    </div>
                </div>
            )}
            
            {/* --- PREVIEW MODAL --- */}
            {isPreviewModalOpen && (
                <div className="fixed inset-0 bg-black/90 backdrop-blur-xl flex items-center justify-center z-[70] p-4">
                    <div className="bg-gray-900 border border-blue-500/30 rounded-2xl shadow-2xl w-full max-w-4xl max-h-[85vh] flex flex-col overflow-hidden animate-in slide-in-from-bottom-4 duration-300">
                        <div className="p-8 border-b border-gray-800 flex justify-between items-center bg-blue-500/5 text-left">
                            <div><h2 className="text-2xl font-black text-white">Review Matches</h2><p className="text-gray-400 mt-1">Found {matches.length} transactions based on your conditions.</p></div>
                            <div className="text-right"><span className="text-[10px] font-black text-gray-500 uppercase block mb-1">Target Category</span><span className="bg-purple-500/20 text-purple-400 px-3 py-1 rounded-full text-xs font-bold border border-purple-500/30">{subcategories.find(s => s.id === ruleFormData.subcategory)?.name || 'Uncategorized'}</span></div>
                        </div>
                        <div className="flex-1 overflow-y-auto">
                            <table className="w-full text-sm text-left">
                                <thead className="text-[10px] uppercase bg-gray-950 text-gray-500 sticky top-0 z-10">
                                    <tr>
                                        <th className="p-6 w-10">
                                            <input aria-label="Select All Matches" type="checkbox" className="rounded border-gray-700 bg-gray-800 text-blue-600 focus:ring-0" checked={selectedMatchIds.length === matches.length && matches.length > 0} onChange={() => setSelectedMatchIds(selectedMatchIds.length === matches.length ? [] : matches.map(m => m.id))} />
                                        </th>
                                        <th className="p-4 font-black tracking-widest">Date</th>
                                        <th className="p-4 font-black tracking-widest">Account</th>
                                        <th className="p-4 font-black tracking-widest">Payee: now → after</th>
                                        <th className="p-4 font-black tracking-widest">Category: now → after</th>
                                        <th className="p-4 font-black tracking-widest text-right">Amount</th>
                                    </tr>
                                </thead>
                                <tbody className="divide-y divide-gray-800/50">
                                    {matches.map(m => (
                                        <tr key={m.id} className={`hover:bg-gray-800/30 transition-colors ${selectedMatchIds.includes(m.id) ? 'bg-blue-500/5' : ''}`}>
                                            <td className="p-6">
                                                <input aria-label={`Select transaction ${m.description}`} type="checkbox" className="rounded border-gray-700 bg-gray-800 text-blue-600 focus:ring-0" checked={selectedMatchIds.includes(m.id)} onChange={() => toggleMatchSelection(m.id)} />
                                            </td>
                                            <td className="p-4 font-mono text-gray-500 whitespace-nowrap">{m.date.split('T')[0]}</td>
                                            <td className="p-4 text-gray-400 text-xs">{m.account_name}</td>
                                            {(() => {
                                                // What the rule would change on this transaction, and what it is now
                                                const newPayee = ruleFormData.clean_merchant_name;
                                                const payeeChanges = !!newPayee && newPayee !== m.description;
                                                const newCategory = subcategories.find(sub => sub.id === ruleFormData.subcategory)?.name;
                                                const splitCount = m.splits?.length ?? 0;
                                                const currentCategory = m.linked_transaction ? 'Transfer'
                                                    : splitCount > 0 ? `Split (${splitCount})`
                                                    : m.subcategory_name || null;
                                                const categoryChanges = !!newCategory && newCategory !== m.subcategory_name;
                                                return (<>
                                                    <td className="p-4">
                                                        <div className="text-gray-300">{m.description}</div>
                                                        {payeeChanges
                                                            ? <div className="text-xs text-green-400 font-bold">→ {newPayee}</div>
                                                            : <div className="text-xs text-gray-600">no change</div>}
                                                    </td>
                                                    <td className="p-4">
                                                        <div className={currentCategory ? 'text-gray-300' : 'text-amber-300 font-bold'}>{currentCategory || 'Uncategorized'}</div>
                                                        {categoryChanges
                                                            ? <div className="text-xs text-green-400 font-bold">→ {newCategory}{splitCount > 0 && <span className="text-gray-500 font-normal"> (its split lines keep their own)</span>}</div>
                                                            : <div className="text-xs text-gray-600">no change</div>}
                                                    </td>
                                                    <td className={`p-4 text-right font-mono whitespace-nowrap ${Number(m.amount) < 0 ? 'text-gray-300' : 'text-green-400'}`}>{formatCurrency(Number(m.amount))}</td>
                                                </>);
                                            })()}
                                        </tr>
                                    ))}
                                </tbody>
                            </table>
                        </div>
                        <div className="p-8 bg-gray-950 flex justify-between items-center border-t border-gray-800">
                            <button onClick={() => setIsPreviewModalOpen(false)} className="text-gray-500 font-bold transition-colors">Go Back</button>
                            <div className="flex items-center gap-6">
                                <span className="text-sm text-gray-500 font-medium">{selectedMatchIds.length} items selected</span>
                                <button onClick={submitFinalRule} className="bg-blue-600 hover:bg-blue-500 px-10 py-4 rounded-xl text-white font-black shadow-xl shadow-blue-900/20 active:scale-95 transition-all">Apply Rule to Selection</button>
                            </div>
                        </div>
                    </div>
                </div>
            )}
        </div>
    );
};

export default TransactionList;