// Copyright (C) 2026 free-the-software
// SPDX-License-Identifier: AGPL-3.0-or-later
// This file is part of Save More Money, under the GNU AGPL v3 or later: see LICENSE.
import { useState } from 'react';
import { XMarkIcon } from '@heroicons/react/24/outline';
import api from '../api';

// Adding or editing an asset (home, vehicle, other) or a loan (mortgage,
// car loan, other loan). Each kind asks only for the details that apply to it.

export type HoldingKind = 'home' | 'vehicle' | 'investment' | 'other' | 'mortgage' | 'auto_loan' | 'loan';

export interface Holding {
    id: number;
    name: string;
    kind: HoldingKind;
    active: boolean;
    closed_date: string | null;
    asset: Record<string, any> | null;
    loan: (Record<string, any> & { scheduled_payment?: string; payoff_date?: string | null }) | null;
    // Loans: figures from the real payments recorded
    loan_status?: {
        owed: string; starting_balance: string; balance_date: string | null;
        payoff_date: string | null; payments_left: number | null;
        escrow_account: number | null; escrow_balance: string | null;
    };
    values: { id: number; date: string; amount: string; source: string }[];
}

export const KIND_LABELS: Record<HoldingKind, string> = {
    home: 'Home', vehicle: 'Vehicle', investment: 'Investment account', other: 'Other asset',
    mortgage: 'Mortgage', auto_loan: 'Car loan', loan: 'Other loan',
};
export const isLoan = (kind: HoldingKind) => ['mortgage', 'auto_loan', 'loan'].includes(kind);

type FieldType = 'text' | 'money' | 'number' | 'percent' | 'date' | 'textarea' | 'select';
interface Field { name: string; label: string; type: FieldType; required?: boolean; options?: [string, string][]; help?: string; wide?: boolean; }

const PURCHASE: Field[] = [
    { name: 'purchase_date', label: 'Purchase date', type: 'date' },
    { name: 'purchase_price', label: 'Purchase price', type: 'money', help: 'Used as its value until you record one' },
    { name: 'ownership_percent', label: 'Your share (%)', type: 'percent', help: '100 unless owned with someone outside this app' },
];

const ASSET_FIELDS: Record<'home' | 'vehicle' | 'investment' | 'other', Field[]> = {
    home: [
        { name: 'address', label: 'Address', type: 'text', wide: true },
        { name: 'property_type', label: 'Property type', type: 'text', help: 'e.g. Single family, Condo' },
        { name: 'year_built', label: 'Year built', type: 'number' },
        { name: 'square_feet', label: 'Square feet', type: 'number' },
        ...PURCHASE,
    ],
    vehicle: [
        { name: 'vehicle_year', label: 'Year', type: 'number' },
        { name: 'make', label: 'Make', type: 'text' },
        { name: 'model', label: 'Model', type: 'text' },
        { name: 'trim', label: 'Trim', type: 'text' },
        { name: 'vin', label: 'VIN', type: 'text' },
        { name: 'mileage', label: 'Mileage', type: 'number' },
        ...PURCHASE,
    ],
    // Brokerage, IRA, 401(k), TSP...: its value is updated from statements
    investment: [{ name: 'ownership_percent', label: 'Your share (%)', type: 'percent', help: '100 unless held jointly with someone outside this app' }],
    other: [...PURCHASE],
};

const LOAN_FIELDS: Field[] = [
    { name: 'lender', label: 'Lender', type: 'text' },
    { name: 'account_last4', label: 'Account (last 4 digits)', type: 'text' },
    { name: 'original_balance', label: 'Amount borrowed', type: 'money', required: true },
    { name: 'interest_rate', label: 'Interest rate (%)', type: 'percent', required: true },
    { name: 'interest_type', label: 'Rate type', type: 'select', options: [['FIXED', 'Fixed'], ['VARIABLE', 'Variable']] },
    { name: 'term_months', label: 'Term (months)', type: 'number', required: true, help: '360 = 30 years, 60 = 5 years' },
    { name: 'origination_date', label: 'Start date', type: 'date' },
    { name: 'first_payment_date', label: 'First payment date', type: 'date', required: true },
    { name: 'payment_schedule', label: 'Payments', type: 'select', options: [['MONTHLY', 'Monthly'], ['BIWEEKLY', 'Every two weeks'], ['WEEKLY', 'Weekly']] },
    { name: 'payment_amount', label: 'Payment (principal + interest)', type: 'money', help: 'Leave empty to work it out from the terms' },
    { name: 'default_extra_principal', label: 'Extra principal each payment', type: 'money' },
];

