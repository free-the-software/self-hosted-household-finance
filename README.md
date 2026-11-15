# 🏦 Save More Money

**Self-hosted household finance.** Keep track of your household's money on a computer you control.

Download statements from your banks (QFX, OFX or CSV) and drop them in. The app reads them, names and categorises each transaction using rules you set, and holds them for you to review before they go into your ledger. From there it shows where your money goes, compares your budget with what you actually spent (by month and for the year), helps you plan next year's budget, forecasts each account's balance from your regular bills and income and warns you before one would go below zero, and tracks your net worth, including the equity in a home or car against its loan. Several people can share one ledger, each with their own login.

It's for people who want the kind of tracking a desktop finance program gives, without handing their bank logins or financial data to an online service. Everything runs in Docker on your own computer or home server, and your data stays there: the app never connects to your bank, and the only thing it asks the internet is whether a new version is out (which you can switch off).

## 💻 System requirements

| | The app | Add for the receipt reader (optional, beta) |
|---|---|---|
| **Operating system** | 64-bit Linux, macOS, or Windows 10/11 with WSL 2 | |
| **Software** | [Docker with the Compose plugin](https://docs.docker.com/engine/install/) (Docker Desktop on macOS and Windows), and git | |
| **Processor** | 2 cores, 64-bit x86 (ARM64 should work but hasn't been tested) | Reading is slow on an ordinary processor: expect tens of seconds per receipt |
| **Memory** | About 1 GB free (2 GB recommended) | About 2 GB more, only while reading receipts |
| **Disk** | About 3 GB for the app and room to build it, plus your data and backups | About 2.5 GB more |
| **Internet** | Only to install and update | A 2 GB download when it's first built |
| **Browser** | Any current browser, on a computer or phone | |

## ✨ What it does

* **Imports from any bank.** QFX and OFX files (which carry the account number and balance), or CSV from most banks, recognised by its column headings. Drop files into the Import Inbox, or into the `data/dropzone` folder, and they're picked up by themselves.
* **Nothing goes in without you.** Imported transactions wait in the Import Inbox for you to approve. Exact duplicates are flagged, and a transaction you already entered by hand is offered as a match instead of being added twice.
* **Rules that tidy up as you go.** Rename messy bank descriptions and file transactions in categories automatically, with conditions on the description, amount, account or memo, combined with AND or OR.
* **Categories, splits and transfers.** A full starter set of categories you can rename, move or merge; split one transaction across several categories; link the two sides of a transfer between your accounts (the app suggests the match).
* **Balances you can trust.** Each account shows whether its balance is **bank verified** (from the bank's own figure) or **calculated** from its transactions.
* **Budgets.** Budget to Actual by month and for the year, and a plan for next year's budget built from what you actually spent.
* **Forecasts.** Each account's balance projected from your regular bills and income, with a warning before one would go below zero.
* **Net worth, loans and equity.** Assets, loans and their payment history, and the equity in a home or car against its loan.
* **Several users, one ledger.** Each person has their own login; admins manage users. Optional single sign-on through Authentik.
* **Backups built in.** Encrypted database and full backups, on a schedule or on demand, with old ones cleared out by the rules you set (the newest two of each kind are always kept), and a script to copy them to another computer.
* **Receipt reading (optional add-on, beta).** Drop photos of receipts in, and the app reads the store, date, total and tax and suggests the transaction each belongs to. See [The receipt reader](#the-receipt-reader).

## ⚙️ How it works

1. **The watcher** keeps an eye on the dropzone folder (`data/dropzone`). It waits until a file has stopped changing before importing it, so a file still being copied is never read half-written, and it picks up anything left waiting when it starts.
2. **Reading the file.** QFX and OFX files go through a strict reader (ofxtools). Banks often bend the format, so a file it rejects is read again by a more forgiving fallback. CSV files are read by their column headings, whichever bank they come from. A file for an account the app doesn't know yet is held until you say which account it belongs to, and that choice is remembered.
3. **The balance.** The statement's balance is stored with its date, so the app knows which transactions the bank had already counted, and shows a bank-verified balance alongside the one it calculates.
4. **Rules** rename and categorise each transaction.
5. **The Import Inbox** holds them for review, with duplicates and possible matches marked. Approving one writes it to your ledger.
6. **Archiving.** The imported file is moved to `data/archive/qfx`, its name prefixed with the statement's end date (for example `20260228_statement.qfx`), so you keep a tidy record of every download.

A scheduler runs the regular jobs: recurring transactions, backups, and clearing out old files by the rules you set in Settings → Storage.

## 🛠 Built with

* **Frontend:** React and TypeScript, Tailwind CSS, built with Vite and served by nginx
* **Backend:** Python, Django and Django REST Framework, served by gunicorn
* **Database:** PostgreSQL
* **Running it:** Docker and Docker Compose, on your own computer or home server
* **Libraries of note:** ofxtools (QFX/OFX), watchdog (the dropzone watcher)
* **Receipt reader (optional):** Microsoft's Florence-2 model, run locally

## 🚀 Installation

**You need:** see [System requirements](#-system-requirements).

```bash
git clone https://github.com/free-the-software/self-hosted-household-finance.git finance
cd finance
./scripts/setup.sh
```

The script asks which port to use, whether other devices on your network may open the app, whether to add the optional receipt reader, and which version: the latest release (the default) or the code as it is. Then it:

* writes `.env` with a random secret key and database password, readable only by you;
* creates the `data/` folders (bank files, receipts, backups);
* starts everything. **A release** downloads ready-made images (about 1 GB, or 3 GB with the receipt reader), which takes minutes. **Building from the code** takes a few minutes, or 10 minutes or more with the receipt reader;
* creates your login: username **admin** with a one-time password, shown once at the end. When you first sign in, you choose your own password.

Then open the address the script shows at the end, `http://localhost:<port>` with the port you chose (8080 unless you changed it), and sign in. If you let other devices on your network open it, they use this machine's address in place of `localhost`. A new ledger starts with a full set of categories laid out the way most personal-finance programs do it: Income, Auto & Transport, Bills & Utilities, Food & Dining, Home, Taxes, Transfers and so on: 20 categories with 106 subcategories (Settings → Categories; rename, move or delete freely; set `STARTER_CATEGORIES=False` in `.env` for a blank slate). The ones the app files things in itself, such as Mortgage Interest, Property Tax, Home Insurance, PMI, Loan Principal, Escrow and Sales Tax, are found by name wherever you move them. The Transactions page shows where to begin:

1. **Settings → Accounts:** add your accounts. For bank imports, give each the account number your bank puts in its download files.
2. **Import Inbox:** drop in a statement: QFX or OFX if your bank offers it (it carries the account number and balance), or CSV from most banks (the columns are recognised by their headings). A file for an account the app doesn't recognise yet is held until you say which account it belongs to, and that choice is remembered. A file it can't read is listed there with the reason.
3. Review and approve. Add renaming rules (Settings → Rules) as you go, and later imports sort themselves.

**By hand instead of the script:** `cp .env.example .env`, change the values marked CHANGE, then `docker compose up -d --build` and, once the backend has started, `docker compose exec backend python manage.py first_admin` (it prints your login).

**Updating:** run `./scripts/update.sh` in the app's folder. It backs up the database, gets the new version, restarts (database changes apply automatically), and clears Docker's leftover images and build pieces so the disk doesn't slowly fill. `./scripts/update.sh --check` only says what's new.

* **Installed from a release:** it moves to the newest release and downloads its images. To go back to an earlier one: `./scripts/update.sh --to 1.2.0`.
* **Built from the code:** it follows the branch you're on and rebuilds what changed.
 Settings → Storage shows your version and the exact command to paste, and (with **Check for new releases** on) says when a new release is out. The app never updates itself: it has no control over Docker, by design.

**System settings** (Settings → Storage, admins): checking for new releases, the low disk space warning (default 5 GB), signing out after idle (default 10 minutes) and the largest upload (default 25 MB). Changes apply at once; **Restore defaults** goes back to the defaults, which `.env` can also set.

### The receipt reader

> ⚠️ **An optional add-on, in beta.** It often misreads receipts, especially long, creased or faded ones. Treat what it reads as a suggestion: nothing changes until you approve it. It needs about **2.5 GB more disk**, about **2 GB more memory while reading**, and makes the first build 10 minutes or more.

Reads photos of receipts (store, date, total, tax) and suggests the transaction each belongs to. Its model is built into its image, so it runs entirely on your computer: receipts are never sent anywhere. It uses its 2 GB of memory **only while reading**: the model is loaded when a receipt arrives (the first one after a quiet spell takes a little longer) and released after 10 minutes without one. Set `RECEIPT_READER_IDLE_MINUTES` in `.env` to change that, or to `0` to keep it loaded.

* **To add it later:** `./scripts/receipt-reader.sh on` in the app's folder. It adds `receipts` to `COMPOSE_PROFILES` in `.env`, builds the reader and restarts the app. Receipt photos left waiting in the dropzone are read once it's running.
* **To remove it:** `./scripts/receipt-reader.sh off`. It takes it out of `.env`, removes the reader and frees its disk space. Receipts already read stay in the app.
* **Settings → Storage** shows whether it's installed and the exact command to add or remove it, ready to copy. The app can't do it for you: it has no control over Docker, by design.
* **If it's down** (stopped, restarting or still starting), receipt photos wait in the dropzone, and the Receipts page says how many. The watcher checks every minute and has them read as soon as the reader answers. Docker restarts the reader by itself after a crash or a reboot.
* **Without it,** the Receipts page says how to add it, and receipt photos are refused with the same explanation. Everything else works the same.

### Ways to run it

| How | `.env` | Address |
|---|---|---|
| **On its own** (default) | `COMPOSE_FILE` unset | `http://localhost:<port>`, where the port is `APP_PORT` (8080 unless you changed it); this computer only unless `APP_BIND=0.0.0.0` and `DOMAIN_NAME` is the machine's IP or name |
| **Behind Traefik** with HTTPS and optional single sign-on | `COMPOSE_FILE=docker-compose.yaml:docker-compose.traefik.yml` | https://`DOMAIN_NAME` (no port published) |
| **Development** | `COMPOSE_FILE=docker-compose.yaml:docker-compose.dev.yml` | http://localhost:5173 with hot reload; see [CONTRIBUTING.md](CONTRIBUTING.md) |

**Ready-made images:** an install from a release adds `docker-compose.release.yml` to `COMPOSE_FILE` and sets `APP_TAG` to the release (setup.sh does both). Behind Traefik, list all three: `COMPOSE_FILE=docker-compose.yaml:docker-compose.release.yml:docker-compose.traefik.yml`. The images are published at `ghcr.io/free-the-software/self-hosted-household-finance/` (`backend`, `frontend` and `receipt-reader`) for each release.

Running on its own, sign-in is a username and password on the app's own sign-in page (see [Users and passwords](#users-and-passwords)). Behind a reverse proxy you can switch to single sign-on with Authentik; see [Authentication](#-authentication). Plain HTTP is fine on your own computer; for anything reachable over a network, put the app behind a reverse proxy with HTTPS.

`docker-compose.traefik.yml` expects a running Traefik on the external Docker network `traefik_default`, entrypoints named `https` and `http`, and a certificate resolver named `cloudflare`; change those names in the file if yours differ.

## 🗄️ Storage & Retention

Processed files are kept under `data/` and grow without limit unless you set a policy. **Settings → Storage** shows how much each kind of file uses and lets an administrator choose how long to keep it. Every policy defaults to *keep forever*, so nothing is deleted until you change one.

| Category | Folder | A policy deletes | Always kept |
|---|---|---|---|
| Receipt images | `data/media/receipts` | The image file, by receipt date | The receipt record (date, total, tax, text, transaction link) and any tax split; it shows as "image expired" |
| Receipt archive copies | `data/archive/receipts` | The duplicate copy left after scanning | The image shown in the app and the receipt record |
| Failed receipt scans | `data/archive/failed` | Images the scanner could not read | Everything else |
| Imported bank files | `data/archive/qfx` | The original QFX/OFX/CSV files | Every imported transaction |

* **Preview** lists the exact files a policy would delete, and deletes nothing.
* The scheduler applies the saved policies every night at 03:30 (server time); **Run clean-up now** does it immediately.
* Only staff users can change policies or run a clean-up. Each run that deletes anything is recorded.
* From the command line: `docker compose exec -u app backend python manage.py apply_retention --dry-run`

## 💾 Backups

**Settings → Backups** makes backups on a schedule and on demand. A backup is one file you can restore everything from.

| Kind | Contains | Default schedule |
|---|---|---|
| Database backup | Every transaction, account, budget, rule and receipt record | Daily at 02:00 |
| Full backup | The database plus every receipt image | Weekly, Sunday at 03:00 |

Each kind has its own schedule (off, daily, weekly or monthly, at an hour you choose, in server time), and either can be started straight away with **Back up now**.

**Set-up.** Backups do not start until an administrator sets up encryption on that page. The app creates a key pair and shows the private key **once**: save it in a password manager, away from the backups. The server keeps only the public half, which can encrypt but not decrypt, so a backup file is unreadable even to someone with access to the server. If the private key is lost, encrypted backups cannot be opened. Encryption can be turned off, but only as a deliberate choice.

**Where they go.** All backups are written to one folder, `data/backups`. The file name says which kind each is: `finance-db-<date>-<time>` or `finance-full-<date>-<time>`. A backup on the same disk as the data does not survive that disk failing, so either:

* set `BACKUP_DIR_HOST` in `.env` to a folder on another disk or a mounted network share, or
* have another machine pull them with `scripts/pull-backups.sh` (see *Pulling backups to another machine* below), or
* download individual backups from the Backups page.

Old backups are removed after 30 days by default; change that under **Settings → Storage**. The two most recent backups of each kind are always kept, however old, so a slow schedule or a failed run can never leave you with none. A failed or overdue backup is shown as a warning on every page.

**Pulling backups to another machine.** `scripts/pull-backups.sh` runs on a second computer and fetches new backup files over SSH. Because that machine does the fetching, the server holds no credentials for it, so a break-in on the server cannot reach or delete the copies. It only ever copies: nothing is deleted on the server, and a local copy is never removed just because the server no longer has it. Old local copies are pruned by age (90 days by default, always keeping the newest three of each kind).

It also tells you when something is wrong, with a desktop notification where available. It reads `backup-status.json` (see below), so its warnings follow the schedule you set in the app with nothing to configure on this machine. It warns when:

* no new backup has arrived for longer than the schedule allows (about 3 days for daily, 9 for weekly, 35 for monthly; a kind that is switched off is never expected),
* the server itself reports a failed or overdue backup,
* the server has stopped refreshing its status, which means its scheduler is not running,
* the server's newest backup is missing here, or a freshly copied file does not match the server's checksum.

If the server cannot be reached it keeps judging by the last status it fetched, so a machine that is simply away from home stays quiet until its copies are really out of date.

Put the settings in `~/.config/finance-backup-pull.env` on that machine:

```bash
FINANCE_BACKUP_SERVER=user@server
FINANCE_BACKUP_DEST=/home/you/Finance_Backups
```

Run `./scripts/pull-backups.sh` once by hand, then schedule it. With systemd, as `~/.config/systemd/user/finance-backup-pull.service`:

```ini
[Unit]
Description=Pull finance app backups from the server

[Service]
Type=oneshot
ExecStart=%h/path/to/finance/scripts/pull-backups.sh
```

and `~/.config/systemd/user/finance-backup-pull.timer`:

```ini
[Unit]
Description=Pull finance app backups from the server every hour

[Timer]
OnStartupSec=5min
OnCalendar=hourly
Persistent=true

[Install]
WantedBy=timers.target
```

then `systemctl --user enable --now finance-backup-pull.timer`. It needs `rsync` on both machines.

A timer cannot type a password, so give the job its own SSH key, and lock that key down on the server so it can do nothing except read the backups folder. On the machine that pulls:

```bash
ssh-keygen -t ed25519 -N "" -f ~/.ssh/finance_backup_pull -C "finance backup pull (read-only)"
```

On the server, add one line to `~/.ssh/authorized_keys`, made of this prefix followed by the contents of `finance_backup_pull.pub`:

```
command="rrsync -ro /full/path/to/finance/data/backups",restrict ssh-ed25519 AAAA...
```

`rrsync` ships with rsync and limits the key to read-only file transfer from that one folder; `restrict` turns off shells and forwarding. Then add to `finance-backup-pull.env`:

```bash
FINANCE_BACKUP_SSH_KEY=/home/you/.ssh/finance_backup_pull
FINANCE_BACKUP_REMOTE_DIR=.
```

The folder is `.` because the key already points at the backups folder. Point the destination at a folder your own backup or cloud sync already covers to get an offsite copy as well; the files are encrypted, so that is safe as long as your private key is kept somewhere else.

**Restoring.** Restoring replaces everything in the app, so it is a command, not a button. Copy the backup file and a file holding your private key into `data/backups/`, then:

```bash
docker compose exec -u app backend python manage.py restore_backup \
    /data/backups/<backup file> --identity /data/backups/<key file>
docker compose restart backend watcher scheduler
```

The command is the same for both kinds of backup. Add `--database-only` to restore the database from a full backup but leave the receipt images as they are. Remove the key file from the server afterwards.

**`backup-status.json`.** The app keeps this small file in the backups folder and rewrites it whenever the schedule changes, a backup finishes or fails, and once an hour. It lists the schedule for each kind of backup, the newest good backup of each kind (file name, size, checksum) and the app's own verdict on its backups. It contains no financial data. Anything that copies or monitors the folder can read it instead of guessing the schedule or logging in to the app; `scripts/pull-backups.sh` does.

**The file format** is deliberately plain, so a backup never depends on this app: a tar archive (`manifest.json`, `db.dump` in `pg_dump` custom format, and `media/` in a full backup), encrypted with [age](https://age-encryption.org). By hand: `age -d -i key.txt backup.tar.age | tar -x`, then `pg_restore`.

From the command line: `python manage.py create_backup db` or `create_backup full`. The backup, retention and upload logic has a test suite; run it without a database server using `python manage.py test finance --settings=config.settings_test`. Backups need PostgreSQL; the backend image carries the matching `pg_dump` (see `PG_MAJOR` in `backend/Dockerfile`).

## 🔐 Authentication

Django requires a logged-in session for every `/api` and `/media` request. The reverse proxy does no authentication of its own.

* **With single sign-on** (behind Traefik, see [Ways to run it](#ways-to-run-it)): sign-in goes through Authentik (OIDC). The React app redirects to `/oidc/authenticate/`, Authentik sends the browser back to `/oidc/callback/`, and Django creates the session.
* **On its own, or in dev:** with the OIDC variables blank, the app shows its own sign-in page (username and password). Sign-in attempts are rate limited per address (`RATE_LIMIT_SIGN_IN`).

### Users and passwords

Without single sign-on, the login made by `setup.sh` (`admin`) is the first **admin**. Everyone signed in sees and edits the same ledger; admins can also manage users, backups and storage.

* **Change your own password:** Settings → My account.
* **Add people, switch an account off, make someone an admin, or set a new password for someone:** Settings → Users (admins only). There is always at least one active admin: you can't switch off or demote yourself, or the last admin.
* **One-time passwords:** a password someone else chose (the first admin's, a new user's, or one an admin set) must be changed at the next sign-in; until then nothing else in the app works.
* **Forgot your password?** Another admin can set a new one in Settings → Users. If you are the only admin, run this on the machine the app runs on, and sign in with the one-time password it prints:

  ```bash
  docker compose exec backend python manage.py reset_password <your-username>
  ```

Passwords must be at least 8 characters, not a common password, and not like the username. With single sign-on, people and passwords are managed in the identity provider instead; users appear in Settings → Users after their first sign-in, where an admin can still make them admins or switch them off.

**Authentik setup (once):**

1. *Applications → Providers → Create → OAuth2/OpenID Provider.* Client type **Confidential**, redirect URI `https://<DOMAIN_NAME>/oidc/callback/` (strict), a signing key selected (RS256), scopes `openid`, `email`, `profile`.
2. *Applications → Create*, attach the provider. The application **slug** is your `OIDC_APP_SLUG`.
3. On the application, add a **policy/group/user binding** for the people allowed in. An application with no bindings is open to every Authentik user.
4. Add to the server's `.env`, then `docker compose up -d --build`:

```bash
BASE_DOMAIN=example.com                    # the domain, defined once
DOMAIN_NAME=finance.${BASE_DOMAIN}         # where this app is served
AUTH_DOMAIN=https://auth.${BASE_DOMAIN}    # Authentik base URL, no trailing path
OIDC_APP_SLUG=finance
OIDC_CLIENT_ID=...
OIDC_CLIENT_SECRET=...
# SITE_URL defaults to https://<DOMAIN_NAME>; DEBUG must be absent or False
```

`BASE_DOMAIN` has to come before the lines that use it. To see the values Compose resolves, without restarting anything: `docker compose config | grep -E "DOMAIN_NAME|AUTH_DOMAIN|Host\("`.

**The backend container must be able to reach `AUTH_DOMAIN`.** During login Django calls Authentik directly (token, userinfo, JWKS). If Authentik sits behind a Traefik that has its own LAN address (macvlan), containers on the same host cannot reach that address and the login fails with a 500 on `/oidc/callback/`. Fix it by giving Traefik a network alias for the Authentik hostname on the Docker network it shares with the backend:

```yaml
# in the Traefik compose file, on the traefik service
    networks:
      default:            # the network the finance backend joins as traefik_default
        aliases:
          - auth.example.com
```

Check from the server with `docker compose exec backend python -c "import requests; print(requests.get('<AUTH_DOMAIN>/application/o/<slug>/jwks/', timeout=10).status_code)"` — it should print `200`. Errors from a failed login are written to `docker compose logs backend`.

A login lasts `SESSION_COOKIE_AGE` seconds (default 12 hours). Users are matched to Django accounts by email address.

**The Django admin is off unless you ask for it.** It has its own username/password login with no single sign-on, second factor or idle sign-out, and a session created there works for the whole app, so it would be a way around the app's own sign-in (or around Authentik). Users are managed in Settings → Users instead. It is on in development (`DEBUG=True` without OIDC); anywhere else set `ENABLE_ADMIN=True` to turn it on. With OIDC configured, the app's own password sign-in is always off. For one-off administration use the command line, for example `docker compose exec -u app backend python manage.py shell`.

## 🛠 Troubleshooting

Run these from the folder the app is in.

* **See what the app is doing,** for example why a bank file didn't import:
  ```bash
  docker compose logs watcher -f      # bank files and receipts as they are picked up
  docker compose logs backend -f      # the web app
  ```
* **Restart the app** after changing `.env`:
  ```bash
  docker compose up -d
  ```
* **Import the files waiting in the dropzone now,** without waiting for the watcher:
  ```bash
  docker compose exec backend python manage.py stage_import
  ```
* **A file couldn't be imported:** the Import Inbox lists it with the reason. Files the app can't read are moved to `data/archive/failed`.
* **Forgot your password:** see [Users and passwords](#users-and-passwords).

## 🤝 Contributing and security

* **Want to help?** See [CONTRIBUTING.md](CONTRIBUTING.md) for setting up a copy to work on, running the tests, and what a change needs.
* **Found a security problem?** Please report it privately, as described in [SECURITY.md](SECURITY.md), not in a public issue.

## 📜 Licence

Copyright (C) 2026 free-the-software

Free software under the [GNU Affero General Public License](LICENSE), version 3 or (at your option) any later version (AGPL-3.0-or-later). You can use, change and share it, including running it for other people. If you give someone a copy, changed or not, you must also give them its source code under the same licence. If you change it and let other people use it over a network, you must offer the people using it your changed source code, also under the same licence. The menu's **Source code** link does this: if you run a changed version for others, set `SOURCE_URL` when building the frontend (for example `SOURCE_URL=https://example.com/your-fork docker compose build frontend`) so the link points at your changes.

```
This program is free software: you can redistribute it and/or modify
it under the terms of the GNU Affero General Public License as published
by the Free Software Foundation, either version 3 of the License, or
(at your option) any later version.

This program is distributed in the hope that it will be useful,
but WITHOUT ANY WARRANTY; without even the implied warranty of
MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
GNU Affero General Public License for more details.
```
