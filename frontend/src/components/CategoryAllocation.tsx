// Copyright (C) 2026 free-the-software
// SPDX-License-Identifier: AGPL-3.0-or-later
// This file is part of Save More Money, under the GNU AGPL v3 or later: see LICENSE.
import { PlusCircleIcon, TrashIcon } from '@heroicons/react/24/outline';
import CategoryPicker from './CategoryPicker';

// The category of a transaction, or its split across several categories.
// Shared by the register's edit window and the Import Inbox's.

export interface SplitLine { id: number; amount: number; subcategory_name: string; memo: string; subcategory?: number; }
interface SubCategory { id: number; name: string; }
export interface CategoryGroup { id: number; name: string; subcategories: SubCategory[]; }

const formatCurrency = (val: number) => new Intl.NumberFormat('en-US', { style: 'currency', currency: 'USD' }).format(val);

// How far the split lines are from the transaction's amount (0 when they add up)
export const splitRemaining = (amount: number, rows: SplitLine[]) =>
    (Number(amount) || 0) - rows.reduce((sum, row) => sum + (Number(row.amount) || 0), 0);

export const isSplitValid = (amount: number, rows: SplitLine[]) => Math.abs(splitRemaining(amount, rows)) < 0.01;

interface Props {
    amount: number;
    subcategory: number | null | undefined;
    onSubcategoryChange: (id: number | null) => void;
    splitMode: boolean;
    onSplitModeChange: (on: boolean) => void;
    splitRows: SplitLine[];
    onSplitRowsChange: (rows: SplitLine[]) => void;
    categories: CategoryGroup[];
    selectId?: string;
}

const CategoryAllocation = ({
    amount, subcategory, onSubcategoryChange, splitMode, onSplitModeChange,
    splitRows, onSplitRowsChange, categories, selectId = 'txSubcategory',
}: Props) => {
    const total = splitRows.reduce((sum, row) => sum + (Number(row.amount) || 0), 0);
    const remaining = splitRemaining(amount, splitRows);
    const valid = isSplitValid(amount, splitRows);

    const addRow = () => onSplitRowsChange([...splitRows, { id: Date.now(), subcategory: 0, amount: 0, memo: '', subcategory_name: '' }]);
    const removeRow = (index: number) => onSplitRowsChange(splitRows.filter((_row, i) => i !== index));
    const updateRow = (index: number, field: keyof SplitLine, value: string | number) =>
        onSplitRowsChange(splitRows.map((row, i) => (i === index ? { ...row, [field]: value } : row)));

    const toggleSplit = () => {
        // Turning a split on starts from the current category and the whole amount
        if (!splitMode && splitRows.length === 0) {
            onSplitRowsChange([
                { id: 1, subcategory: subcategory || 0, amount: amount || 0, memo: '', subcategory_name: '' },
                { id: 2, subcategory: 0, amount: 0, memo: '', subcategory_name: '' },
            ]);
        }
        onSplitModeChange(!splitMode);
    };

    return (
        <div>
            <div className="flex justify-between items-center mb-2">
                <span className="block text-xs font-bold text-gray-500 uppercase">Category Allocation</span>
                <button type="button" onClick={toggleSplit} className={`text-xs font-bold px-3 py-1 rounded-full border transition-colors ${splitMode ? 'bg-purple-600 text-white border-purple-600' : 'text-purple-400 border-purple-500/50 hover:bg-purple-900/30'}`}>
                    {splitMode ? 'Split Mode Active' : 'Switch to Split'}
                </button>
            </div>

            {splitMode ? (
                <div className="bg-gray-800/50 rounded-xl border border-gray-700 p-4 space-y-3">
                    {splitRows.map((row, index) => (
                        <div key={row.id || index} className="flex gap-2 items-start">
                            <div className="flex-1 min-w-0">
                                <CategoryPicker
                                    ariaLabel="Split Category"
                                    value={row.subcategory || null}
                                    onChange={(id) => updateRow(index, 'subcategory', id ?? 0)}
                                    categories={categories}
                                    placeholder="Search categories..."
                                    compact
                                />
                            </div>
                            <div className="w-24">
                                <input aria-label="Split Amount" type="number" step="0.01" value={row.amount} onChange={(e) => updateRow(index, 'amount', Number.parseFloat(e.target.value))} className="w-full bg-gray-900 border-gray-600 rounded-lg text-white p-2 text-sm text-right font-mono" placeholder="0.00" />
                            </div>
                            <button type="button" aria-label="Remove Split Row" onClick={() => removeRow(index)} className="p-2 text-gray-500 hover:text-red-400"><TrashIcon className="w-5 h-5" /></button>
                        </div>
                    ))}
                    <div className="flex justify-between items-center pt-2 border-t border-gray-700 mt-2">
                        <button type="button" onClick={addRow} className="text-xs font-bold text-blue-400 hover:text-blue-300 flex items-center"><PlusCircleIcon className="w-4 h-4 mr-1" /> Add Split</button>
                        <div className={`text-sm font-mono font-bold ${valid ? 'text-green-400' : 'text-red-400'}`}>{valid ? (<span>Matched: {formatCurrency(total)}</span>) : (<span>Remaining: {formatCurrency(remaining)}</span>)}</div>
                    </div>
                </div>
            ) : (
                <>
                    <label htmlFor={selectId} className="sr-only">Subcategory</label>
                    <CategoryPicker
                        id={selectId}
                        value={subcategory}
                        onChange={onSubcategoryChange}
                        categories={categories}
                        allowNone
                    />
                </>
            )}
        </div>
    );
};

export default CategoryAllocation;