const MORTGAGE_FIELDS: Field[] = [
    { name: 'escrow_mode', label: 'Escrow', type: 'select', wide: true, options: [
        ['EXPENSE', 'Expense it with each payment (simplest)'],
        ['ACCOUNT', 'Hold it in an escrow account; record the bills the lender pays from it'],
    ], help: 'Either way, the escrow part of each payment is set below' },
    { name: 'property_tax_amount', label: 'Property tax escrow (per month)', type: 'money' },
    { name: 'homeowners_insurance_amount', label: 'Home insurance escrow (per month)', type: 'money' },
    { name: 'pmi_amount', label: 'PMI (per month)', type: 'money' },
];

const LOAN_DEFAULTS: Record<string, string> = {
    interest_type: 'FIXED', payment_schedule: 'MONTHLY', term_months: '360', default_extra_principal: '0',
    property_tax_amount: '0', homeowners_insurance_amount: '0', pmi_amount: '0', escrow_mode: 'EXPENSE',
};

const NUMERIC: FieldType[] = ['money', 'number', 'percent'];
const today = () => new Date().toISOString().slice(0, 10);

interface Props {
    kind: HoldingKind;
    holding: Holding | null;          // null when adding
    assets: Holding[];                // homes and vehicles a loan can be secured on
    onClose: () => void;
    onSaved: () => void;
}

