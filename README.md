# OrderFlow

**B2B ordering for suppliers and the businesses they sell to.**

OrderFlow gives a supplier (a manufacturer, distributor or wholesaler) a
private online catalog for their business clients. Clients sign in to their
own portal, build a cart and place purchase orders. The supplier's team
reviews, approves and fulfils those orders in one place, with GST worked out
on every line and a full history of who did what.

## Features

**For suppliers**

- Dashboard with revenue for the month, orders waiting for review, a 30-day
  revenue chart, top products and top clients
- Order management with status filters, search, date ranges and CSV export
- A clear order workflow: pending → approved → fulfilled, or rejected with a
  reason, with a timeline of every change
- Product catalog with GST slabs, units and categories, plus CSV import and
  export
- Invite clients and teammates by email (invitations are rate limited per
  person and per workspace); suspend or reactivate them at any time, and
  suspension signs them out everywhere
- Change a client's or your own sign-in email safely: the new address has to
  confirm a link before the switch
- Bulletins shown on client dashboards, with expiry dates in your time zone
- Company settings: legal name, GSTIN, address, support contact, time zone
  and order number prefix

**For their clients**

- A private portal with the supplier's live catalog, search and category
  filters with product counts
- Cart with quantities, GST and totals; protected against double submits
- Order history with an Open tab, status updates by email (replies go to
  the supplier), cancel while pending, one-click reorder, and printable
  proforma invoices

**Security**

- Every supplier's data is isolated; records from another company are never
  reachable
- Email verification, password reset by email, invite-only client accounts
- Passwords hashed and strength-checked; only wrong passwords count toward
  the sign-in limit, per account and network, so nobody can lock you out
- Changing a password or suspending an account ends every other session, and
  the account holder gets an email whenever their password changes
- Idle sessions end after 12 hours; signing out also clears "keep me signed in"
- Emailed links (verification, reset, invitation) are single-use, expire, and
  are kept out of the server's access log
- CSRF protection on every form, a strict Content Security Policy, secure
  cookies and HSTS in production

## Tech stack

- Python 3.12, Flask 3, SQLAlchemy 2 with Alembic migrations
- PostgreSQL in production, SQLite for local development and tests
- Server-rendered Jinja templates with a custom CSS design system and a small
  amount of plain JavaScript (pages work without it)
- Email through Resend (or any SMTP server)
- Deployed on Render with gunicorn; tested with pytest on GitHub Actions
  against both SQLite and Postgres

## Project layout

```
orderflow/
  __init__.py        app factory
  config.py          settings from environment variables
  models.py          database models
  services/          business logic (orders, accounts, analytics, catalog, cart)
  auth/              sign up, sign in, verification, invites, password reset
  admin/             supplier workspace
  portal/            client portal
  public/            landing page, legal pages, health check
  templates/         Jinja templates (layouts, macros, pages, emails)
  static/            CSS, JavaScript, icons
migrations/          Alembic migrations
tests/               pytest suite
docs/                UI building blocks and deployment guide
```

## Running locally

```bash
python3.12 -m venv .venv
source .venv/bin/activate
pip install -r requirements-dev.txt
cp .env.example .env
flask --app wsgi db upgrade
flask --app wsgi seed-demo
flask --app wsgi run --debug
```

Open <http://127.0.0.1:5000>. `seed-demo` creates a demo supplier with
products, clients and two months of orders; it prints the sign-in details.
Emails are printed to the terminal while `MAIL_BACKEND=console`.

The `flask` command uses the development settings, with a public
development key, when `APP_ENV` and `SECRET_KEY` aren't set. Anything else,
such as `gunicorn wsgi:app`, refuses to start until both are set in `.env`
or the environment.

## Tests

```bash
ruff check .
ruff format --check .
pytest --cov=orderflow
```

To run the suite against Postgres, set
`TEST_DATABASE_URL=postgresql+psycopg://user:password@localhost:5432/orderflow_test`.

## Deployment

See [docs/deploy.md](docs/deploy.md) for deploying to Render with a custom
domain and Resend for email.

## Versions

| Version | What it is |
| --- | --- |
| [v1.0](https://github.com/subham-hq/orderflow/tree/v1.0) | The original CS50x final project, as submitted ([video demo](https://youtu.be/xvPRFuExpm0)) |
| [v1.0.1](https://github.com/subham-hq/orderflow/tree/v1.0.1) | v1.0 with its four most serious bugs fixed |
| v2.0 | This version: a rebuilt, production-ready OrderFlow |

v1 was a single Flask file using the CS50 SQL library and Bootstrap. v2 keeps
the same idea (multi-company ordering with an approval workflow) and rebuilds
it as a modular application with migrations, email-based accounts, GST-aware
orders, a client portal, analytics, a new design and a full test suite.
