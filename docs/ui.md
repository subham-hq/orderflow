# UI building blocks

Pages are server-rendered Jinja templates. They use the macros in
`orderflow/templates/macros/ui.html` and the CSS classes below, and nothing
else, so the design system in `orderflow/static/css/app.css` controls how
everything looks.

## Rules

- **No inline styles or scripts.** The Content Security Policy blocks
  `style="…"` attributes, `<style>` blocks and inline `<script>` in pages.
  Put CSS in `static/css/app.css` and behaviour in `static/js/app.js`.
  (Emails are the exception: email clients need inline styles.)
- Every form posts a CSRF token: `{{ form.hidden_tag() }}` for WTForms, or
  `{{ csrf() }}` / `post_button()` for plain forms.
- Destructive or irreversible actions use `post_button(..., confirm="…")`,
  which asks for confirmation in a dialog.
- Money is always `{{ paise | inr }}` or `money(paise)`; dates are
  `{{ value | datetime }}` or `{{ value | date }}` (shown in the company's
  time zone). Never format money or dates by hand.
- Order statuses are always shown with `status_badge(order.status)`.
- Layouts: `layouts/marketing.html` (public site), `layouts/auth.html`
  (sign-in pages), `layouts/app.html` (supplier workspace, sidebar) and
  `layouts/portal.html` (client portal, top bar). Pages set
  `{% set active_nav = "orders" %}` to highlight their navigation item.

## Macros

| Macro | Use |
| --- | --- |
| `icon(name, label=None)` | SVG icon from `static/img/icons.svg` |
| `field(form.field, hint=…, …)` | Label, input, hint and error for any WTForms field |
| `button(label, variant, …)` | Submit button |
| `link_button(label, href, variant, …)` | Link styled as a button |
| `post_button(action, label, variant, confirm=…, fields=…)` | One-button POST form |
| `status_badge(status)` | Order status pill |
| `badge(label, tone)` | Generic pill (`neutral`, `success`, `warning`, `danger`, `info`) |
| `money(paise)` | Formatted rupees with tabular figures |
| `page_header(title, subtitle, eyebrow)` | Page title; use `{% call page_header(...) %}actions{% endcall %}` for buttons |
| `empty_state(title, text, action_url, action_label, icon_name)` | Nothing-here-yet panel |
| `stat(label, value, hint, tone, href)` | Metric tile |
| `pagination(page, endpoint)` | Previous / next links for a SQLAlchemy pagination object |
| `flashes()` | Flash messages (layouts already include it) |
| `csrf()` | Hidden CSRF input |

Button variants: `primary`, `secondary`, `ghost`, `danger`. Sizes: `sm`, `lg`.

## CSS classes

Layout: `container`, `section`, `stack` (vertical rhythm), `cluster`
(wrapping row), `split` (space-between row), `grid` with `grid--2`,
`grid--3`, `grid--4`, `sidebar-layout` (main + aside).

Components:

- `card`, `card__header`, `card__title`, `card__body`, `card__footer`,
  `card--narrow`, `card--flush` (no padding, e.g. around a table)
- `table-wrap` (scroll container) and `table`; cells: `num` (right-aligned
  figures), `nowrap`, `table__primary` (main text), `table__secondary`
  (muted second line); rows: `is-muted`
- `toolbar` (filters and search above a list), `search` (search input with
  icon), `tabs` / `tab` / `is-active` (status filters)
- `badge`, `badge--<status>`, `badge--<tone>`
- `alert`, `alert--success|info|warning|danger`
- `kv` (definition list of label/value pairs), `kv__row`, `kv__label`,
  `kv__value`
- `timeline`, `timeline__item`, `timeline__dot`, `timeline__body`,
  `timeline__meta`
- `stat`, `stat__label`, `stat__value`, `stat__hint`
- `chart`, `chart__bar`, `chart__label`, `chart__axis` (server-drawn SVG charts)
- `stepper` (quantity input with − / + buttons; add `data-stepper`)
- `product-grid`, `product-card` (catalog)
- `summary` (order totals box), `summary__row`, `summary__total`
- `invoice` (printable invoice page; prints cleanly with `@media print`)
- `empty-state`, `page-header`, `pagination`, `avatar`, `text-link`

Text: `muted`, `mono` (SKUs, order numbers), `money`, `visually-hidden`,
`text-right`, `eyebrow`.

## JavaScript hooks

| Attribute | Behaviour |
| --- | --- |
| `data-confirm="Message"` on a form | Confirm in a dialog before submitting |
| `data-stepper` with `data-step="±1"` buttons | Quantity stepper |
| `data-autosubmit` on a select or checkbox | Submit its form on change |
| `data-copy="text"` on a button | Copy to clipboard |
| `data-print` on a button | Open the print dialog |
| `data-nav-toggle` / `data-sidebar-toggle` with `aria-controls` | Open and close menus |
| `data-allow-resubmit` on a form | Don't disable its buttons after submit |
