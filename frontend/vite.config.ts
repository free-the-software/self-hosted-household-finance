// Copyright (C) 2026 free-the-software
// SPDX-License-Identifier: AGPL-3.0-or-later
// This file is part of Save More Money, under the GNU AGPL v3 or later: see LICENSE.
import { defineConfig, loadEnv } from 'vite'
import react from '@vitejs/plugin-react'

// https://vitejs.dev/config/
export default defineConfig(({ mode }) => {
  // Load env file from the current directory. 
  // The third parameter '' loads all variables regardless of prefix.
  const env = loadEnv(mode, process.cwd(), '');
  const domain = env.DOMAIN_NAME || 'localhost';

  // Paths Django owns. Traefik does this routing in prod.
  const toDjango = {
    target: 'http://backend:8000',
    changeOrigin: true,
    secure: false,
    // Pass the browser's real host:port (X-Forwarded-Host) so Django builds
    // the right OIDC callback URL when Authentik is tested in dev.
    xfwd: true,
    configure: (proxy: any) => {
      proxy.on('proxyReq', (proxyReq: any) => {
        proxyReq.setHeader('Host', domain); // Now uses the variable
      });
    },
  };

  return {
    plugins: [react()],
    // The release version shown in the menu (frontend/Dockerfile's APP_VERSION)
    define: {
      __APP_VERSION__: JSON.stringify(process.env.APP_VERSION || env.APP_VERSION || ''),
      // Where people using the app can get its source code (the AGPL asks a
      // network service to offer it). Running a changed version for others?
      // Point SOURCE_URL at your changed code.
      __SOURCE_URL__: JSON.stringify(process.env.SOURCE_URL || env.SOURCE_URL || 'https://github.com/free-the-software/self-hosted-household-finance'),
    },
    
    // --- SAFARI 14 DESTRUCTURING FIX ---
    build: {
      target: 'es2022'
    },
    esbuild: {
      target: 'es2022'
    },
    optimizeDeps: {
      esbuildOptions: {
        target: 'es2022'
      }
    },
    // -----------------------------------

    server: {
      allowedHosts: [domain], // Now uses the variable
      port: 5173,
      host: true,
      // The page's live-reload connection goes to the port the browser uses
      // (another one when a second copy runs alongside)
      hmr: { clientPort: Number(env.DEV_APP_PORT) || 5173 },
      proxy: {
        '/api': toDjango,
        '/media': toDjango,
        // Login pages: Authentik callback (prod-style) or the Django admin
        // login used in dev, plus the admin's CSS/JS.
        '/oidc': toDjango,
        '/admin': toDjango,
        '/static': toDjango,
      },
    }
  }
})