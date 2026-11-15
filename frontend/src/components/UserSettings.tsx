// Copyright (C) 2026 free-the-software
// SPDX-License-Identifier: AGPL-3.0-or-later
// This file is part of Save More Money, under the GNU AGPL v3 or later: see LICENSE.
import { useEffect, useState } from 'react';
import type { FormEvent } from 'react';
import { PlusCircleIcon, TrashIcon, XMarkIcon } from '@heroicons/react/24/outline';
import api from '../api';
import type { Session } from '../api';
import { PasswordFields } from './MyAccount';

// Settings → Users (admins only; finance/auth.py UserViewSet). Everyone
// signed in shares the same ledger; admins also manage users, backups and
// storage. The server keeps at least one active admin.

interface User {
    id: number; username: string; name: string; email: string;
    is_admin: boolean; is_active: boolean; last_login: string | null; date_joined: string;
}
type Editing = { user: User | null; username: string; name: string; email: string; is_admin: boolean; is_active: boolean };

const errorText = (err: any, fallback: string) => {
    const data = err.response?.data;
    if (!data) return fallback;
    if (typeof data.error === 'string') return data.error;
    return Object.values(data).flat().join(' ') || fallback;
};
const when = (iso: string | null) => iso ? new Date(iso).toLocaleDateString('en-US', { month: 'short', day: 'numeric', year: 'numeric' }) : 'Never';

