# CP-minicapstone

EventMatch is a lightweight student activity coordination MVP built with Flask and SQLite.

[![Deploy to Render](https://render.com/images/deploy-to-render-button.svg)](https://render.com/deploy?repo=https://github.com/aldiyar513/CP-minicapstone)

## Run locally

1. Create a virtual environment:

```bash
python3 -m venv .venv
source .venv/bin/activate
```

2. Install dependencies:

```bash
pip install -r requirements.txt
```

3. Start the app:

```bash
python3 app.py
```

The app seeds a local SQLite database automatically in `instance/eventmatch.db` the first time it starts.

## Deploy to Google Cloud Run

The app now supports production-style environment configuration:

- `PORT` sets the listening port.
- `HOST` sets the bind address for the Flask dev server.
- `SECRET_KEY` overrides the default development secret.
- `DATABASE_URL` overrides the default SQLite database.
- `INSTANCE_CONNECTION_NAME`, `DB_NAME`, `DB_USER`, and `DB_PASSWORD` can be used for Google Cloud SQL over the `/cloudsql/...` Unix socket when `DATABASE_URL` is not set.

For a production process, run:

```bash
gunicorn app:app
```

`gunicorn.conf.py` reads `PORT`, `WEB_CONCURRENCY`, `GUNICORN_THREADS`, and `GUNICORN_TIMEOUT`.

### Cloud Run

This repository now includes:

- [Dockerfile](Dockerfile) for container-based deployment to Cloud Run
- [.dockerignore](.dockerignore) to avoid shipping local files into the container

For production on Cloud Run, do not rely on local SQLite. Use `DATABASE_URL` or a Cloud SQL PostgreSQL connection.

## Current backend features

- `/` activity feed with category and date filters
- `/activities/new` create activity flow with persistent role setup
- `/activities/<id>` activity detail with join, interested, leave, chat, and ETA persistence
- `/activities/<id>/host` host-only dashboard for attendee, waitlist, and role management
- `/profile` profile data sourced from the database

## Verification

Run the test suite with:

```bash
.venv/bin/pytest -q
```
