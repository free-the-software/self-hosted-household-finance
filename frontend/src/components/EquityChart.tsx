// Copyright (C) 2026 free-the-software
// SPDX-License-Identifier: AGPL-3.0-or-later
// This file is part of Save More Money, under the GNU AGPL v3 or later: see LICENSE.
import { useEffect, useState } from 'react';
import { Bar, BarChart, CartesianGrid, Cell, ReferenceLine, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts';
import { XMarkIcon } from '@heroicons/react/24/outline';
import api from '../api';

// A home or vehicle and the loan secured on it, month by month over the
// loan's life (finance/loans.py equity_history). Each bar is the asset's
// value, split into what is owed and the equity on top; months ahead follow
// the loan's schedule with the value held at its latest figure, and are
// drawn lighter.

interface EquityPoint { date: string; value: string | null; owed: string; equity: string | null; source: 'actual' | 'schedule' | 'projected'; }
interface Equity {
    loan: { id: number; name: string };
    asset: { id: number; name: string };
    today: EquityPoint;
    payoff_date: string | null;
    covers_interest: boolean;
    points: EquityPoint[];
}

// Categorical slots 2 and 1, validated against the dark surface (#111827)
const OWED = '#d95926';
const EQUITY = '#3987e5';
const SURFACE = '#111827';
const GRID = '#374151';
const AXIS_TEXT = '#9ca3af';
const AHEAD_OPACITY = 0.45;
// Thin bars lose their gaps: beyond this many, bars touch
const GAPS_UP_TO = 120;

const money = (value: number) => value.toLocaleString('en-US', { style: 'currency', currency: 'USD', maximumFractionDigits: 0 });
const compact = (value: number) => value.toLocaleString('en-US', { style: 'currency', currency: 'USD', notation: 'compact', maximumFractionDigits: 1 });
const asDate = (iso: string) => new Date(iso + 'T00:00:00');
const monthLabel = (iso: string) => asDate(iso).toLocaleDateString('en-US', { month: 'short', year: 'numeric' });
const SOURCE_LABELS = { actual: 'Actual balance', schedule: 'From the loan schedule', projected: 'Ahead, on schedule' };

const EquityTooltip = ({ active, payload }: any) => {
    if (!active || !payload?.length) return null;
    const p = payload[0].payload;
    return (
        <div className="bg-gray-950 border border-gray-700 rounded-lg px-3 py-2 text-xs shadow-xl">
            <p className="text-gray-400 mb-1">{monthLabel(p.date)} · {SOURCE_LABELS[p.source as EquityPoint['source']]}</p>
            {p.value !== null && <p className="text-white font-mono">Value {money(p.value)}</p>}
            <p className="text-gray-300 font-mono"><span className="inline-block w-2 h-2 rounded-sm mr-1.5" style={{ background: OWED }} />Owed {money(p.owed)}</p>
            {p.equity !== null && <p className="text-white font-bold font-mono"><span className="inline-block w-2 h-2 rounded-sm mr-1.5" style={{ background: EQUITY }} />Equity {money(p.equity)}</p>}
        </div>
    );
};

const EquityChart = ({ holdingId, onClose }: { holdingId: number; onClose: () => void }) => {
    const [data, setData] = useState<Equity | null>(null);
    const [error, setError] = useState('');
    const [showTable, setShowTable] = useState(false);

    useEffect(() => {
        api.get(`/holdings/${holdingId}/equity/`)
            .then(res => setData(res.data))
            .catch(err => setError(err.response?.data?.error || 'Could not load the equity history.'));
    }, [holdingId]);

    const rows = (data?.points || []).map(p => ({
        date: p.date, source: p.source,
        value: p.value === null ? null : Number(p.value),
        owed: Number(p.owed),
        equity: p.equity === null ? null : Number(p.equity),
    }));
    const gaps = rows.length <= GAPS_UP_TO;
    const now = data?.today;
    const tiles: [string, string | null, string?][] = now ? [
        ['Value today', now.value === null ? null : money(Number(now.value))],
        ['Owed today', money(Number(now.owed)), OWED],
        ['Equity today', now.equity === null ? null : money(Number(now.equity)), EQUITY],
        ['Paid off', data?.payoff_date ? monthLabel(data.payoff_date) : null],
    ] : [];

    return (
        <div className="fixed inset-0 bg-black/80 backdrop-blur-sm flex items-center justify-center z-50 p-4">
            <div className="bg-gray-900 border border-gray-700 rounded-2xl shadow-2xl w-full max-w-5xl max-h-[90vh] flex flex-col overflow-hidden">
                <div className="p-5 border-b border-gray-800 flex justify-between items-center bg-gray-950">
                    <div>
                        <h3 className="text-lg font-bold text-white">Equity: {data ? `${data.asset.name} and ${data.loan.name}` : '…'}</h3>
                        <p className="text-xs text-gray-400 mt-1">Each bar is what it's worth: what you owe, with your equity on top.</p>
                    </div>
                    <button onClick={onClose} className="text-gray-500 hover:text-white" aria-label="Close"><XMarkIcon className="w-6 h-6" /></button>
                </div>
                <div className="p-5 overflow-y-auto space-y-4">
                    {error && <p className="text-sm text-red-400">{error}</p>}
                    {!data && !error && <p className="text-sm text-gray-400">Loading...</p>}
                    {data && <>
                        <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
                            {tiles.map(([label, value, colour]) => (
                                <div key={label} className="bg-gray-800/50 border border-gray-800 rounded-xl p-3">
                                    <p className="text-xs font-bold text-gray-400 uppercase flex items-center gap-1.5">
                                        {colour && <span className="inline-block w-2.5 h-2.5 rounded-sm" style={{ background: colour }} />}{label}
                                    </p>
                                    <p className="text-xl font-bold text-white font-mono mt-1">{value ?? '—'}</p>
                                </div>
                            ))}
                        </div>
                        {!data.covers_interest && (
                            <p className="text-sm text-amber-300">The regular payment doesn't cover the interest at this balance, so nothing is shown ahead. Check the loan's payment amount in Settings.</p>
                        )}

                        <div className="flex flex-wrap items-center justify-between gap-3">
                            <div className="flex flex-wrap items-center gap-4 text-xs text-gray-300">
                                <span className="flex items-center gap-1.5"><span className="inline-block w-3 h-3 rounded-sm" style={{ background: OWED }} />Owed</span>
                                <span className="flex items-center gap-1.5"><span className="inline-block w-3 h-3 rounded-sm" style={{ background: EQUITY }} />Equity</span>
                                <span className="flex items-center gap-1.5 text-gray-400"><span className="inline-block w-3 h-3 rounded-sm" style={{ background: EQUITY, opacity: AHEAD_OPACITY }} />Lighter: ahead, on schedule</span>
                            </div>
                            <button onClick={() => setShowTable(!showTable)} className="text-xs font-bold px-2 py-1 rounded border border-gray-700 text-gray-300 hover:text-white">
                                {showTable ? 'Show chart' : 'Show table'}
                            </button>
                        </div>

                        {showTable ? (
                            <div className="overflow-x-auto max-h-96">
                                <table className="w-full text-sm font-mono text-right">
                                    <thead className="text-xs text-gray-500 uppercase font-sans sticky top-0 bg-gray-900">
                                        <tr><th className="text-left py-1.5">Month</th><th>Value</th><th>Owed</th><th>Equity</th><th className="text-left pl-4">Figures</th></tr>
                                    </thead>
                                    <tbody className="divide-y divide-gray-800 text-gray-300">
                                        {rows.map(r => (
                                            <tr key={r.date} className={r.source === 'projected' ? 'text-gray-500' : ''}>
                                                <td className="text-left py-1 font-sans">{r.date === now?.date ? 'Today' : monthLabel(r.date)}</td>
                                                <td>{r.value === null ? '—' : money(r.value)}</td>
                                                <td>{money(r.owed)}</td>
                                                <td className={r.source === 'projected' ? '' : 'text-white font-bold'}>{r.equity === null ? '—' : money(r.equity)}</td>
                                                <td className="text-left pl-4 font-sans text-xs">{SOURCE_LABELS[r.source]}</td>
                                            </tr>
                                        ))}
                                    </tbody>
                                </table>
                            </div>
                        ) : (
                            <div className="h-80" role="img" aria-label={`Value, owed and equity by month for ${data.asset.name}, ${monthLabel(rows[0].date)} to ${monthLabel(rows[rows.length - 1].date)}`}>
                                <ResponsiveContainer width="100%" height="100%">
                                    <BarChart data={rows} stackOffset="sign" barCategoryGap={gaps ? 2 : 0} margin={{ top: 8, right: 16, bottom: 0, left: 8 }}>
                                        <CartesianGrid stroke={GRID} strokeDasharray="2 4" vertical={false} />
                                        <XAxis dataKey="date" tickFormatter={monthLabel} tick={{ fill: AXIS_TEXT, fontSize: 11 }} tickLine={false} axisLine={{ stroke: GRID }} minTickGap={32} />
                                        <YAxis tickFormatter={compact} tick={{ fill: AXIS_TEXT, fontSize: 11 }} tickLine={false} axisLine={false} width={64} />
                                        <ReferenceLine y={0} stroke="#6b7280" />
                                        {now && <ReferenceLine x={now.date} stroke="#ef4444" strokeWidth={2} label={{ value: 'Today', fill: '#fca5a5', fontSize: 11, position: 'insideTopLeft' }} />}
                                        <Tooltip content={<EquityTooltip />} cursor={{ fill: '#ffffff', fillOpacity: 0.06 }} />
                                        {([['owed', OWED], ['equity', EQUITY]] as const).map(([key, colour]) => (
                                            <Bar key={key} dataKey={key} stackId="loan" fill={colour} isAnimationActive={false}
                                                stroke={gaps ? SURFACE : undefined} strokeWidth={gaps ? 1 : 0}>
                                                {rows.map(r => <Cell key={r.date} fillOpacity={r.source === 'projected' ? AHEAD_OPACITY : 1} />)}
                                            </Bar>
                                        ))}
                                    </BarChart>
                                </ResponsiveContainer>
                            </div>
                        )}
                        <p className="text-xs text-gray-500">
                            Up to today: your recorded values and the loan's actual balance (the loan schedule before its first known balance).
                            Ahead: the regular payment plus any usual extra principal on every due date, with the value kept at its latest figure.
                            Record a new value or a statement balance to update it.
                        </p>
                    </>}
                </div>
            </div>
        </div>
    );
};

export default EquityChart;
