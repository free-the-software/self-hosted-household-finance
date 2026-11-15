// Copyright (C) 2026 free-the-software
// SPDX-License-Identifier: AGPL-3.0-or-later
// This file is part of Save More Money, under the GNU AGPL v3 or later: see LICENSE.
import { BrowserRouter, Routes, Route, Navigate } from 'react-router';

// 1. Import the "Frame" we just built
import Layout from './components/Layout';
import AuthWrapper from './components/AuthWrapper';

// 2. Import your Pages/Components
// (Double check these paths match where your files actually are!)
import TransactionList from './components/TransactionList';
import BudgetView from './components/BudgetView';
import RecurringTransactions from './components/RecurringTransactions';
import ImportReview from './components/ImportReview'; // Ensure this path is correct
import ReceiptInbox from './components/ReceiptsInbox'; // Ensure this path is correct
import Settings from './components/Settings'; // New Settings Page
import NetWorth from './components/NetWorth';
import StartPage from './components/StartPage';
import { TourProvider } from './components/Tour';
import tourSteps from './components/tourSteps';
import Dialogs from './components/Dialogs';

function App() {
  return (
    <AuthWrapper>
    {/* The app's own confirm windows and notices, for every page */}
    <Dialogs />
    <BrowserRouter>
    <TourProvider steps={tourSteps}>
      <Routes>
        {/* The Layout component wraps all these routes. 
            It provides the Sidebar that stays visible. */}
        <Route element={<Layout />}>
            
            {/* The front door opens the first page in the user's menu */}
            <Route path="/" element={<StartPage />} />
            <Route path="/transactions" element={<TransactionList />} />
            
            {/* MAIN PAGES */}
            {/* The old Cash Flow page: everything on it is now on Net Worth */}
            <Route path="/accounts" element={<Navigate to="/net-worth" replace />} />
            <Route path="/budget" element={<BudgetView />} />
            {/* Yearly Performance is now the year view of Budget to Actual */}
            <Route path="/performance" element={<Navigate to="/budget?period=year" replace />} />
            <Route path="/net-worth" element={<NetWorth />} />
            <Route path="/recurring" element={<RecurringTransactions />} />
            

            {/* NEW IMPORT PAGE */}
            <Route path="/import" element={<ImportReview />} />
            {/* NEW RECEIPT INBOX PAGE */}
            <Route path="/receipts" element={<ReceiptInbox />} />
            {/* OPTIONAL: Catch-all redirect (sends 404s back home) */}
            <Route path="*" element={<Navigate to="/" replace />} />
            <Route path="/settings" element={<Settings />} />

        </Route>
      </Routes>
    </TourProvider>
    </BrowserRouter>
    </AuthWrapper>
  );
}

export default App;