const HoldingForm = ({ kind, holding, assets, onClose, onSaved }: Props) => {
    const loan = isLoan(kind);
    const fields = loan
        ? [...LOAN_FIELDS, ...(kind === 'mortgage' ? MORTGAGE_FIELDS : [])]
        : ASSET_FIELDS[kind as 'home' | 'vehicle' | 'investment' | 'other'];
    const existing = (loan ? holding?.loan : holding?.asset) || {};

    const initial: Record<string, string> = {};
    for (const field of fields) {
        const value = existing[field.name];
        initial[field.name] = value === null || value === undefined ? (loan ? LOAN_DEFAULTS[field.name] ?? '' : field.name === 'ownership_percent' ? '100' : '') : String(value);
    }
    if (loan && kind === 'auto_loan' && !holding) initial.term_months = '60';

    const [name, setName] = useState(holding?.name ?? '');
    const [details, setDetails] = useState(initial);
    const [notes, setNotes] = useState<string>(existing.notes ?? '');
    const [securedBy, setSecuredBy] = useState<string>(holding?.loan?.asset_account ? String(holding.loan.asset_account) : '');
    // The first value (asset), when adding
    const [value, setValue] = useState({ date: today(), amount: '', source: '' });
    // A loan starts with what is owed on a date; real payments change it from then on
    const [start, setStart] = useState({ balance: '', date: '', escrow: '' });
    const [errors, setErrors] = useState<Record<string, string>>({});
    const [saving, setSaving] = useState(false);

    // Not a closed (sold) home or vehicle, unless this loan already points at it
    const securable = assets.filter(a => (kind === 'mortgage' ? a.kind === 'home' : kind === 'auto_loan' ? a.kind === 'vehicle' : !isLoan(a.kind))
        && (!(a.closed_date || a.active === false) || a.id === holding?.loan?.asset_account));

    const flatten = (data: any, prefix = ''): Record<string, string> => {
        const out: Record<string, string> = {};
        if (!data || typeof data !== 'object') return out;
        for (const [key, val] of Object.entries(data)) {
            if (Array.isArray(val)) out[prefix + key] = val.join(' ');
            else if (val && typeof val === 'object') Object.assign(out, flatten(val, ''));
            else out[prefix + key] = String(val);
        }
        return out;
    };

    const save = async () => {
        const problems: Record<string, string> = {};
        if (!name.trim()) problems.name = 'Give it a name.';
        for (const field of fields) if (field.required && !details[field.name]) problems[field.name] = 'Required.';
        setErrors(problems);
        if (Object.keys(problems).length) return;

        const detail: Record<string, any> = { notes };
        for (const field of fields) {
            const raw = details[field.name].trim();
            detail[field.name] = raw === '' ? (NUMERIC.includes(field.type) || field.type === 'date' ? null : '') : raw;
        }
        if (loan) detail.asset_account = securedBy ? Number(securedBy) : null;
        const body: Record<string, any> = { name: name.trim(), kind, [loan ? 'loan' : 'asset']: detail };
        if (loan) {
            if (!holding) {
                body.starting_balance = start.balance.trim() || null;
                body.starting_date = start.date || null;
            }
            if (details.escrow_mode === 'ACCOUNT') body.escrow_starting_balance = start.escrow.trim() || null;
        }

        setSaving(true);
        try {
            const saved = holding
                ? (await api.patch(`/holdings/${holding.id}/`, body)).data
                : (await api.post('/holdings/', body)).data;
            if (!holding && !loan && value.amount) {
                await api.post(`/holdings/${saved.id}/values/`, value);
            }
            onSaved();
        } catch (err: any) {
            const data = err.response?.data;
            setErrors(Object.keys(flatten(data)).length ? flatten(data) : { form: 'Could not save. Check the values and try again.' });
        } finally {
            setSaving(false);
        }
    };

    const input = 'w-full bg-gray-800 border border-gray-700 rounded-lg text-white p-2 text-sm focus:border-blue-500 outline-none';

    const renderField = (field: Field) => (
        <div key={field.name} className={field.wide ? 'md:col-span-2' : ''}>
            <label htmlFor={`f-${field.name}`} className="block text-xs font-bold text-gray-400 uppercase mb-1">
                {field.label}{field.required && <span className="text-red-400"> *</span>}
            </label>
            {field.type === 'select' ? (
                <select id={`f-${field.name}`} value={details[field.name]} onChange={e => setDetails({ ...details, [field.name]: e.target.value })} className={input}>
                    {field.options!.map(([v, l]) => <option key={v} value={v}>{l}</option>)}
                </select>
            ) : (
                <input
                    id={`f-${field.name}`}
                    type={field.type === 'date' ? 'date' : 'text'}
                    inputMode={NUMERIC.includes(field.type) ? 'decimal' : undefined}
                    value={details[field.name]}
                    onChange={e => setDetails({ ...details, [field.name]: e.target.value })}
                    className={input}
                />
            )}
            {errors[field.name] ? <p className="text-xs text-red-400 mt-1">{errors[field.name]}</p>
                : field.help && <p className="text-xs text-gray-500 mt-1">{field.help}</p>}
        </div>
    );

    return (
        <div className="fixed inset-0 bg-black/80 backdrop-blur-sm flex items-center justify-center z-50 p-4">
            <div className="bg-gray-900 border border-gray-700 rounded-2xl shadow-2xl w-full max-w-2xl max-h-[90vh] flex flex-col overflow-hidden">
                <div className="p-5 border-b border-gray-800 flex justify-between items-center bg-gray-950">
                    <h3 className="text-lg font-bold text-white">{holding ? 'Edit' : 'Add'} {KIND_LABELS[kind].toLowerCase()}</h3>
                    <button onClick={onClose} className="text-gray-500 hover:text-white" aria-label="Close"><XMarkIcon className="w-6 h-6" /></button>
                </div>
                <div className="p-5 overflow-y-auto space-y-4">
                    <div>
                        <label htmlFor="f-name" className="block text-xs font-bold text-gray-400 uppercase mb-1">Name<span className="text-red-400"> *</span></label>
                        <input id="f-name" value={name} onChange={e => setName(e.target.value)} className={input}
                            placeholder={kind === 'home' ? 'e.g. Our house' : kind === 'vehicle' ? 'e.g. 2024 Camry' : kind === 'mortgage' ? 'e.g. House mortgage' : kind === 'auto_loan' ? 'e.g. Camry loan' : ''} />
                        {errors.name && <p className="text-xs text-red-400 mt-1">{errors.name}</p>}
                    </div>
                    <div className="grid grid-cols-1 md:grid-cols-2 gap-4">{fields.map(renderField)}</div>

                    {loan && (
                        <div>
                            <label htmlFor="f-secured" className="block text-xs font-bold text-gray-400 uppercase mb-1">Secured by</label>
                            <select id="f-secured" value={securedBy} onChange={e => setSecuredBy(e.target.value)} className={input}>
                                <option value="">Nothing / not listed</option>
                                {securable.map(a => <option key={a.id} value={a.id}>{a.name}</option>)}
                            </select>
                            <p className="text-xs text-gray-500 mt-1">The {kind === 'auto_loan' ? 'vehicle' : 'home'} this loan is for. Add it as an asset first to pick it here.</p>
                        </div>
                    )}

                    {loan && (!holding || (details.escrow_mode === 'ACCOUNT' && !holding.loan_status?.escrow_account)) && (
                        <div className="bg-gray-800/50 border border-gray-700 rounded-xl p-4 space-y-3">
                            {!holding && <>
                                <p className="text-sm font-bold text-white">Starting balance</p>
                                <p className="text-xs text-gray-500">
                                    What you owed, and on what date. From then on the balance changes only with the payments you record,
                                    so extra principal shows exactly. Leave empty to start from the amount borrowed on the start date.
                                </p>
                                <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
                                    <input inputMode="decimal" aria-label="Balance owed" placeholder="Balance owed" value={start.balance} onChange={e => setStart({ ...start, balance: e.target.value })} className={input} />
                                    <input type="date" aria-label="As of" value={start.date} onChange={e => setStart({ ...start, date: e.target.value })} className={input} />
                                </div>
                            </>}
                            {details.escrow_mode === 'ACCOUNT' && (
                                <div>
                                    <label htmlFor="f-escrow-start" className="block text-xs font-bold text-gray-400 uppercase mb-1">Escrow balance {holding ? 'today' : 'on that date'}</label>
                                    <input id="f-escrow-start" inputMode="decimal" placeholder="0.00" value={start.escrow} onChange={e => setStart({ ...start, escrow: e.target.value })} className={input} />
                                    <p className="text-xs text-gray-500 mt-1">From your latest escrow statement, if you have one.</p>
                                </div>
                            )}
                            {(errors.starting_balance || errors.starting_date || errors.escrow_starting_balance) &&
                                <p className="text-xs text-red-400">{errors.starting_balance || errors.starting_date || errors.escrow_starting_balance}</p>}
                        </div>
                    )}

                    {!holding && !loan && (
                        <div className="bg-gray-800/50 border border-gray-700 rounded-xl p-4">
                            <p className="text-sm font-bold text-white mb-1">Current value (optional)</p>
                            <p className="text-xs text-gray-500 mb-3">
                                {kind === 'investment' ? 'From your latest statement. Record a new value whenever you get one.'
                                      : 'e.g. from Zillow or Kelley Blue Book. You can record new values any time.'}
                            </p>
                            <div className="grid grid-cols-1 md:grid-cols-3 gap-3">
                                <input type="date" aria-label="As of" value={value.date} onChange={e => setValue({ ...value, date: e.target.value })} className={input} />
                                <input inputMode="decimal" aria-label="Amount" placeholder="Amount" value={value.amount} onChange={e => setValue({ ...value, amount: e.target.value })} className={input} />
                                <input aria-label="Source" placeholder="e.g. Zillow estimate" value={value.source} onChange={e => setValue({ ...value, source: e.target.value })} className={input} />
                            </div>
                            {(errors.amount || errors.date) && <p className="text-xs text-red-400 mt-1">{errors.amount || errors.date}</p>}
                        </div>
                    )}

                    <div>
                        <label htmlFor="f-notes" className="block text-xs font-bold text-gray-400 uppercase mb-1">Notes</label>
                        <textarea id="f-notes" rows={2} value={notes} onChange={e => setNotes(e.target.value)} className={input}
                            placeholder={kind === 'mortgage' ? 'e.g. Remove PMI at 78% loan-to-value' : ''} />
                    </div>
                    {Object.entries(errors).filter(([k]) => !['name', 'amount', 'date', 'starting_balance', 'starting_date', 'escrow_starting_balance', ...fields.map(f => f.name)].includes(k)).map(([k, v]) => (
                        <p key={k} className="text-sm text-red-400">{v}</p>
                    ))}
                </div>
                <div className="p-4 border-t border-gray-800 bg-gray-950 flex justify-end gap-3">
                    <button onClick={onClose} className="px-4 py-2 text-gray-400 hover:text-white">Cancel</button>
                    <button onClick={save} disabled={saving} className="px-5 py-2 bg-blue-600 hover:bg-blue-500 disabled:opacity-50 text-white font-bold rounded-lg">
                        {saving ? 'Saving...' : 'Save'}
                    </button>
                </div>
            </div>
        </div>
    );
};

export default HoldingForm;
