// Copyright (C) 2026 free-the-software
// SPDX-License-Identifier: AGPL-3.0-or-later
// This file is part of Save More Money, under the GNU AGPL v3 or later: see LICENSE.
import { Fragment, useEffect, useMemo, useState } from 'react';
import { CartesianGrid, Line, LineChart, ReferenceLine, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts';
import { TrashIcon, ChevronDownIcon, ChevronRightIcon, CheckCircleIcon, ArrowTrendingDownIcon } from '@heroicons/react/24/outline';
import { Link, useSearchParams } from 'react-router';
import api from '../api';
import { KIND_LABELS, isLoan } from './HoldingForm';
import type { Holding } from './HoldingForm';
import LoanPaymentForm from './LoanPaymentForm';
import AccountLedger from './AccountLedger';
import EquityChart from './EquityChart';
import { askConfirm, notify } from './Dialogs';

// The Cash Flow page's forecast for an account (finance/forecast.py)
interface Outlook { id: number; will_decrement: boolean; deficit_date: string | null; projected_balance: string; }

// Net worth over time: bank accounts (worked back from their bank balances),
// plus homes, vehicles and other assets at their recorded values, less loans.

interface Point {
    date: string;
    assets: string;
    liabilities: string;
    net_worth: string;
    accounts: Record<string, string>;
}
interface AccountInfo {
    id: number; name: string; type: string; kind: string; group: string; closed: string | null; balance_is_calculated: boolean;
    // How current the figure is, e.g. "Verified" on the bank's balance date
    as_of_label: string; as_of: string | null;
}

// The chart's single series. Validated against the page's dark surface (#111827).
const SERIES = '#3987e5';
const GRID = '#374151';
const AXIS_TEXT = '#9ca3af';

const money = (value: number, cents = false) =>
    value.toLocaleString('en-US', { style: 'currency', currency: 'USD', minimumFractionDigits: cents ? 2 : 0, maximumFractionDigits: cents ? 2 : 0 });
const compact = (value: number) =>
    value.toLocaleString('en-US', { style: 'currency', currency: 'USD', notation: 'compact', maximumFractionDigits: 1 });
const asDate = (iso: string) => new Date(iso + 'T00:00:00');
const monthLabel = (iso: string) => asDate(iso).toLocaleDateString('en-US', { month: 'short', year: 'numeric' });
const dayLabel = (iso: string) => asDate(iso).toLocaleDateString('en-US', { month: 'short', day: 'numeric', year: 'numeric' });
const iso = (d: Date) => `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`;

// The ranges offered; each gives the first day to show (null = all history)
type RangeKey = '5D' | 'YTD' | '1Y' | '3Y' | '5Y' | 'MAX' | 'CUSTOM';
const RANGES: [RangeKey, string][] = [['5D', '5 days'], ['YTD', 'YTD'], ['1Y', '1 yr'], ['3Y', '3 yr'], ['5Y', '5 yr'], ['MAX', 'Max'], ['CUSTOM', 'Custom']];
const rangeStart = (key: RangeKey): string | null => {
    const d = new Date();
    if (key === '5D') d.setDate(d.getDate() - 5);
    else if (key === 'YTD') { d.setMonth(0); d.setDate(1); }
    else if (key === '1Y') d.setFullYear(d.getFullYear() - 1);
    else if (key === '3Y') d.setFullYear(d.getFullYear() - 3);
    else if (key === '5Y') d.setFullYear(d.getFullYear() - 5);
    else return null;
    return iso(d);
};

// The order and titles of the account groups (the server says which group each is in)
const GROUP_TITLES: [string, string][] = [
    ['checking', 'Checking'], ['savings', 'Savings'], ['investments', 'Investments'], ['property', 'Homes and vehicles'],
    ['other_assets', 'Other assets'], ['credit_cards', 'Credit cards'], ['loans', 'Loans'], ['other', 'Other accounts'],
];
// The quick updates offered beside each asset or loan
type EntryMode = 'value' | 'statement' | 'escrow-statement' | 'escrow-payment';
const ENTRY_LABELS: Record<EntryMode, string> = {
    value: 'Value', statement: 'Balance owed (from a statement)', 'escrow-statement': 'Escrow balance (from a statement)', 'escrow-payment': 'Amount the lender paid from escrow',
};

const ChartTooltip = ({ active, payload }: any) => {
    if (!active || !payload?.length) return null;
    const p = payload[0].payload;
    return (
        <div className="bg-gray-950 border border-gray-700 rounded-lg px-3 py-2 text-xs shadow-xl">
            <p className="text-gray-400 mb-1">{p.label}</p>
            <p className="text-white font-bold font-mono">Net worth {money(p.net)}</p>
            <p className="text-gray-300 font-mono">Assets {money(p.assets)}</p>
            <p className="text-gray-300 font-mono">Debts {money(p.debts)}</p>
        </div>
    );
};

const NetWorth = () => {
    const [points, setPoints] = useState<Point[]>([]);
    const [accounts, setAccounts] = useState<AccountInfo[]>([]);
    const [holdings, setHoldings] = useState<Holding[]>([]);
    const [equityFor, setEquityFor] = useState<number | null>(null);
    const [loading, setLoading] = useState(true);
    const [range, setRange] = useState<RangeKey>('1Y');
    const [custom, setCustom] = useState({ start: '', end: iso(new Date()) });
    const [showTable, setShowTable] = useState(false);
    const [paying, setPaying] = useState<Holding | null>(null);
    // Forecasts for the accounts shown on Cash Flow, by account id
    const [outlooks, setOutlooks] = useState<Record<string, Outlook>>({});
    // The account whose transactions are open
    const [viewing, setViewing] = useState<{ id: number; name: string } | null>(null);
    // ?account=<id> (from the shortfall warning) opens that account's transactions
    const [searchParams, setSearchParams] = useSearchParams();
    const [expanded, setExpanded] = useState<number | null>(null);
    const [newValue, setNewValue] = useState<{ id: number; mode: EntryMode; date: string; amount: string; source: string; kind: string } | null>(null);
    const startEntry = (h: Holding, mode: EntryMode) =>
        setNewValue({ id: h.id, mode, date: iso(new Date()), amount: '', source: '', kind: 'property_tax' });
    const [error, setError] = useState('');

    const load = async () => {
        const params: Record<string, string> = {};
        if (range === 'CUSTOM') {
            if (!custom.start) return;            // wait until a start date is chosen
            params.start = custom.start;
            if (custom.end) params.end = custom.end;
        } else {
            const start = rangeStart(range);
            if (start) params.start = start;
        }
        try {
            const [history, list, dashboard] = await Promise.all([
                api.get('/net-worth/', { params }), api.get('/holdings/'), api.get('/accounts/dashboard/'),
            ]);
            setPoints(history.data.points);
            setAccounts(history.data.accounts);
            setHoldings(list.data);
            setOutlooks(Object.fromEntries(dashboard.data.checking.map((o: Outlook) => [String(o.id), o])));
            setError('');
        } catch (err: any) {
            setError(err.response?.data?.error || 'Could not load net worth.');
        } finally {
            setLoading(false);
        }
    };
    useEffect(() => { load(); }, [range]);   // a custom range loads when Apply is pressed

    useEffect(() => {
        const wanted = searchParams.get('account');
        const account = wanted && accounts.find(a => String(a.id) === wanted);
        if (account) {
            setViewing({ id: account.id, name: account.name });
            setSearchParams({}, { replace: true });
        }
    }, [accounts, searchParams]);

    const shortfalls = Object.values(outlooks).filter(o => o.will_decrement);
    const nameOf = (id: number) => accounts.find(a => a.id === id)?.name ?? 'An account';

    // Every figure on the page follows the range: from its first point to its last
    const first = points[0];
    const latest = points[points.length - 1];
    const daily = points.length > 1 && (asDate(latest.date).getTime() - asDate(first.date).getTime()) / 86400000 <= 92;
    const chartData = points.map(p => ({
        label: daily ? dayLabel(p.date) : monthLabel(p.date), net: Number(p.net_worth), assets: Number(p.assets), debts: Number(p.liabilities),
    }));
    const change = latest && first ? Number(latest.net_worth) - Number(first.net_worth) : 0;
    const endsToday = latest && latest.date === iso(new Date());
    const shown = useMemo(() => accounts, [accounts]);

    const saveValue = async () => {
        if (!newValue) return;
        try {
            const { id, mode, date, amount, source, kind } = newValue;
            if (mode === 'escrow-payment') await api.post(`/holdings/${id}/escrow-payment/`, { date, amount, kind, memo: source });
            else await api.post(`/holdings/${id}/values/`, { date, amount, source, which: mode === 'escrow-statement' ? 'escrow' : undefined });
            setNewValue(null);
            load();
        } catch (err: any) {
            const data = err.response?.data;
            notify('Could not save: ' + (data?.error || (data ? Object.values(data).flat().join(' ') : err.message)));
        }
    };
    const deleteValue = async (holding: Holding, entryId: number) => {
        if (!(await askConfirm({ title: 'Delete this recorded value?', confirmLabel: 'Delete', danger: true }))) return;
        await api.delete(`/holdings/${holding.id}/values/${entryId}/`);
        load();
    };

    if (loading) return <div className="p-6 text-gray-400">Loading...</div>;

    return (
        <div className="p-6 space-y-6 max-w-7xl mx-auto">
            <h1 data-tour="networth-title" className="text-3xl font-bold text-white tracking-tight">Net Worth</h1>
            {error && <p className="text-red-400">{error}</p>}

            {/* Accounts forecast to go below zero: hard to miss, one click from the detail */}
            {shortfalls.map(o => (
                <button key={o.id} onClick={() => setViewing({ id: o.id, name: nameOf(o.id) })}
                    className="w-full text-left flex items-center gap-3 bg-red-950/40 border border-red-500/50 hover:border-red-400 rounded-2xl p-4">
                    <ArrowTrendingDownIcon className="w-6 h-6 text-red-400 shrink-0" />
                    <span className="text-sm text-red-100">
                        <span className="font-bold">{nameOf(o.id)}</span> is forecast to go below zero{' '}
                        {o.deficit_date ? `on ${dayLabel(o.deficit_date)}` : 'now'} (lowest {money(Number(o.projected_balance))}).
                        <span className="underline ml-2">See its transactions</span>
                    </span>
                </button>
            ))}

            {/* --- Headline --- */}
            {latest ? (
                <div className="grid grid-cols-1 md:grid-cols-4 gap-4">
                    <div className="md:col-span-2 bg-gray-900 border border-gray-800 rounded-2xl p-5">
                        <p className="text-xs font-bold text-gray-400 uppercase">Net worth {endsToday ? 'today' : `on ${dayLabel(latest.date)}`}</p>
                        <p className="text-4xl font-bold text-white font-mono mt-1">{money(Number(latest.net_worth))}</p>
                        <p className="text-sm text-gray-400 mt-2">
                            <span className={change >= 0 ? 'text-green-400' : 'text-red-400'}>{change >= 0 ? '▲' : '▼'} {money(Math.abs(change))}</span>
                            {' '}since {first ? dayLabel(first.date) : ''}
                        </p>
                    </div>
                    <div className="bg-gray-900 border border-gray-800 rounded-2xl p-5">
                        <p className="text-xs font-bold text-gray-400 uppercase">Assets</p>
                        <p className="text-2xl font-bold text-white font-mono mt-1">{money(Number(latest.assets))}</p>
                    </div>
                    <div className="bg-gray-900 border border-gray-800 rounded-2xl p-5">
                        <p className="text-xs font-bold text-gray-400 uppercase">Debts</p>
                        <p className="text-2xl font-bold text-white font-mono mt-1">{money(Math.abs(Number(latest.liabilities)))}</p>
                    </div>
                </div>
            ) : (
                <p className="text-gray-400">Nothing to show yet. Add accounts, import bank files, or add your home, vehicles and loans below.</p>
            )}

            {/* --- Chart --- */}
            {(points.length > 0 || range === 'CUSTOM') && (
                <div className="bg-gray-900 border border-gray-800 rounded-2xl p-5">
                    <div className="flex flex-wrap justify-between items-center gap-3 mb-4">
                        <h2 className="text-sm font-bold text-gray-300">Net worth {daily ? 'by day' : 'by month'}</h2>
                        <div className="flex flex-wrap items-center gap-2">
                            {RANGES.map(([key, label]) => (
                                <button key={key} onClick={() => setRange(key)}
                                    className={`px-3 py-1 rounded-lg text-xs font-bold border ${range === key ? 'bg-blue-600 border-blue-500 text-white' : 'border-gray-700 text-gray-400 hover:text-white'}`}>
                                    {label}
                                </button>
                            ))}
                            <button onClick={() => setShowTable(!showTable)} className="ml-2 text-xs text-gray-400 hover:text-white underline">
                                {showTable ? 'Show chart' : 'Show as table'}
                            </button>
                        </div>
                    </div>
                    {range === 'CUSTOM' && (
                        <div className="flex flex-wrap items-center gap-2 mb-4 text-sm">
                            <label className="text-gray-400" htmlFor="nw-from">From</label>
                            <input id="nw-from" type="date" value={custom.start} onChange={e => setCustom({ ...custom, start: e.target.value })} className="bg-gray-800 border border-gray-700 rounded-lg text-white p-1.5" />
                            <label className="text-gray-400" htmlFor="nw-to">to</label>
                            <input id="nw-to" type="date" value={custom.end} onChange={e => setCustom({ ...custom, end: e.target.value })} className="bg-gray-800 border border-gray-700 rounded-lg text-white p-1.5" />
                            <button onClick={load} disabled={!custom.start} className="px-3 py-1.5 bg-blue-600 hover:bg-blue-500 disabled:opacity-40 text-white font-bold rounded-lg">Apply</button>
                        </div>
                    )}
                    {showTable ? (
                        <div className="max-h-96 overflow-y-auto">
                            <table className="w-full text-sm text-right font-mono">
                                <thead className="text-xs uppercase text-gray-500 sticky top-0 bg-gray-900">
                                    <tr><th className="text-left py-2">{daily ? 'Day' : 'Month'}</th><th>Assets</th><th>Debts</th><th>Net worth</th></tr>
                                </thead>
                                <tbody className="divide-y divide-gray-800 text-gray-300">
                                    {[...chartData].reverse().map(row => (
                                        <tr key={row.label}><td className="text-left py-1.5 font-sans">{row.label}</td><td>{money(row.assets)}</td><td>{money(Math.abs(row.debts))}</td><td className="text-white font-bold">{money(row.net)}</td></tr>
                                    ))}
                                </tbody>
                            </table>
                        </div>
                    ) : (
                        points.length < 2 ? <p className="text-sm text-gray-500 py-8 text-center">Not enough history in this range to draw a chart.</p> :
                        <div className="h-72" role="img" aria-label={`Net worth by month, from ${chartData[0]?.label} to ${chartData[chartData.length - 1]?.label}`}>
                            <ResponsiveContainer width="100%" height="100%">
                                <LineChart data={chartData} margin={{ top: 8, right: 16, bottom: 0, left: 8 }}>
                                    <CartesianGrid stroke={GRID} strokeDasharray="2 4" vertical={false} />
                                    <XAxis dataKey="label" tick={{ fill: AXIS_TEXT, fontSize: 11 }} tickLine={false} axisLine={{ stroke: GRID }} minTickGap={24} />
                                    <YAxis tickFormatter={compact} tick={{ fill: AXIS_TEXT, fontSize: 11 }} tickLine={false} axisLine={false} width={64} />
                                    <ReferenceLine y={0} stroke="#6b7280" />
                                    <Tooltip content={<ChartTooltip />} cursor={{ stroke: '#6b7280', strokeWidth: 1 }} />
                                    <Line type="monotone" dataKey="net" stroke={SERIES} strokeWidth={2} dot={false}
                                        activeDot={{ r: 5, fill: SERIES, stroke: '#111827', strokeWidth: 2 }} isAnimationActive={false} />
                                </LineChart>
                            </ResponsiveContainer>
                        </div>
                    )}
                    <p className="text-xs text-gray-500 mt-3">
                        Bank account history is worked back from each account's latest bank balance, so a gap in imported transactions
                        shifts the months before it. Homes and vehicles use the last value you recorded.
                    </p>
                </div>
            )}

            {/* --- Breakdown at the end of the range, with the change over it --- */}
            {latest && (
                <div className="bg-gray-900 border border-gray-800 rounded-2xl p-5">
                    <h2 className="text-sm font-bold text-gray-300 mb-3">
                        By account, {endsToday ? 'today' : dayLabel(latest.date)}
                    </h2>
                    <table className="w-full text-sm">
                        <thead>
                            <tr className="text-xs uppercase text-gray-500">
                                <th></th><th className="text-right font-bold pb-1">Balance</th>
                                {/* The change since the start of the range, or for checking its
                                    outlook, labelled on each group's own row: a
                                    checking balance swings with every payday and bill, so its
                                    change over a period says little */}
                                <th className="pb-1 pl-4"><span className="sr-only">Change, or outlook for checking</span></th>
                            </tr>
                        </thead>
                        <tbody>
                            {GROUP_TITLES.filter(([group]) => shown.some(a => a.group === group && latest.accounts[String(a.id)] !== undefined))
                                .map(([group, title], index, groups) => {
                                const rows = shown.filter(a => a.group === group && latest.accounts[String(a.id)] !== undefined);
                                // The groups after checking show a change: label it once, above the first of them
                                const firstChange = group !== 'checking' && groups.findIndex(([g]) => g !== 'checking') === index;
                                const at = (point: Point | undefined, a: AccountInfo) => Number(point?.accounts[String(a.id)] ?? 0);
                                const total = rows.reduce((sum, a) => sum + at(latest, a), 0);
                                const totalChange = rows.reduce((sum, a) => sum + at(latest, a) - at(first, a), 0);
                                const changeCell = (value: number) => (
                                    <span className={Math.abs(value) < 0.005 ? 'text-gray-600' : value > 0 ? 'text-green-400' : 'text-red-400'}>
                                        {Math.abs(value) < 0.005 ? '—' : `${value > 0 ? '+' : '−'}${money(Math.abs(value))}`}
                                    </span>
                                );
                                return (
                                    <Fragment key={group}>
                                        {firstChange && (
                                            <tr>
                                                <td></td><td></td>
                                                <td className="pt-4 pl-4 text-right text-xs font-bold uppercase text-gray-500 whitespace-nowrap">
                                                    Change since {first ? dayLabel(first.date) : ''}
                                                </td>
                                            </tr>
                                        )}
                                        <tr>
                                            <td className="pt-4 pb-1 text-xs font-bold uppercase text-gray-500">{title}</td>
                                            <td className="pt-4 pb-1 text-right text-xs font-bold text-gray-400 font-mono">{money(total)}</td>
                                            {group === 'checking'
                                                ? <td className="pt-4 pb-1 pl-4 text-right text-xs font-bold uppercase text-gray-500">Outlook</td>
                                                : <td className="pt-4 pb-1 pl-4 text-right text-xs font-bold font-mono">{changeCell(totalChange)}</td>}
                                        </tr>
                                        {rows.map(a => {
                                            const value = at(latest, a);
                                            // Accounts with transactions open their transaction list
                                            const opens = a.kind === 'bank' || a.kind === 'loan';
                                            const outlook = outlooks[String(a.id)];
                                            return (
                                                <tr key={a.id}
                                                    onClick={opens ? () => setViewing({ id: a.id, name: a.name }) : undefined}
                                                    onKeyDown={opens ? (e) => { if (e.key === 'Enter') setViewing({ id: a.id, name: a.name }); } : undefined}
                                                    tabIndex={opens ? 0 : undefined}
                                                    title={opens ? 'Show its transactions' : undefined}
                                                    className={`border-t border-gray-800/60 ${opens ? 'cursor-pointer hover:bg-gray-800/40 outline-none focus:bg-gray-800/60' : ''}`}>
                                                    <td className="py-1.5 text-gray-300">
                                                        {a.name}
                                                        <div className="text-[11px] text-amber-300/80">
                                                            {a.as_of_label}{a.as_of && <> · {dayLabel(a.as_of)}</>}
                                                        </div>
                                                    </td>
                                                    <td className={`py-1.5 text-right font-mono ${value < 0 ? 'text-red-300' : 'text-white'}`}>{money(value)}</td>
                                                    {group === 'checking' ? (
                                                    <td className="py-1.5 pl-4 text-right text-xs whitespace-nowrap">
                                                        {!outlook ? <span className="text-gray-600" title='No forecast: switch on "Dashboard Forecast" for this account in Settings → Accounts'>—</span> : outlook.will_decrement ? (
                                                            <span className="inline-flex items-center gap-1 text-red-400 font-bold" title={`Lowest ahead: ${money(Number(outlook.projected_balance))}`}>
                                                                <ArrowTrendingDownIcon className="w-4 h-4" /> Deficit from {outlook.deficit_date ? dayLabel(outlook.deficit_date) : 'today'}
                                                            </span>
                                                        ) : (
                                                            <span className="inline-flex items-center gap-1 text-green-400 font-bold">
                                                                <CheckCircleIcon className="w-4 h-4" /> Healthy <span className="font-normal text-gray-500">· low {money(Number(outlook.projected_balance))}</span>
                                                            </span>
                                                        )}
                                                    </td>
                                                    ) : (
                                                    <td className="py-1.5 pl-4 text-right font-mono">{changeCell(value - at(first, a))}</td>
                                                    )}
                                                </tr>
                                            );
                                        })}
                                    </Fragment>
                                );
                            })}
                        </tbody>
                    </table>
                </div>
            )}

            {/* --- Assets and loans --- */}
            <div className="bg-gray-900 border border-gray-800 rounded-2xl p-5 space-y-4">
                <div className="flex flex-wrap justify-between items-center gap-3">
                    <h2 className="text-sm font-bold text-gray-300">Assets and loans</h2>
                    <Link to="/settings" className="text-xs text-blue-400 hover:text-blue-300 underline">Add or edit them in Settings → Accounts</Link>
                </div>
                {holdings.length === 0 && <p className="text-sm text-gray-500">None yet. Add your home, vehicles, investments and loans in Settings → Accounts to see your full net worth.</p>}
                <div className="divide-y divide-gray-800">
                    {holdings.map(h => {
                        const loan = isLoan(h.kind);
                        const latestEntry = h.values[h.values.length - 1];
                        const current = latest?.accounts[String(h.id)];
                        const open = expanded === h.id;
                        // A home or vehicle and the loan secured on it, either way round
                        const securedBy = loan ? null : holdings.find(l => isLoan(l.kind) && l.loan?.asset_account === h.id);
                        const linked = loan ? h.loan?.asset_account : securedBy?.id;
                        const owedOnIt = securedBy ? latest?.accounts[String(securedBy.id)] : undefined;
                        return (
                            <div key={h.id} className="py-3">
                                <div className="flex flex-wrap items-center gap-3">
                                    <button onClick={() => setExpanded(open ? null : h.id)} className="text-gray-500 hover:text-white" aria-label={open ? 'Hide history' : 'Show history'}>
                                        {open ? <ChevronDownIcon className="w-4 h-4" /> : <ChevronRightIcon className="w-4 h-4" />}
                                    </button>
                                    <div className="flex-1 min-w-[12rem]">
                                        <p className="text-white font-bold">{h.name} <span className="text-xs font-normal text-gray-500">{KIND_LABELS[h.kind]}</span></p>
                                        <p className="text-xs text-gray-500">
                                            {loan && h.loan ? <>
                                                {h.loan.lender && <>{h.loan.lender} · </>}
                                                {Number(h.loan.interest_rate)}% · {money(Number(h.loan.payment_amount || h.loan.scheduled_payment), true)} per payment
                                                {h.loan_status?.payoff_date && <> · paid off {monthLabel(h.loan_status.payoff_date)} at this rate ({h.loan_status.payments_left} payments)</>}
                                                {h.loan_status?.escrow_balance != null && <> · escrow {money(Number(h.loan_status.escrow_balance), true)}</>}
                                            </> : <>
                                                {latestEntry ? <>Value recorded {latestEntry.date}{latestEntry.source && <> ({latestEntry.source})</>}</> : 'No value recorded yet: using the purchase price'}
                                                {securedBy && current !== undefined && owedOnIt !== undefined && <> · equity <span className="text-gray-300 font-mono">{money(Number(current) + Number(owedOnIt))}</span> after {securedBy.name}</>}
                                            </>}
                                        </p>
                                    </div>
                                    <p className={`font-mono font-bold ${current !== undefined && Number(current) < 0 ? 'text-red-300' : 'text-white'}`}>
                                        {current !== undefined ? money(Number(current)) : '—'}
                                    </p>
                                    <div className="flex flex-wrap gap-1">
                                        {(loan ? ([
                                            ['Record payment', () => setPaying(h)],
                                            ['Statement balance', () => startEntry(h, 'statement')],
                                            ...(h.loan_status?.escrow_account ? [
                                                ['Escrow bill paid', () => startEntry(h, 'escrow-payment')],
                                                ['Escrow balance', () => startEntry(h, 'escrow-statement')],
                                            ] : []),
                                        ] as [string, () => void][]) : ([['Update value', () => startEntry(h, 'value')]] as [string, () => void][]))
                                            .concat(linked ? [['Equity', () => setEquityFor(h.id)]] : []).map(([label, action]) => (
                                            <button key={label} onClick={action} className="text-xs font-bold px-2 py-1 rounded border border-gray-700 text-gray-300 hover:text-white">{label}</button>
                                        ))}
                                    </div>
                                </div>

                                {newValue?.id === h.id && (
                                    <div className="mt-3 ml-7 flex flex-wrap gap-2 items-center">
                                        <input type="date" aria-label="As of" value={newValue.date} onChange={e => setNewValue({ ...newValue, date: e.target.value })} className="bg-gray-800 border border-gray-700 rounded-lg text-white p-1.5 text-sm" />
                                        <span className="text-xs text-gray-400">{ENTRY_LABELS[newValue.mode]}</span>
                                        <input inputMode="decimal" aria-label={ENTRY_LABELS[newValue.mode]} placeholder="Amount" value={newValue.amount}
                                            onChange={e => setNewValue({ ...newValue, amount: e.target.value })} className="bg-gray-800 border border-gray-700 rounded-lg text-white p-1.5 text-sm w-36" />
                                        {newValue.mode === 'escrow-payment' && (
                                            <select aria-label="Paid for" value={newValue.kind} onChange={e => setNewValue({ ...newValue, kind: e.target.value })} className="bg-gray-800 border border-gray-700 rounded-lg text-white p-1.5 text-sm">
                                                <option value="property_tax">Property tax</option>
                                                <option value="insurance">Home insurance</option>
                                                <option value="pmi">PMI</option>
                                                <option value="other">Other</option>
                                            </select>
                                        )}
                                        <input aria-label="Note" placeholder={newValue.mode === 'value' ? 'e.g. Zillow estimate' : newValue.mode === 'escrow-payment' ? 'e.g. 2026 county tax bill' : 'e.g. October statement'} value={newValue.source}
                                            onChange={e => setNewValue({ ...newValue, source: e.target.value })} className="bg-gray-800 border border-gray-700 rounded-lg text-white p-1.5 text-sm flex-1 min-w-[10rem]" />
                                        <button onClick={saveValue} className="px-3 py-1.5 bg-blue-600 hover:bg-blue-500 text-white text-sm font-bold rounded-lg">Save</button>
                                        <button onClick={() => setNewValue(null)} className="px-2 py-1.5 text-gray-400 hover:text-white text-sm">Cancel</button>
                                    </div>
                                )}

                                {open && (
                                    <div className="mt-3 ml-7 text-sm">
                                        {h.values.length === 0 ? <p className="text-gray-500 text-xs">{loan ? 'No statement balances recorded.' : 'No values recorded.'}</p> : (
                                            <table className="text-xs">
                                                <tbody>
                                                    {[...h.values].reverse().map(v => (
                                                        <tr key={v.id}>
                                                            <td className="pr-4 py-0.5 text-gray-400 font-mono">{v.date}</td>
                                                            <td className="pr-4 py-0.5 text-white font-mono text-right">{money(Number(v.amount), true)}</td>
                                                            <td className="pr-4 py-0.5 text-gray-500">{v.source}</td>
                                                            <td><button onClick={() => deleteValue(h, v.id)} className="text-gray-600 hover:text-red-400" title="Delete this value"><TrashIcon className="w-4 h-4" /></button></td>
                                                        </tr>
                                                    ))}
                                                </tbody>
                                            </table>
                                        )}
                                    </div>
                                )}
                            </div>
                        );
                    })}
                </div>
            </div>

            {viewing && <AccountLedger account={viewing} onClose={() => setViewing(null)} />}
            {equityFor !== null && <EquityChart holdingId={equityFor} onClose={() => setEquityFor(null)} />}
            {paying && <LoanPaymentForm holding={paying} onClose={() => setPaying(null)} onSaved={() => { setPaying(null); load(); }} />}
        </div>
    );
};

export default NetWorth;
