// Copyright (C) 2026 free-the-software
// SPDX-License-Identifier: AGPL-3.0-or-later
// This file is part of Save More Money, under the GNU AGPL v3 or later: see LICENSE.
import React, { useState, useEffect } from 'react';
import { XMarkIcon } from '@heroicons/react/24/outline';
import { useNavigate, useSearchParams } from 'react-router';
import { apiFetch } from '../api';
import { notify } from './Dialogs';
import PlanBudget from './PlanBudget';
import BudgetSuggestions from './BudgetSuggestions';
import type { QuickPlan } from './BudgetSuggestions';

// --- Types ---
interface BudgetRow {
  id: number;
  name: string;
  category_name: string;
  is_income: boolean;
  budget: number;
  actual: number;
}

interface Summary {
  income_budget: number; income_actual: number;
  expense_budget: number; expense_actual: number;
  savings_budget: number; savings_actual: number;
}

// Budget to Actual: one month, or the year so far ("year": every month of a
// past year, up to this month of this one). /budget?period=year opens the year.
const BudgetView = () => {
  const navigate = useNavigate();
  const [searchParams] = useSearchParams();
  const [year, setYear] = useState(new Date().getFullYear());
  const [month, setMonth] = useState<number | 'year'>(searchParams.get('period') === 'year' ? 'year' : new Date().getMonth() + 1);
  // years: those with transactions or budgets, through next year (from the server)
  const [data, setData] = useState<{summary: Summary, rows: BudgetRow[], first_month: number, last_month: number, years: number[]} | null>(null);
  const wholeYear = month === 'year';
  const monthName = (m: number) => new Date(0, m - 1).toLocaleString('default', { month: 'short' });
  // e.g. "Mar 2026", "Jan–Mar 2026"
  const periodLabel = !data ? '' : !wholeYear ? `${monthName(Number(month))} ${year}`
      : data.last_month === 0 ? `${year}` : `${monthName(data.first_month)}–${monthName(data.last_month)} ${year}`;
  const [loading, setLoading] = useState(false);
  const [showAll, setShowAll] = useState(false);
  const [isExpanded, setIsExpanded] = useState(true);
  // The year being set up in the Plan window, if open
  const [planning, setPlanning] = useState<number | null>(null);
  // The year whose suggested budget is being reviewed, if open
  const [suggesting, setSuggesting] = useState<number | null>(null);
  // A quick plan from the Plan window, being reviewed
  const [reviewing, setReviewing] = useState<QuickPlan | null>(null);

  // Edit Budget Modal State
  const [editOpen, setEditOpen] = useState(false);
  const [selectedRow, setSelectedRow] = useState<BudgetRow | null>(null);
  const [editAmount, setEditAmount] = useState<string>('');
  const [applyFuture, setApplyFuture] = useState(false);

  // --- Transaction list and edit window ---
  const [txListOpen, setTxListOpen] = useState(false);
  const [txList, setTxList] = useState<any[]>([]);
  const [loadingTx, setLoadingTx] = useState(false);
  const [selectedSubcat, setSelectedSubcat] = useState<BudgetRow | null>(null);

  const fetchData = async () => {
    setLoading(true);
    try {
      const res = await apiFetch(`/api/budgets/summary/?year=${year}&${wholeYear ? 'period=year' : `month=${month}`}`);
      if (res.ok) {
        const json = await res.json();
        setData(json);
      }
    } catch (err) {
      console.error(err);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => { fetchData(); }, [year, month]);

  const handleEditClick = (row: BudgetRow) => {
    if (wholeYear) {
      notify('Budgets are set month by month: pick a month above to change this one.', 'info');
      return;
    }
    setSelectedRow(row);
    setEditAmount(row.budget.toString());
    setApplyFuture(false); 
    setEditOpen(true);
  };

  const handleSave = async () => {
    if (!selectedRow) return;
    try {
        await apiFetch('/api/budgets/set-target/', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
            subcategory_id: selectedRow.id,
            year, 
            month, 
            amount: parseFloat(editAmount),
            apply_future: applyFuture
        })
        });
        setEditOpen(false);
        fetchData(); 
    } catch(e) {
        console.error("Save failed", e);
    }
  };

  // --- Fetch Transactions for "Actual" Click ---
  const handleActualClick = async (row: BudgetRow) => {
    setSelectedSubcat(row);
    setTxListOpen(true);
    setLoadingTx(true);
    try {
      // The same months as the figure clicked
      const period = wholeYear ? `until_month=${data?.last_month || 12}` : `month=${month}`;
      const res = await apiFetch(`/api/transactions/?subcategory=${row.id}&year=${year}&${period}`);
      if (res.ok) {
        const json = await res.json();
        // Handle DRF pagination just in case
        setTxList(json.results || json);
      }
    } catch (err) {
      console.error("Failed to load transactions", err);
    } finally {
      setLoadingTx(false);
    }
  };

  const handleTxClick = (tx: any) => {
    // 1. Save everything to browser memory
    sessionStorage.setItem('pendingEditTx', JSON.stringify(tx));
    if (!wholeYear) sessionStorage.setItem('pendingFilterMonth', month.toString());
    sessionStorage.setItem('pendingFilterYear', year.toString());
    
    if (selectedSubcat) {
        sessionStorage.setItem('pendingFilterSubcat', selectedSubcat.id.toString());
    }

    // 2. Perform a clean, standard navigation without passing router state
    navigate('/transactions');
  };

  const getVarianceColor = (variance: number) => {
    if (Math.abs(variance) < 0.01) return 'text-gray-500'; 
    return variance >= 0 ? 'text-green-400' : 'text-red-400';   
  };

  if (loading && !data) return <div className="p-6 text-white font-medium">Loading Budget...</div>;
  if (!data) return <div className="p-6 text-white font-medium">No Data Found.</div>;

  const filteredRows = showAll
      ? data.rows
      : data.rows.filter(r => Math.abs(r.budget) > 0 || Math.abs(r.actual) > 0);

  // --- ROLLUP & GROUPING LOGIC ---
  const groupedData: {
      category_name: string;
      is_income: boolean;
      budget: number;
      actual: number;
      variance: number;
      items: BudgetRow[];
  }[] = [];

  filteredRows.forEach(row => {
      let group = groupedData.find(g => g.category_name === row.category_name);
      if (!group) {
          group = { category_name: row.category_name, is_income: row.is_income, budget: 0, actual: 0, variance: 0, items: [] };
          groupedData.push(group);
      }
      group.items.push(row);
      
      group.budget += Number(row.budget);
      let rowActual = row.is_income ? Number(row.actual) : (Number(row.actual) * -1);
      if (Math.abs(rowActual) < 0.001) rowActual = 0;
      group.actual += rowActual;
      
      const rowVariance = row.is_income ? (Number(row.actual) - Number(row.budget)) : (Number(row.budget) - rowActual);
      group.variance += rowVariance;
  });

  return (
    <div className="p-6 space-y-6 max-w-7xl mx-auto h-full relative">
      {/* --- Header & Controls --- */}
      <div className="flex flex-col md:flex-row justify-between items-start md:items-center gap-4">
        <h1 className="text-3xl font-bold text-white tracking-tight">Budget to Actual <span className="text-gray-500 font-normal text-xl">{periodLabel}</span></h1>
        
        <div className="flex flex-wrap items-center gap-6 bg-gray-900/50 p-2 px-4 rounded-xl border border-gray-800">
          <select 
            value={year}
            onChange={(e) => setYear(Number(e.target.value))}
            className="bg-gray-800 border border-gray-700 text-white text-sm rounded-lg p-1.5 focus:ring-blue-500 outline-none"
          >
            {(data?.years?.includes(year) ? data.years : [...(data?.years || []), year].sort()).map(y => <option key={y} value={y}>{y}</option>)}
          </select>

          <button onClick={() => setPlanning(year + 1)}
            className="text-xs font-bold px-3 py-1.5 rounded-lg border border-blue-500/50 text-blue-300 hover:bg-blue-900/30"
            title={`Set up ${year + 1}'s budget from ${year}`}>
            Plan {year + 1}
          </button>

          <label className="relative inline-flex items-center cursor-pointer">
            <input type="checkbox" className="sr-only peer" checked={showAll} onChange={(e) => setShowAll(e.target.checked)} />
            <div className="w-9 h-5 bg-gray-700 rounded-full peer peer-checked:after:translate-x-full after:content-[''] after:absolute after:top-[2px] after:left-[2px] after:bg-white after:rounded-full after:h-4 after:w-4 after:transition-all peer-checked:bg-blue-600"></div>
            <span className="ml-2 text-xs font-bold text-gray-300 uppercase tracking-widest">Show All</span>
          </label>

          <label className="relative inline-flex items-center cursor-pointer">
            <input type="checkbox" className="sr-only peer" checked={isExpanded} onChange={(e) => setIsExpanded(e.target.checked)} />
            <div className="w-9 h-5 bg-gray-700 rounded-full peer peer-checked:after:translate-x-full after:content-[''] after:absolute after:top-[2px] after:left-[2px] after:bg-white after:rounded-full after:h-4 after:w-4 after:transition-all peer-checked:bg-purple-600"></div>
            <span className="ml-2 text-xs font-bold text-gray-300 uppercase tracking-widest">Expand</span>
          </label>
        </div>
      </div>

      {/* --- Month Pills --- */}
      <div className="flex flex-wrap gap-2">
        {Array.from({length: 12}, (_, i) => i + 1).map(m => (
          <button
            key={m}
            onClick={() => setMonth(m)}
            className={`px-4 py-1.5 rounded-full text-xs font-bold transition-all border ${
                month === m 
                ? "bg-blue-600 border-blue-500 text-white shadow-lg shadow-blue-900/20" 
                : "bg-gray-900 border-gray-700 text-gray-400 hover:border-gray-500 hover:text-white"
            }`}
          >
            {monthName(m)}
          </button>
        ))}
        <button
          onClick={() => setMonth('year')}
          className={`px-4 py-1.5 rounded-full text-xs font-bold transition-all border ${
              wholeYear
              ? "bg-blue-600 border-blue-500 text-white shadow-lg shadow-blue-900/20"
              : "bg-gray-900 border-gray-700 text-gray-400 hover:border-gray-500 hover:text-white"
          }`}
        >
          {year === new Date().getFullYear() ? 'Year to date' : 'Full year'}
        </button>
      </div>

      {/* --- KPI Summary Cards --- */}
      <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
        <KPICard title="Income" budget={data.summary.income_budget} actual={data.summary.income_actual} type="income" />
        <KPICard title="Expenses" budget={data.summary.expense_budget} actual={data.summary.expense_actual} type="expense" />
        <KPICard title="Savings (income − expenses)" budget={data.summary.savings_budget} actual={data.summary.savings_actual} type="surplus" />
      </div>

      {/* --- Budget Table --- */}
      <div data-tour="budget-table" className="bg-gray-900 rounded-xl border border-gray-800 overflow-hidden shadow-2xl mb-10">
        <div className="overflow-x-auto">
          <table className="w-full text-left text-sm text-gray-400">
            <thead className="bg-gray-800 text-xs uppercase text-gray-500 font-black tracking-widest">
              <tr>
                <th className="px-6 py-4 font-black">Category</th>
                <th className="px-6 py-4 text-right">Budget</th>
                <th className="px-6 py-4 text-right">Actual</th>
                <th className="px-6 py-4 text-right">Variance</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-gray-800">
              {groupedData.map(group => (
                <React.Fragment key={group.category_name}>
                  {/* --- ROLLUP HEADER ROW --- */}
                  <tr className="bg-black/20 border-t-2 border-gray-700/50">
                    <td className="px-6 py-4 text-white font-bold text-base">{group.category_name}</td>
                    <td className="px-6 py-4 text-right text-white font-mono font-bold">
                      {group.budget.toLocaleString(undefined, {minimumFractionDigits: 2})}
                    </td>
                    <td className="px-6 py-4 text-right text-white font-mono font-bold">
                      {group.actual.toLocaleString(undefined, {minimumFractionDigits: 2})}
                    </td>
                    <td className={`px-6 py-4 text-right font-mono font-bold ${getVarianceColor(group.variance)}`}>
                      {group.variance > 0 ? '+' : ''}{group.variance.toLocaleString(undefined, {minimumFractionDigits: 2})}
                    </td>
                  </tr>
                  
                  {/* --- SUBCATEGORY ROWS --- */}
                  {isExpanded && group.items.map(row => {
                    let displayActual = row.is_income ? row.actual : (row.actual * -1);
                    if(Math.abs(displayActual) < 0.001) displayActual = 0;
                    const variance = row.is_income ? (row.actual - row.budget) : (row.budget - displayActual);
                    
                    return (
                      <tr key={row.id} className="hover:bg-gray-800/40 transition-colors">
                        <td className="px-6 py-3 pl-12 text-gray-400">{row.name}</td>
                        <td className="px-6 py-3 text-right">
                            <button 
                                onClick={() => handleEditClick(row)}
                                title={wholeYear ? 'The budget for these months together; pick a month to change it' : 'Change this budget'}
                                className={`font-mono font-medium ${wholeYear ? 'text-gray-300 cursor-default' : 'text-blue-400 hover:text-blue-300 decoration-dotted underline underline-offset-4'}`}
                            >
                                {Number(row.budget).toLocaleString(undefined, {minimumFractionDigits: 2})}
                            </button>
                        </td>
                        <td className="px-6 py-3 text-right font-mono text-gray-300">
                            {/* --- Actual Amount Link --- */}
                            <button 
                                onClick={() => handleActualClick(row)}
                                className="text-gray-300 hover:text-white font-mono font-medium decoration-dotted underline underline-offset-4"
                                title="View Transactions"
                            >
                                {Number(displayActual).toLocaleString(undefined, {minimumFractionDigits: 2})}
                            </button>
                        </td>
                        <td className={`px-6 py-3 text-right font-mono font-bold ${getVarianceColor(variance)}`}>
                            {variance > 0 ? '+' : ''}{Number(variance).toLocaleString(undefined, {minimumFractionDigits: 2})}
                        </td>
                      </tr>
                    );
                  })}
                </React.Fragment>
              ))}
            </tbody>
          </table>
        </div>
      </div>

      {suggesting !== null && (
        <BudgetSuggestions year={suggesting} plan={reviewing ?? undefined} onClose={() => { setSuggesting(null); setReviewing(null); }}
          onDone={() => { const planned = suggesting; setSuggesting(null); setReviewing(null); setYear(planned); setMonth(1); }} />
      )}
      {planning !== null && (
        <PlanBudget year={planning} onClose={() => setPlanning(null)}
          onSuggest={() => { const planned = planning; setPlanning(null); setReviewing(null); setSuggesting(planned); }}
          onReview={(plan) => { const planned = planning; setPlanning(null); setReviewing(plan); setSuggesting(planned); }}
          onDone={(quiet) => { const planned = planning; setPlanning(null); setYear(planned); setMonth(1); if (!quiet) notify(`${planned}'s budget is set up.`, 'info'); }} />
      )}

      {/* --- Edit Budget Modal --- */}
      {editOpen && (
        <div className="fixed inset-0 z-50 bg-black/80 backdrop-blur-sm flex items-center justify-center p-4">
            <div className="bg-gray-900 border border-gray-800 rounded-2xl w-full max-w-sm overflow-hidden shadow-2xl animate-in zoom-in-95 duration-200">
                <div className="flex justify-between items-center p-6 border-b border-gray-800 bg-gray-950">
                    <h2 className="text-lg font-bold text-white">Update Budget</h2>
                    <button onClick={() => setEditOpen(false)} className="text-gray-500 hover:text-white transition-colors">
                        <XMarkIcon className="w-6 h-6" />
                    </button>
                </div>
                
                <div className="p-6 space-y-4">
                    <div>
                        <p className="text-xs font-black text-gray-500 uppercase tracking-widest mb-1">Subcategory</p>
                        <p className="text-white font-bold">{selectedRow?.name}</p>
                    </div>

                    <div>
                        <label className="block text-xs font-bold text-gray-400 mb-1 uppercase">Target Amount</label>
                        <input 
                            type="number"
                            autoFocus
                            className="w-full bg-gray-800 border border-gray-700 text-white text-lg font-mono rounded-lg p-2.5 focus:ring-blue-500 focus:border-blue-500 outline-none"
                            value={editAmount}
                            onChange={(e) => setEditAmount(e.target.value)}
                        />
                    </div>

                    <label className="flex items-start cursor-pointer group mt-4">
                        <div className="relative flex items-center">
                            <input 
                                type="checkbox" 
                                className="sr-only peer" 
                                checked={applyFuture} 
                                onChange={(e) => setApplyFuture(e.target.checked)} 
                            />
                            <div className="w-5 h-5 bg-gray-800 border border-gray-700 rounded peer-checked:bg-blue-600 peer-checked:border-blue-500 transition-all flex items-center justify-center">
                                <div className="hidden peer-checked:block text-white">✓</div>
                            </div>
                        </div>
                        <span className="ml-3 text-sm text-gray-300 group-hover:text-white transition-colors">
                            Apply to remaining months of {year}?
                        </span>
                    </label>
                </div>

                <div className="p-6 bg-gray-950 border-t border-gray-800 flex justify-end gap-3">
                    <button onClick={() => setEditOpen(false)} className="px-4 py-2 text-gray-400 font-medium hover:text-white">Cancel</button>
                    <button onClick={handleSave} className="px-6 py-2 bg-blue-600 hover:bg-blue-500 text-white font-bold rounded-lg shadow-lg transition-colors">Save</button>
                </div>
            </div>
        </div>
      )}

      {/* --- Transactions List Modal --- */}
      {txListOpen && (
        <div className="fixed inset-0 z-[60] bg-black/80 backdrop-blur-sm flex items-center justify-center p-4">
            <div className="bg-gray-900 border border-gray-800 rounded-2xl w-full max-w-2xl overflow-hidden shadow-2xl flex flex-col max-h-[80vh] animate-in zoom-in-95 duration-200">
                <div className="flex justify-between items-center p-6 border-b border-gray-800 bg-gray-950">
                    <div>
                        <h2 className="text-lg font-bold text-white">Transactions</h2>
                        <p className="text-xs text-gray-400 mt-1">{selectedSubcat?.name} ({periodLabel})</p>
                    </div>
                    <button onClick={() => setTxListOpen(false)} className="text-gray-500 hover:text-white transition-colors">
                        <XMarkIcon className="w-6 h-6" />
                    </button>
                </div>
                <div className="overflow-y-auto p-2 flex-1">
                    {loadingTx ? (
                        <div className="p-8 text-center text-gray-500">Loading transactions...</div>
                    ) : txList.length === 0 ? (
                        <div className="p-8 text-center text-gray-500">No transactions found for this period.</div>
                    ) : (
                        <table className="w-full text-left text-sm text-gray-400">
                            <thead className="bg-gray-800 text-xs uppercase text-gray-500 font-bold sticky top-0">
                                <tr>
                                    <th className="px-4 py-3 rounded-tl-lg">Date</th>
                                    <th className="px-4 py-3">Description</th>
                                    <th className="px-4 py-3 text-right rounded-tr-lg">Amount</th>
                                </tr>
                            </thead>
                            <tbody className="divide-y divide-gray-800">
                                {txList.map((tx: any) => {
                                    // The part that counts toward this category: for a split
                                    // transaction, only its split lines here, as in the budget
                                    const whole = Number(tx.amount);
                                    const share = tx.subcategory_amount != null ? Number(tx.subcategory_amount) : whole;
                                    const isPart = Math.abs(share - whole) > 0.005;
                                    return (
                                    <tr 
                                        key={tx.id} 
                                        onClick={() => handleTxClick(tx)} 
                                        className="hover:bg-blue-900/20 cursor-pointer transition-colors"
                                    >
                                        <td className="px-4 py-3 font-mono">{tx.date}</td>
                                        <td className="px-4 py-3 text-white">{tx.description}</td>
                                        <td className={`px-4 py-3 text-right font-mono font-medium ${share < 0 ? 'text-white' : 'text-green-400'}`}>
                                            {share.toLocaleString(undefined, {minimumFractionDigits: 2})}
                                            {isPart && (
                                                <div className="text-xs font-normal text-gray-500">
                                                    split, of {whole.toLocaleString(undefined, {minimumFractionDigits: 2})}
                                                </div>
                                            )}
                                        </td>
                                    </tr>
                                    );
                                })}
                            </tbody>
                            <tfoot>
                                <tr className="border-t border-gray-700">
                                    <td colSpan={2} className="px-4 py-3 text-right text-xs uppercase text-gray-500 font-bold">Total</td>
                                    <td className="px-4 py-3 text-right font-mono font-bold text-white">
                                        {txList.reduce((sum: number, tx: any) => sum + Number(tx.subcategory_amount ?? tx.amount), 0)
                                            .toLocaleString(undefined, {minimumFractionDigits: 2, maximumFractionDigits: 2})}
                                    </td>
                                </tr>
                            </tfoot>
                        </table>
                    )}
                </div>
            </div>
        </div>
      )}

    </div>
  );
};

