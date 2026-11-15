// Copyright (C) 2026 free-the-software
// SPDX-License-Identifier: AGPL-3.0-or-later
// This file is part of Save More Money, under the GNU AGPL v3 or later: see LICENSE.
import { useEffect, useState } from 'react';
import { XMarkIcon } from '@heroicons/react/24/outline';
import api from '../api';

// An account's transactions in date order with the running balance, from
// the bank's balance through everything pending and scheduled. Opened from
// the Cash Flow page and from the Net Worth page's list of accounts.

interface LedgerRow {
  id: number | string;
  date: string;
  description: string;
  amount: number;
  running_balance: number;
  status: 'VERIFIED' | 'PROJECTED';
  is_balance_calculated?: boolean;
}

const AccountLedger = ({ account, onClose }: { account: { id: number; name: string }; onClose: () => void }) => {
  const [ledger, setLedger] = useState<LedgerRow[]>([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    setLoading(true);
    api.get(`/accounts/${account.id}/ledger/`)
      .then((res: any) => setLedger(res.data))
      .catch((err: any) => console.error("Failed to load ledger", err))
      .finally(() => setLoading(false));
  }, [account.id]);

  return (
        <div className="fixed inset-0 z-[100] bg-black/90 backdrop-blur-md flex items-center justify-center p-4">
          <div className="bg-gray-950 border border-gray-800 rounded-2xl w-full max-w-5xl flex flex-col max-h-[90vh] shadow-[0_0_50px_rgba(0,0,0,1)] animate-in zoom-in-95 duration-200">
            <div className="flex justify-between items-center p-6 border-b border-gray-800 shrink-0">
              <div>
                <h3 className="text-xl font-bold text-white">Forecast: {account.name}</h3>
                <p className="text-xs text-gray-500 mt-1 uppercase tracking-widest font-bold">Projected cash flow sequence</p>
              </div>
              <button onClick={onClose} className="text-gray-500 hover:text-white transition-colors p-2">
                <XMarkIcon className="w-8 h-8" />
              </button>
            </div>

            <div className="flex-1 overflow-hidden p-6 flex flex-col">
              {loading ? (
                <div className="flex-1 flex items-center justify-center text-gray-500 font-bold uppercase tracking-widest">Running Simulation...</div>
              ) : (
                <div className="flex-1 overflow-y-auto rounded-xl border border-gray-800 bg-gray-900/50">
                  <table className="w-full text-left text-sm text-gray-400 relative">
                    <thead className="sticky top-0 bg-gray-950 text-[10px] uppercase text-gray-600 font-black border-b border-gray-800 z-10">
                      <tr>
                        <th className="px-4 py-3">Date</th>
                        <th className="px-4 py-3">Description</th>
                        <th className="px-4 py-3 text-right">Amount</th>
                        <th className="px-4 py-3 text-right">Running Balance</th>
                      </tr>
                    </thead>
                    <tbody className="divide-y divide-gray-800/50">
                      {ledger.map((row) => {
                        const isVerified = row.status === 'VERIFIED';
                        const isNegative = row.running_balance < 0;
                        return (
                          <tr key={row.id} className={`hover:bg-blue-500/5 transition-colors ${isVerified ? 'bg-blue-500/5' : ''}`}>
                            <td className="px-4 py-3 font-mono text-xs text-gray-500">{row.date}</td>
                            <td className="px-4 py-3">
                                <div className="flex items-center gap-2">
                                    <span className={isVerified ? 'text-blue-400 font-bold' : 'text-gray-200'}>{row.description}</span>
                                    {isVerified && <span className="bg-blue-600 text-white text-[8px] font-black px-1.5 py-0.5 rounded uppercase">Verified</span>}
                                </div>
                            </td>
                            <td className={`px-4 py-3 text-right font-mono text-xs ${row.amount < 0 ? 'text-red-400' : 'text-green-400'}`}>
                              {row.amount !== 0 ? Number(row.amount).toLocaleString(undefined, { minimumFractionDigits: 2 }) : '—'}
                            </td>
                            <td className={`px-4 py-3 text-right font-mono font-bold text-base ${isNegative ? 'text-red-500 bg-red-500/10' : 'text-white'}`}>
                              ${Number(row.running_balance).toLocaleString(undefined, { minimumFractionDigits: 2 })}
                            </td>
                          </tr>
                        );
                      })}
                    </tbody>
                  </table>
                </div>
              )}
            </div>
          </div>
        </div>
  );
};

export default AccountLedger;
