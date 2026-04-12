# CP-minicapstone

EventMatch is a lightweight student activity coordination MVP built with Flask and SQLite.

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
