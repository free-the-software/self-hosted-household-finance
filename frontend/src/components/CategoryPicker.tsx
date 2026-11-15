// Copyright (C) 2026 free-the-software
// SPDX-License-Identifier: AGPL-3.0-or-later
// This file is part of Save More Money, under the GNU AGPL v3 or later: see LICENSE.
import { useEffect, useMemo, useRef, useState } from 'react';
import type { CategoryGroup } from './CategoryAllocation';

// A category box you can type in: a few letters filter the list to matching
// subcategories (or whole categories), arrow keys move, Enter picks.

interface Props {
    value: number | null | undefined;
    onChange: (id: number | null) => void;
    categories: CategoryGroup[];
    // Offer "Uncategorized" as a choice (a single category may be empty; a split line may not)
    allowNone?: boolean;
    placeholder?: string;
    id?: string;
    ariaLabel?: string;
    compact?: boolean;
}

interface Option { id: number | null; name: string; group: string; }

const CategoryPicker = ({ value, onChange, categories, allowNone = false, placeholder = 'Type to search categories...', id, ariaLabel, compact = false }: Props) => {
    const [open, setOpen] = useState(false);
    const [query, setQuery] = useState('');
    const [active, setActive] = useState(0);
    const box = useRef<HTMLDivElement>(null);
    const list = useRef<HTMLUListElement>(null);

    const options: Option[] = useMemo(() => {
        const all: Option[] = categories.flatMap(cat => cat.subcategories.map(sub => ({ id: sub.id, name: sub.name, group: cat.name })));
        return allowNone ? [{ id: null, name: 'Uncategorized', group: '' }, ...all] : all;
    }, [categories, allowNone]);

    const selected = options.find(option => option.id === (value || null) && (option.id !== null || allowNone));

    // Every typed word must appear in the subcategory or its category: "din out" finds Dining Out
    const shown = useMemo(() => {
        const words = query.trim().toLowerCase().split(/\s+/).filter(Boolean);
        if (!words.length) return options;
        return options.filter(option => {
            const text = `${option.name} ${option.group}`.toLowerCase();
            return words.every(word => text.includes(word));
        });
    }, [options, query]);

    // Close when clicking elsewhere
    useEffect(() => {
        if (!open) return;
        const onClick = (e: MouseEvent) => { if (box.current && !box.current.contains(e.target as Node)) { setOpen(false); setQuery(''); } };
        document.addEventListener('mousedown', onClick);
        return () => document.removeEventListener('mousedown', onClick);
    }, [open]);

    // Keep the highlighted option in view
    useEffect(() => {
        list.current?.querySelector<HTMLElement>(`[data-index="${active}"]`)?.scrollIntoView({ block: 'nearest' });
    }, [active, open]);

    const choose = (option: Option) => {
        onChange(option.id);
        setOpen(false);
        setQuery('');
    };

    const onKeyDown = (e: React.KeyboardEvent<HTMLInputElement>) => {
        if (e.key === 'ArrowDown') { e.preventDefault(); setOpen(true); setActive(i => Math.min(i + 1, shown.length - 1)); }
        else if (e.key === 'ArrowUp') { e.preventDefault(); setActive(i => Math.max(i - 1, 0)); }
        else if (e.key === 'Enter') { if (open && shown[active]) { e.preventDefault(); choose(shown[active]); } }
        else if (e.key === 'Escape') { setOpen(false); setQuery(''); }
        else if (e.key === 'Tab') { setOpen(false); setQuery(''); }
    };

    const listId = id ? `${id}-options` : undefined;
    const padding = compact ? 'p-2 text-sm' : 'p-2';

    return (
        <div ref={box} className="relative w-full">
            <input
                id={id}
                type="text"
                role="combobox"
                aria-label={ariaLabel}
                aria-expanded={open}
                aria-controls={listId}
                autoComplete="off"
                value={open ? query : (selected ? (selected.group ? `${selected.name} (${selected.group})` : selected.name) : '')}
                placeholder={selected ? undefined : placeholder}
                onFocus={() => { setOpen(true); setQuery(''); setActive(0); }}
                onClick={() => setOpen(true)}
                onChange={e => { setQuery(e.target.value); setOpen(true); setActive(0); }}
                onKeyDown={onKeyDown}
                className={`w-full bg-gray-800 border border-gray-700 rounded-lg text-white ${padding} focus:border-blue-500 outline-none`}
            />
            {open && (
                <ul id={listId} ref={list} role="listbox" className="absolute z-50 mt-1 w-full max-h-64 overflow-y-auto bg-gray-900 border border-gray-700 rounded-lg shadow-2xl py-1">
                    {shown.length === 0 && <li className="px-3 py-2 text-sm text-gray-500">No category matches "{query}"</li>}
                    {shown.map((option, index) => (
                        <li
                            key={option.id ?? 'none'}
                            data-index={index}
                            role="option"
                            aria-selected={option.id === (value || null)}
                            onMouseDown={e => { e.preventDefault(); choose(option); }}
                            onMouseEnter={() => setActive(index)}
                            className={`px-3 py-1.5 text-sm cursor-pointer flex justify-between gap-3 ${index === active ? 'bg-blue-600 text-white' : 'text-gray-200'} ${option.id === null ? 'italic' : ''}`}
                        >
                            <span>{option.name}</span>
                            {option.group && <span className={`text-xs ${index === active ? 'text-blue-100' : 'text-gray-500'}`}>{option.group}</span>}
                        </li>
                    ))}
                </ul>
            )}
        </div>
    );
};

export default CategoryPicker;
