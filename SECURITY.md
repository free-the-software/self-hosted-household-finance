# Security

This app holds people's financial history, so security problems matter. Thank you for taking the time to report one.

## Reporting a problem

**Please don't open a public issue for a security problem.** Report it privately instead:

1. Go to this repository's **Security** tab.
2. Click **Report a vulnerability**.
3. Describe what you found, how to reproduce it, and what someone could do with it.

Only the maintainers can see the report. You'll get a reply when it has been read, and a note when it is fixed; the fix will credit you unless you'd rather it didn't.

This is a project maintained in spare time, so replies may take a few days. A problem that exposes financial data or allows signing in without a password is treated as urgent.

## What is supported

Fixes go into the latest release. If you run an older one, update first and check whether the problem is still there.

## Running it safely

The app is meant to be self-hosted. A few things keep an install safe:

* **Keep `.env` private.** It holds the secret key, the database password and, if used, the single sign-on secret. It is never committed.
* **Use HTTPS for anything reachable over a network.** On its own the app listens on this computer only; to open it to other devices, put it behind a reverse proxy with HTTPS (see the README).
* **Leave the Django admin off** (the default). People and passwords are managed in Settings → Users.
* **Keep backups encrypted** (Settings → Backups), and keep the private key somewhere other than the server.
* **Update** when a new release comes out.
