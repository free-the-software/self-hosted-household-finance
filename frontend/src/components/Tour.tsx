// Copyright (C) 2026 free-the-software
// SPDX-License-Identifier: AGPL-3.0-or-later
// This file is part of Save More Money, under the GNU AGPL v3 or later: see LICENSE.
import { createContext, useCallback, useContext, useEffect, useLayoutEffect, useState } from 'react';
import type { CSSProperties, ReactNode } from 'react';
import { useLocation, useNavigate } from 'react-router';
import { XMarkIcon } from '@heroicons/react/24/outline';

// The getting-started tour: a card for each step that goes to the right
// page, highlights the real control (marked data-tour="...") and explains
// it. The page behind is dimmed and can't be clicked during the tour, so
// nothing is changed by accident. A step whose control isn't on the page
// (an empty inbox, say) just shows its card in the middle.

export interface TourStep {
    title: string;
    body: ReactNode;
    route?: string;          // path, optionally with ?query; omitted = stay put
    target?: string;         // data-tour name of the control to highlight
}

const DONE_KEY = 'finance-tour-done';
const FIND_FOR_MS = 2500;    // how long to wait for a page to draw its control
const PAD = 6;               // space around the highlighted control
const CARD_WIDTH = 360;

const TourContext = createContext<{ start: () => void }>({ start: () => undefined });
export const useTour = () => useContext(TourContext);

// Remembered per browser, so the tour offers itself once on a new install
export const tourWasSeen = () => {
    try { return localStorage.getItem(DONE_KEY) === '1'; } catch { return true; }
};
const markSeen = () => {
    try { localStorage.setItem(DONE_KEY, '1'); } catch { /* private window: it may offer itself again */ }
};

export const TourProvider = ({ steps, children }: { steps: TourStep[]; children: ReactNode }) => {
    const [index, setIndex] = useState<number | null>(null);
    const start = useCallback(() => setIndex(0), []);
    const finish = useCallback(() => { markSeen(); setIndex(null); }, []);
    return (
        <TourContext.Provider value={{ start }}>
            {children}
            {index !== null && <TourCard steps={steps} index={index} setIndex={setIndex} finish={finish} />}
        </TourContext.Provider>
    );
};

const TourCard = ({ steps, index, setIndex, finish }: {
    steps: TourStep[]; index: number; setIndex: (i: number) => void; finish: () => void;
}) => {
    const step = steps[index];
    const location = useLocation();
    const navigate = useNavigate();
    const [rect, setRect] = useState<DOMRect | null>(null);
    const [viewport, setViewport] = useState({ width: window.innerWidth, height: window.innerHeight });
    const last = index === steps.length - 1;

    // Go to the step's page
    useEffect(() => {
        if (step.route && location.pathname + location.search !== step.route) navigate(step.route);
    }, [step.route]);

    // Find its control (pages load their data first), scroll it into view, and follow it
    useLayoutEffect(() => {
        setRect(null);
        if (!step.target) return;
        let element: Element | null = null;
        let frame = 0;
        const began = Date.now();
        const follow = () => {
            if (!element || !element.isConnected) {
                element = document.querySelector(`[data-tour="${step.target}"]`);
                if (element) element.scrollIntoView({ block: 'center', behavior: 'smooth' });
                else if (Date.now() - began > FIND_FOR_MS) return;
            }
            if (element) {
                const next = element.getBoundingClientRect();
                setRect(prev => prev && prev.top === next.top && prev.left === next.left
                    && prev.width === next.width && prev.height === next.height ? prev : next);
            }
            frame = requestAnimationFrame(follow);
        };
        follow();
        return () => cancelAnimationFrame(frame);
    }, [index, step.target]);

    useEffect(() => {
        const resize = () => setViewport({ width: window.innerWidth, height: window.innerHeight });
        const keys = (e: KeyboardEvent) => {
            if (e.key === 'Escape') finish();
            else if (e.key === 'ArrowRight') last ? finish() : setIndex(index + 1);
            else if (e.key === 'ArrowLeft' && index > 0) setIndex(index - 1);
        };
        window.addEventListener('resize', resize);
        window.addEventListener('keydown', keys);
        return () => { window.removeEventListener('resize', resize); window.removeEventListener('keydown', keys); };
    }, [index, last]);

    // The highlight, kept on screen even for a control taller than the window
    const hole = rect && {
        top: Math.max(rect.top - PAD, 4),
        left: Math.max(rect.left - PAD, 4),
        bottom: Math.min(rect.bottom + PAD, viewport.height - 4),
        right: Math.min(rect.right + PAD, viewport.width - 4),
    };

    // The card goes below the control, else above it, else along the bottom of the window
    const width = Math.min(CARD_WIDTH, viewport.width - 32);
    const cardStyle: CSSProperties = { width };
    if (!hole) {
        cardStyle.top = '50%'; cardStyle.left = '50%'; cardStyle.transform = 'translate(-50%, -50%)';
    } else {
        cardStyle.left = Math.min(Math.max(hole.left, 16), viewport.width - width - 16);
        const below = viewport.height - hole.bottom;
        if (below >= 240) cardStyle.top = hole.bottom + 12;
        else if (hole.top >= 240) cardStyle.bottom = viewport.height - hole.top + 12;
        else cardStyle.bottom = 16;
    }

    return (
        <div className="fixed inset-0 z-[100]" role="dialog" aria-modal="true" aria-labelledby="tour-title">
            {/* Blocks clicks on the page; dims it when nothing is highlighted */}
            <div className={`absolute inset-0 ${hole ? '' : 'bg-black/70'}`} />
            {hole && (
                <div
                    className="absolute rounded-xl ring-2 ring-blue-400 pointer-events-none transition-all duration-200"
                    style={{ top: hole.top, left: hole.left, width: hole.right - hole.left, height: hole.bottom - hole.top, boxShadow: '0 0 0 9999px rgba(0,0,0,0.7)' }}
                />
            )}
            <div className="absolute bg-gray-900 border border-blue-500/50 rounded-2xl shadow-2xl p-5" style={cardStyle}>
                <div className="flex justify-between items-start gap-3 mb-2">
                    <h2 id="tour-title" className="text-lg font-bold text-white">{step.title}</h2>
                    <button onClick={finish} className="text-gray-500 hover:text-white" aria-label="Close the tour"><XMarkIcon className="w-5 h-5" /></button>
                </div>
                <div className="text-sm text-gray-300 space-y-2">{step.body}</div>
                <div className="flex items-center justify-between mt-5">
                    <span className="text-xs text-gray-500">{index + 1} of {steps.length}</span>
                    <div className="flex gap-2">
                        {!last && <button onClick={finish} className="px-3 py-1.5 text-sm text-gray-400 hover:text-white">Skip tour</button>}
                        {index > 0 && <button onClick={() => setIndex(index - 1)} className="px-3 py-1.5 text-sm text-gray-200 bg-gray-800 hover:bg-gray-700 rounded-lg">Back</button>}
                        <button onClick={() => last ? finish() : setIndex(index + 1)} autoFocus
                            className="px-4 py-1.5 text-sm font-bold text-white bg-blue-600 hover:bg-blue-500 rounded-lg">
                            {last ? 'Done' : 'Next'}
                        </button>
                    </div>
                </div>
            </div>
        </div>
    );
};
