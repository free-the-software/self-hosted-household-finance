# Contributing

Thanks for wanting to help. This page covers setting up a copy to work on, running the tests, and what a change needs before it can be merged.

For anything bigger than a small fix, please open an issue first to talk it through, so your time isn't spent on something that won't fit.

**Security problems:** don't open an issue; see [SECURITY.md](SECURITY.md).

## A copy to work on

You need [Docker with the Compose plugin](https://docs.docker.com/engine/install/).

```bash
git clone https://github.com/free-the-software/self-hosted-household-finance.git finance && cd finance
cp .env.example .env
# In .env: uncomment the development COMPOSE_FILE line,
# set SITE_URL=http://localhost:5173 and DEBUG=True
docker compose up -d --build
docker compose exec backend python manage.py first_admin    # prints your login
```

The app is at http://localhost:5173 and reloads as you edit. Django's admin is at http://localhost:8000/admin, and the database at localhost:5433 (for a tool like pgAdmin). To try something on an empty database without touching your usual one, `./scripts/dev-copy.sh fresh` makes a second copy alongside (see the script for details).

## Testing with your own data

`scripts/pull-prod-db.sh` copies the database from the server you run the app on into your dev copy, so you can try a change against real data:

```bash
./scripts/pull-prod-db.sh user@server
```

* It only reads on the server, but it **replaces your dev database** (it asks first).
* **Your real financial data is then on the dev machine.** Keep it off shared computers, and never put it in commits, screenshots or issues.
* It finds the server's database container as `finance_db`. If you set `CONTAINER_PREFIX` there, run it as `PROD_DB_CONTAINER=<prefix>_db ./scripts/pull-prod-db.sh user@server`.

## Tests

Backend (no database server needed; uses a throwaway SQLite database):

```bash
docker compose exec backend python manage.py test finance --settings=config.settings_test
```

Frontend type check and build:

```bash
docker compose exec frontend npx tsc --noEmit -p .
docker compose exec frontend npm run build
```

Every pull request runs the same checks automatically on GitHub (`.github/workflows/tests.yml`): the backend tests, the frontend type check and build, and a check that the scripts parse. A pull request shows a tick when they pass.

## Releases

A release is a version tag on main:

```bash
git tag v1.2.0 && git push origin v1.2.0
```

That runs `.github/workflows/release.yml`: the checks again, then the backend, frontend and receipt reader images are built (with the version inside) and published to `ghcr.io/free-the-software/self-hosted-household-finance/`, tagged `1.2.0`, `1.2` and `latest`. Installs made from a release pick it up with `./scripts/update.sh`. Use [semantic versions](https://semver.org): the last number for fixes, the middle for new features, the first for changes that need people to do something when they update. A tag like `v1.2.0-rc1` builds images for testing without being offered as an update.

## What a change needs

* **One topic per pull request**, with a description of what changes for someone using the app.
* **Tests** for backend changes, in `backend/finance/tests.py`, and all tests passing.
* **Database changes** come with their migration (`python manage.py makemigrations finance`), committed alongside.
* **It works for anyone's setup:** no bank, account, file name or amount of your own in the code. Bank files are recognised by their contents, not by whose they are.
* **Plain words in anything people see.** Say what happened and what to do next ("Choose where its subcategories should go"), not error codes. Use the app's own dialogs (`components/Dialogs.tsx`), never the browser's `alert()` or `confirm()`.
* **No real data anywhere:** no real names, account numbers, amounts or receipts in code, tests, screenshots or issues. Make up examples.
* **Nothing secret in git:** no `.env` files, keys or backups.

## Licence

The project is under the [AGPL-3.0-or-later](LICENSE). By sending a pull request, you agree that your contribution is shared under the same licence.

New source files start with the same short notice as the others:

```
# Copyright (C) 2026 free-the-software
# SPDX-License-Identifier: AGPL-3.0-or-later
# This file is part of Save More Money, under the GNU AGPL v3 or later: see LICENSE.
```

If your change is substantial, add your own line: `# Copyright (C) <year> <your name>`.
