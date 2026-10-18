# Deploying OrderFlow on Render

OrderFlow runs on [Render](https://render.com) as one web service and one
Postgres database, both described in [`render.yaml`](../render.yaml).
Transactional email (sign-up confirmation, password resets, invitations and
order updates) goes through [Resend](https://resend.com).

You'll need:

- the GitHub repository (`subham-hq/orderflow`) with v2 merged into `main`
- a Render account
- a Resend account
- access to your domain's DNS settings

## 1. Set up email sending with Resend

1. In Resend, open **Domains → Add domain** and enter your domain (for
   example `example.com`, or a subdomain such as `mail.example.com`).
2. Resend shows a few DNS records (an MX record and TXT records for SPF and
   DKIM). Add them at your DNS provider exactly as shown.
3. Back in Resend, click **Verify**. DNS changes can take a few minutes to a
   few hours.
4. Open **API Keys → Create API key**, choose **Sending access**, and copy
   the key (it starts with `re_`). You'll paste it into Render in step 2.

Your sender address must be on the verified domain, for example
`OrderFlow <orders@example.com>`.

## 2. Create the services from the Blueprint

1. In Render, choose **New → Blueprint** and connect the
   `subham-hq/orderflow` repository, branch `main`.
2. Render reads `render.yaml` and lists a web service (`orderflow`) and a
   Postgres database (`orderflow-db`), both in the Singapore region.
3. Fill in the values it asks for:

   | Variable | Example | What it's for |
   | --- | --- | --- |
   | `APP_BASE_URL` | `https://example.com` | Links inside emails. No trailing slash. |
   | `RESEND_API_KEY` | `re_…` | Sending email |
   | `MAIL_FROM` | `OrderFlow <orders@example.com>` | Sender shown on every email |
   | `CONTACT_EMAIL` | `hello@example.com` | Receives landing-page enquiries; shown in the legal pages |
   | `LEGAL_NAME` | `Example Technologies Pvt Ltd` | The business named in the Privacy Policy and Terms |

   `SECRET_KEY` is generated for you, `DATABASE_URL` is connected to the
   database automatically and `APP_ENV` is set to `production`. Leave them
   as they are. If you ever set up a service by hand, set all three: without
   `APP_ENV` or `SECRET_KEY` the app refuses to start rather than run with
   development settings and a public key, and in production it refuses to
   start without `DATABASE_URL` rather than keep orders in a SQLite file that
   the next deploy deletes.
4. Choose instance types. The free web service sleeps when it's idle and free
   Render Postgres databases expire, so use paid instances for a public
   launch.
5. Click **Apply**. Render installs the dependencies, and on every start
   `bin/start.sh` applies database migrations and then starts gunicorn.
   Render checks `/healthz` before sending traffic to a new deploy.

## 3. Connect your domain

1. In the web service, open **Settings → Custom Domains** and add your domain
   (and `www.` if you want it).
2. Render shows the DNS records to add: a `CNAME` for subdomains such as
   `www`, and for the root domain an `ALIAS`/`ANAME` or `A` record, depending
   on what your DNS provider supports. Add them.
3. Render issues the HTTPS certificate automatically once DNS resolves.
4. Make sure `APP_BASE_URL` matches the address people will use, then
   redeploy if you changed it.

## 4. First launch checklist

- Open the site, choose **Start free**, and create your own supplier account.
- Check that the confirmation email arrives and its link opens your domain.
- Add a product, invite yourself as a client using a second email address,
  accept the invitation and place a test order.
- Approve and fulfil the order and check the status emails.
- Send a test enquiry from the contact form and check it reaches
  `CONTACT_EMAIL`.
- Read through the Privacy Policy and Terms (`/privacy`, `/terms`) and adjust
  them for your business if you need to.

Don't run `flask seed-demo` on production; it creates a demo company with
sample data and known passwords. Use it locally or on a separate staging
service.

## Operating the service

- **Logs:** the service's **Logs** tab shows requests and errors. Email
  delivery problems are logged with the message "Could not send". The
  request log (set in `bin/start.sh`) leaves out query strings and the
  Referer header, and shows `[token]` in place of the secret part of links
  from emails (password resets, invitations and email confirmations), so
  nobody reading the logs can use a link meant for someone else. Render's
  own HTTP request logs, on plans that have them, still record full paths.
- **Backups:** paid Render Postgres plans take daily backups; check your
  plan's retention in the database's **Backups** tab.
- **Scaling:** the Blueprint runs one gunicorn worker (`WEB_CONCURRENCY`)
  with eight threads (`GUNICORN_THREADS`). Rate limits on signing in,
  signing up, password resets and the contact form are counted in memory by
  each worker, so with one worker every limit is exact; the counts start
  again after each deploy or restart. Before you raise `WEB_CONCURRENCY` or
  run more than one instance, share the counts in Redis:
  1. In Render, choose **New → Key Value**, in the same region as the web
     service.
  2. Copy its **Internal Key Value URL** (`redis://red-…:6379`).
  3. Set `RATELIMIT_STORAGE_URI` on the web service to that URL.

  The Redis client is already in `requirements.txt`. If Key Value can't be
  reached, the app counts in memory until it's back, rather than failing
  sign-ins, and logs "Rate limit storage unreachable". With more than one
  worker and no Redis, the app logs a warning at start-up.
- **Client IP addresses:** rate limits use the address that Render's proxy
  adds to `X-Forwarded-For`. If Render ever puts a second proxy in front of
  your service (for example a CDN you add yourself), set `PROXY_HOPS` to the
  number of proxies so limits apply per visitor rather than per proxy.
- **Secret key:** changing `SECRET_KEY` signs everyone out and invalidates
  links already sent by email.
- **Migrations:** schema changes ship as files in `migrations/versions/` and
  are applied automatically on the next deploy.

## Running it locally

```bash
python3.12 -m venv .venv
source .venv/bin/activate
pip install -r requirements-dev.txt
cp .env.example .env
flask --app wsgi db upgrade
flask --app wsgi seed-demo
flask --app wsgi run --debug
```

Then open <http://127.0.0.1:5000>. Emails are printed to the terminal.
