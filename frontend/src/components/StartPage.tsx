// Copyright (C) 2026 free-the-software
// SPDX-License-Identifier: AGPL-3.0-or-later
// This file is part of Save More Money, under the GNU AGPL v3 or later: see LICENSE.
import { useEffect } from 'react';
import { useNavigate } from 'react-router';
import api from '../api';
import { DEFAULT_START } from './menu';

// The app's front door: opens the first page in the user's menu
const StartPage = () => {
    const navigate = useNavigate();
    useEffect(() => {
        api.get('/account/preferences/')
            .then(res => navigate((res.data.menu_order || [])[0] || DEFAULT_START, { replace: true }))
            .catch(() => navigate(DEFAULT_START, { replace: true }));
    }, []);
    return null;
};

export default StartPage;