const UserSettings = ({ session }: { session: Session | null }) => {
    const [users, setUsers] = useState<User[]>([]);
    const [error, setError] = useState('');
    const [editing, setEditing] = useState<Editing | null>(null);
    const [password, setPassword] = useState({ password: '', repeat: '' });
    const [settingPasswordFor, setSettingPasswordFor] = useState<User | null>(null);
    const [deleting, setDeleting] = useState<User | null>(null);
    const [formError, setFormError] = useState('');
    const passwords = session?.password_login;

    const load = () => api.get('/users/').then(res => { setUsers(res.data); setError(''); })
        .catch(err => setError(errorText(err, 'Could not load the users.')));
    useEffect(() => { load(); }, []);

    const close = () => { setEditing(null); setSettingPasswordFor(null); setPassword({ password: '', repeat: '' }); setFormError(''); };

    const saveUser = async (e: FormEvent) => {
        e.preventDefault();
        if (!editing) return;
        const body: Record<string, any> = { name: editing.name, email: editing.email, is_admin: editing.is_admin, is_active: editing.is_active };
        try {
            if (editing.user) {
                await api.patch(`/users/${editing.user.id}/`, body);
            } else {
                if (password.password !== password.repeat) { setFormError("The two passwords don't match."); return; }
                await api.post('/users/', { ...body, username: editing.username, password: password.password });
            }
            close();
            load();
        } catch (err) {
            setFormError(errorText(err, 'Could not save.'));
        }
    };

    const savePassword = async (e: FormEvent) => {
        e.preventDefault();
        if (!settingPasswordFor) return;
        if (password.password !== password.repeat) { setFormError("The two passwords don't match."); return; }
        try {
            await api.post(`/users/${settingPasswordFor.id}/set-password/`, { new_password: password.password });
            close();
        } catch (err) {
            setFormError(errorText(err, 'Could not set the password.'));
        }
    };

    const remove = async () => {
        if (!deleting) return;
        try {
            await api.delete(`/users/${deleting.id}/`);
            setDeleting(null);
            load();
        } catch (err) {
            setError(errorText(err, 'Could not delete the user.'));
            setDeleting(null);
        }
    };

    const input = 'w-full bg-gray-800 border border-gray-700 rounded-lg text-white p-2.5 text-sm focus:border-blue-500 outline-none disabled:opacity-50';
    const label = 'block text-xs font-bold text-gray-400 uppercase mb-1';
    const isMe = (u: User | null) => !!u && u.username === session?.username;

    return (
        <div className="animate-in fade-in duration-300 space-y-4">
            <div className="flex flex-wrap items-center gap-4">
                {passwords && (
                    <button onClick={() => setEditing({ user: null, username: '', name: '', email: '', is_admin: false, is_active: true })}
                        className="bg-blue-600 hover:bg-blue-500 text-white px-4 py-2 rounded-lg text-sm font-bold transition-colors flex items-center shadow-lg">
                        <PlusCircleIcon className="w-5 h-5 mr-2" /> Add User
                    </button>
                )}
                <p className="text-xs text-gray-400">
                    Everyone here sees and edits the same ledger. Admins can also manage users, backups and storage.
                    {!passwords && ' With single sign-on, people are added in your identity provider and appear here after their first sign-in.'}
                </p>
            </div>
            {error && <p className="text-sm text-red-400">{error}</p>}

            <div className="bg-gray-900 rounded-xl border border-gray-800 overflow-x-auto shadow-xl">
                <table className="w-full text-left text-sm text-gray-400">
                    <thead className="bg-gray-950 text-xs uppercase text-gray-500 font-bold border-b border-gray-800">
                        <tr>
                            <th className="px-6 py-4">User</th>
                            <th className="px-6 py-4">Role</th>
                            <th className="px-6 py-4">Last signed in</th>
                            <th className="px-6 py-4 text-right">Actions</th>
                        </tr>
                    </thead>
                    <tbody className="divide-y divide-gray-800">
                        {users.map(u => (
                            <tr key={u.id} className={`hover:bg-gray-800/40 cursor-pointer ${u.is_active ? '' : 'opacity-50'}`}
                                onClick={() => setEditing({ user: u, username: u.username, name: u.name, email: u.email, is_admin: u.is_admin, is_active: u.is_active })}>
                                <td className="px-6 py-4">
                                    <p className="text-white font-medium">{u.username}{isMe(u) && <span className="ml-2 text-xs text-blue-400">you</span>}</p>
                                    <p className="text-xs text-gray-500">{[u.name, u.email].filter(Boolean).join(' · ')}</p>
                                </td>
                                <td className="px-6 py-4">
                                    {u.is_admin ? <span className="text-blue-300 font-medium">Admin</span> : 'User'}
                                    {!u.is_active && <span className="ml-2 text-xs text-gray-500">(switched off)</span>}
                                </td>
                                <td className="px-6 py-4">{when(u.last_login)}</td>
                                <td className="px-6 py-4 text-right whitespace-nowrap" onClick={e => e.stopPropagation()}>
                                    {passwords && !isMe(u) && (
                                        <button onClick={() => setSettingPasswordFor(u)} className="text-xs font-bold px-2 py-1 rounded border border-gray-700 text-gray-300 hover:text-white mr-2">
                                            Set password
                                        </button>
                                    )}
                                    {!isMe(u) && (
                                        <button onClick={() => setDeleting(u)} className="p-1.5 text-gray-500 hover:text-red-400 align-middle" aria-label={`Delete ${u.username}`} title="Delete">
                                            <TrashIcon className="w-5 h-5" />
                                        </button>
                                    )}
                                </td>
                            </tr>
                        ))}
                    </tbody>
                </table>
            </div>

            {(editing || settingPasswordFor) && (
                <div className="fixed inset-0 bg-black/80 backdrop-blur-sm flex items-center justify-center z-50 p-4">
                    <form onSubmit={editing ? saveUser : savePassword} className="bg-gray-900 border border-gray-700 rounded-2xl shadow-2xl w-full max-w-md overflow-hidden">
                        <div className="p-5 border-b border-gray-800 flex justify-between items-center bg-gray-950">
                            <h3 className="text-lg font-bold text-white">
                                {settingPasswordFor ? `New password for ${settingPasswordFor.username}` : editing?.user ? `Edit ${editing.user.username}` : 'Add a user'}
                            </h3>
                            <button type="button" onClick={close} className="text-gray-500 hover:text-white" aria-label="Close"><XMarkIcon className="w-6 h-6" /></button>
                        </div>
                        <div className="p-5 space-y-4">
                            {editing && <>
                                <div>
                                    <label htmlFor="u-name" className={label}>Username</label>
                                    <input id="u-name" required disabled={!!editing.user} autoComplete="off" value={editing.username}
                                        onChange={e => setEditing({ ...editing, username: e.target.value })} className={input} />
                                </div>
                                <div className="grid grid-cols-2 gap-3">
                                    <div>
                                        <label htmlFor="u-full" className={label}>Name</label>
                                        <input id="u-full" value={editing.name} onChange={e => setEditing({ ...editing, name: e.target.value })} className={input} />
                                    </div>
                                    <div>
                                        <label htmlFor="u-email" className={label}>Email</label>
                                        <input id="u-email" type="email" value={editing.email} onChange={e => setEditing({ ...editing, email: e.target.value })} className={input} />
                                    </div>
                                </div>
                                <label className="flex items-start gap-2 text-sm text-gray-300">
                                    <input type="checkbox" className="mt-1" checked={editing.is_admin} disabled={isMe(editing.user)}
                                        onChange={e => setEditing({ ...editing, is_admin: e.target.checked })} />
                                    <span>Admin <span className="block text-xs text-gray-500">Can manage users, backups and storage.</span></span>
                                </label>
                                {editing.user && (
                                    <label className="flex items-start gap-2 text-sm text-gray-300">
                                        <input type="checkbox" className="mt-1" checked={editing.is_active} disabled={isMe(editing.user)}
                                            onChange={e => setEditing({ ...editing, is_active: e.target.checked })} />
                                        <span>Can sign in <span className="block text-xs text-gray-500">Untick to switch the account off without deleting it.</span></span>
                                    </label>
                                )}
                                {isMe(editing.user) && <p className="text-xs text-gray-500">You can't remove your own admin rights or switch yourself off. Another admin can.</p>}
                            </>}
                            {(settingPasswordFor || (editing && !editing.user)) && <PasswordFields value={password} onChange={setPassword} idPrefix="u-pw" />}
                            {(settingPasswordFor || (editing && !editing.user)) && (
                                <p className="text-xs text-gray-500">
                                    A one-time password: give it to them, and they choose their own when they sign in.
                                    {settingPasswordFor && ' They are signed out everywhere else.'}
                                </p>
                            )}
                            {formError && <p className="text-sm text-red-400" role="alert">{formError}</p>}
                        </div>
                        <div className="p-4 border-t border-gray-800 bg-gray-950 flex justify-end gap-3">
                            <button type="button" onClick={close} className="px-4 py-2 text-gray-400 hover:text-white">Cancel</button>
                            <button type="submit" className="px-5 py-2 bg-blue-600 hover:bg-blue-500 text-white font-bold rounded-lg">
                                {settingPasswordFor ? 'Set password' : editing?.user ? 'Save' : 'Add user'}
                            </button>
                        </div>
                    </form>
                </div>
            )}

            {deleting && (
                <div className="fixed inset-0 bg-black/80 backdrop-blur-sm flex items-center justify-center z-50 p-4">
                    <div className="bg-gray-900 border border-gray-700 rounded-2xl shadow-2xl w-full max-w-md p-6 space-y-4">
                        <h3 className="text-lg font-bold text-white">Delete {deleting.username}?</h3>
                        <p className="text-sm text-gray-400">They won't be able to sign in. The ledger isn't affected. To keep the account for later, switch it off instead.</p>
                        <div className="flex justify-end gap-3">
                            <button onClick={() => setDeleting(null)} className="px-4 py-2 text-gray-400 hover:text-white">Cancel</button>
                            <button onClick={remove} className="px-5 py-2 bg-red-600 hover:bg-red-500 text-white font-bold rounded-lg">Delete</button>
                        </div>
                    </div>
                </div>
            )}
        </div>
    );
};

export default UserSettings;