// --- Sub-Component: KPI Card ---
const KPICard = ({title, budget, actual, type}: any) => {
    let colorClass = 'text-white';
    
    if (type === 'surplus') {
        colorClass = actual >= 0 ? 'text-green-400' : 'text-red-400';
    }

    let displayActual = type === 'expense' ? (actual * -1) : actual;
    if (Math.abs(displayActual) < 0.001) displayActual = 0;

    return (
        <div className="bg-gray-800 border border-gray-700 rounded-xl p-5 shadow-lg">
            <div className="text-[10px] font-black text-gray-500 uppercase tracking-[0.2em] text-center mb-4">
                {title}
            </div>
            <div className="flex justify-between items-end">
                <div>
                    <span className="block text-[10px] font-bold text-gray-500 uppercase mb-1 text-gray-500">Budget</span>
                    <span className="text-xl font-mono text-gray-300">
                        ${Number(budget).toLocaleString(undefined, {minimumFractionDigits: 0})}
                    </span>
                </div>
                <div className="text-right">
                    <span className="block text-[10px] font-bold text-gray-500 uppercase mb-1 text-gray-500">Actual</span>
                    <span className={`text-2xl font-mono font-black ${colorClass}`}>
                        ${Number(displayActual).toLocaleString(undefined, {minimumFractionDigits: 0})}
                    </span>
                </div>
            </div>
        </div>
    );
};

export default BudgetView;