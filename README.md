# Norman’s NYC Events

A public, read-only Django guide to upcoming New York City events selected around Norman’s interests. It highlights strong matches first, then presents the remaining events by date with interest chips and filters for category, neighborhood, date, and price.

## Stack

- Python 3.12
- Django 6.1.1
- SQLite for local development
- PostgreSQL through `DATABASE_URL` in production
- Server-rendered templates, custom CSS, and a tiny image-fallback script
- Vercel’s Django runtime and CDN-collected static files

## Local setup

```bash
cd ~/repos/events_webpage
uv sync --python 3.12
export DEBUG=1
uv run python manage.py migrate
uv run python manage.py import_events
uv run python manage.py runserver
```

Open http://127.0.0.1:8000/. The repository’s `data/events.json` contains the reviewed launch catalog. `import_events` is idempotent: events are keyed by official URL and occurrences by their stable source key, so rerunning it updates rather than duplicates records.

To use Django admin locally:

```bash
export DEBUG=1
uv run python manage.py createsuperuser
```

Then open http://127.0.0.1:8000/admin/.

## Tests and checks

The host environment currently contains an unrelated pytest entry-point that can stall automatic plugin discovery. The deterministic project command disables global plugin autoload and explicitly enables pytest-django:

```bash
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 uv run pytest -p django -q
DEBUG=1 uv run python manage.py check
DEBUG=1 uv run python manage.py makemigrations --check --dry-run
DEBUG=1 uv run python manage.py collectstatic --noinput
```

## Production environment

Configure these in the Vercel project for Production and Preview:

- `DEBUG=0`
- `SECRET_KEY`: a long random value; never commit it
- `DATABASE_URL`: a managed PostgreSQL connection string
- `ALLOWED_HOSTS`: deployed hostnames, comma-separated; `.vercel.app` permits Vercel subdomains
- `CSRF_TRUSTED_ORIGINS`: complete HTTPS origins, comma-separated; `https://*.vercel.app` supports previews

The application refuses to start in production without `SECRET_KEY`, `DATABASE_URL`, and `ALLOWED_HOSTS`, and rejects a non-PostgreSQL production database. Vercel detects `manage.py` and the `config.wsgi` entry point. Because `STATIC_ROOT` is configured, Vercel runs `collectstatic` and serves the output from its CDN. `vercel.json` only sets the function duration.

## First production migration and seed

Do not run migrations during every serverless build. After attaching a PostgreSQL provider and setting the Vercel environment variables, pull them into a local file and run the explicit one-time commands against that database:

```bash
vercel pull --environment=production
vercel env pull .env.production.local --environment=production
set -a
source .env.production.local
set +a
export DEBUG=0
uv run python manage.py migrate --noinput
uv run python manage.py import_events
```

Both commands are safe to rerun when deploying schema or editorial-data updates. The importer does not delete records; expired occurrences remain in the database but are hidden from public pages.

## GitHub → Vercel handoff

1. Commit this directory to a GitHub repository.
2. Import that repository in Vercel.
3. Attach a managed PostgreSQL integration or add a vendor-neutral `DATABASE_URL` manually.
4. Add the production environment variables above.
5. Deploy, then run the explicit migration and seed commands.
6. Check `/`, an event detail page, `/admin/`, and `/static/events/site.css` on the deployed hostname.

Automated event ingestion is deliberately deferred. A later authenticated cron workflow can generate the same validated JSON shape and invoke a controlled import path without changing the core schema.
