// Copyright (C) 2026 free-the-software
// SPDX-License-Identifier: AGPL-3.0-or-later
// This file is part of Save More Money, under the GNU AGPL v3 or later: see LICENSE.
import { useId } from 'react';

// The app's logo: a rising trend over growing bars, on a blue-to-teal tile,
// next to the name. The same mark is the browser tab icon (public/favicon.svg).

export const LogoMark = ({ className = 'w-8 h-8' }: { className?: string }) => {
    const gradient = useId();   // unique per copy, so two logos on a page don't clash
    return (
        <svg viewBox="0 0 64 64" className={className} aria-hidden="true">
            <defs>
                <linearGradient id={gradient} x1="0" y1="0" x2="1" y2="1">
                    <stop offset="0" stopColor="#2563eb" />
                    <stop offset="1" stopColor="#14b8a6" />
                </linearGradient>
            </defs>
            <rect width="64" height="64" rx="15" fill={`url(#${gradient})`} />
            <rect x="13" y="38" width="8" height="13" rx="2" fill="#fff" fillOpacity=".35" />
            <rect x="28" y="31" width="8" height="20" rx="2" fill="#fff" fillOpacity=".35" />
            <rect x="43" y="24" width="8" height="27" rx="2" fill="#fff" fillOpacity=".35" />
            <path d="M12 41 L25 31 L33 36 L49 19" fill="none" stroke="#fff" strokeWidth="4.5" strokeLinecap="round" strokeLinejoin="round" />
            <path d="M40 18 L50 18 L50 28" fill="none" stroke="#fff" strokeWidth="4.5" strokeLinecap="round" strokeLinejoin="round" />
        </svg>
    );
};

const Logo = ({ size = 'md' }: { size?: 'md' | 'lg' }) => (
    <span className={`inline-flex items-center ${size === 'lg' ? 'gap-3' : 'gap-2.5'}`}>
        <LogoMark className={size === 'lg' ? 'w-11 h-11' : 'w-8 h-8'} />
        <span className={`font-bold tracking-wider whitespace-nowrap ${size === 'lg' ? 'text-2xl' : 'text-xl'}`}>
            SAVE MORE{' '}
            <span className="bg-gradient-to-r from-blue-500 to-teal-400 bg-clip-text text-transparent">MONEY</span>
        </span>
    </span>
);

export default Logo;
