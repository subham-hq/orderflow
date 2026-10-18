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

## Layout blocks

| Layout | Blocks | Notes |
| --- | --- | --- |
| all (`base.html`) | `title`, `description`, `meta`, `body_class` | `title` is the page name; " · OrderFlow" is added for you |
| `layouts/app.html` | `content`, `topbar_title`, `topbar_actions` | `topbar_title` falls back to `title`. `active_nav`: `dashboard`, `orders`, `products`, `clients`, `bulletins`, `team`, `settings`. A view may pass `nav_badges={'orders': 4}` to show a count on a nav item |
| `layouts/portal.html` | `content` | `active_nav`: `home`, `catalog`, `orders`, `account`, `cart`. The cart count comes from `session['cart']` |
| `layouts/auth.html` | `content`, `aside` | `aside` replaces the brand panel shown on wide screens |
| `layouts/marketing.html` | `content`, `flashes` | Override `flashes` to show messages somewhere else on the page |

## Macros

| Macro | Use |
| --- | --- |
| `icon(name, label=None)` | SVG icon from `static/img/icons.svg` |
| `field(form.field, hint=…, …)` | Label, input, hint and error for any WTForms field |
| `form_errors(form)` | Form-level error in an alert |
| `button(label, variant, …)` | Submit button |
| `link_button(label, href, variant, …)` | Link styled as a button |
| `post_button(action, label, variant, confirm=…, fields=…)` | One-button POST form |
| `status_badge(status)` | Order status pill |
| `badge(label, tone)` | Generic pill (`neutral`, `success`, `warning`, `danger`, `info`) |
| `money(paise)` | Formatted rupees with tabular figures |
| `page_header(title, subtitle, eyebrow)` | Page title; use `{% call page_header(...) %}actions{% endcall %}` for buttons |
| `empty_state(title, text, action_url, action_label, icon_name)` | Nothing-here-yet panel |
| `stat(label, value, hint, tone, href)` | Metric tile (`tone`: `warning`, `success`, `danger`, `info`) |
| `pagination(page, endpoint)` | Previous / next links for a SQLAlchemy pagination object |
| `flashes()` | Flash messages (layouts already include it) |
| `csrf()` | Hidden CSRF input |

Button variants: `primary`, `secondary`, `ghost`, `danger`, and `accent`
(marigold, for dark brand panels). Sizes: `sm`, `lg`. Add `btn--block` for a
full-width button.

Icons in `icons.svg`: `dashboard`, `orders`, `box`, `users`, `team`,
`megaphone`, `settings`, `logout`, `menu`, `home`, `cart`, `chevron-left`,
`chevron-right`, `chevron-down`, `check-circle`, `check`, `info`, `alert`,
`x`, `mail`, `plus`, `minus`, `search`, `truck`, `clock`, `download`, `edit`,
`trash`, `eye`, `eye-off`, `arrow-right`, `receipt`, `printer`, `user-plus`,
`ban`, `rotate`, `send`, `filter`, `tag`, `shield`, `chart`, `sparkle`,
`building`, `external`.

## CSS classes

Layout: `container` (`container--narrow`), `section`, `stack` (vertical
rhythm; `stack--xs`, `stack--sm`, `stack--lg`, `stack--xl`), `cluster`
(wrapping row; `cluster--end`), `split` (space-between row), `grid` with
`grid--2`, `grid--3`, `grid--4`, `sidebar-layout` (main + aside).

Components:

- `btn`, `btn--<variant>`, `btn--sm`, `btn--lg`, `btn--block`;
  `inline-form` (the form `post_button` renders)
- Forms (the `field` macro writes these): `field`, `field__label`,
  `field__hint`, `field__error`, `field--error`, `field--check`, `check`,
  `check__input`, `check__label`; controls `input`, `select`, `textarea`;
  `form-actions` (row of buttons under a form)
- `card`, `card__header`, `card__title`, `card__subtitle`, `card__body`,
  `card__footer`, `card--narrow`, `card--flush` (no padding, e.g. around a
  table)
- `table-wrap` (scroll container) and `table`; cells: `num` (right-aligned
  figures), `nowrap`, `table__primary` (main text), `table__secondary`
  (muted second line); rows: `is-muted`. Tables with four or more columns
  keep a minimum width and scroll sideways on phones.
- `toolbar` (filters and search above a list), `toolbar__field` (a labelled
  control in a toolbar), `toolbar__spacer`; `search` (put
  `{{ icon('search') }}` and an `input` inside); `tabs` / `tab` /
  `is-active` (status filters) with `tab__count`; `chips` / `chip` /
  `chip__count` (pill filters such as catalog categories; mark the current
  one with `is-active` or `aria-current="page"`)
- `badge`, `badge--<status>`, `badge--<tone>`, `badge__dot`
- `alert`, `alert--success|info|warning|danger`, `alert__text`,
  `alert__title`, `alert__close`; `flashes` (stack of flash alerts)
- `kv` (definition list of label/value pairs), `kv__row`, `kv__label`,
  `kv__value`, `kv--compact`
- `timeline`, `timeline__item`, `timeline__item--<status>` (dot colour),
  `timeline__item--next` (hollow dot for a step still to come),
  `timeline__dot`, `timeline__body`, `timeline__title`, `timeline__meta`,
  `timeline__note`, `timeline--compact`
- `stat`, `stat__label`, `stat__value`, `stat__hint`, `stat--warning|success|danger|info`
- `chart`, `chart__svg`, `chart__bar` (`chart__bar--current`,
  `chart__bar--accent`, `chart__bar--empty`), `chart__label`
  (`chart__label--x`, `chart__label--y`, `chart__label--strong`),
  `chart__axis` (`chart__axis--grid` for dashed grid lines), `chart__line`,
  `chart__area`, `chart__legend` (server-drawn SVG charts; colours come
  from CSS, so don't set `fill` or `stroke` in the markup)
- `meter` (an SVG bar showing a share), `meter__track`, `meter__fill`
- `stepper` (quantity input with − / + buttons; add `data-stepper`),
  `stepper__button`, `stepper__input`
- `product-grid`, `product-card` (catalog), with `product-card__category`,
  `product-card__name`, `product-card__sku`, `product-card__description`,
  `product-card__price`, `product-card__tax`, `product-card__in-cart`,
  `product-card__actions`, `product-card__body` (the details, which grow so
  every card's form lines up), `product-card__form`
- `summary` (order totals box), `summary__row`, `summary__total`
- `invoice` (printable invoice page; prints cleanly with `@media print`),
  `no-print` (hidden when printing)
- `empty-state`, `empty-state__icon`, `empty-state__title`,
  `empty-state__text`, `empty-state--compact` (inside a card)
- `page-header`, `page-header__eyebrow`, `page-header__title`,
  `page-header__subtitle`, `page-header__actions`
- `pagination`, `avatar` (`avatar--sm`, `avatar--lg`), `text-link`
- `queue`, `queue__item`, `queue__link`, `queue__main`, `queue__amount`
  (a list of orders that need attention)
- `checklist__list`, `checklist__item` (`is-done`), `checklist__marker`,
  `checklist__body`, `checklist__title`, `checklist__text`,
  `checklist__progress` (setup steps)
- `weave` (indigo brand panel with a woven texture)

Text: `muted`, `faint`, `lead`, `mono` (SKUs, order numbers), `money`,
`visually-hidden`, `text-right`, `eyebrow`, `prose` (long-form text such as
the legal pages), `text-pre-line` and `order-note` (keep the line breaks
people typed, for notes, bulletins and reasons).

Shell classes (used by the layouts, not by pages): `brand`, `brand__mark`,
`brand__name`, `brand__monogram`, `brand__text`, `brand__sub`, `nav-link`,
`nav-link--button`, `nav-link__count`, `menu-button`, `site-header`,
`site-nav`, `site-footer`, `auth`, `auth__*`, `auth-visual__*`, `shell`,
`sidebar`, `sidebar__*`, `user-chip`, `topbar`, `topbar__*`, `main`,
`portal-header`, `portal-nav`, `cart-link`, `cart-link__count`,
`portal-main`, `portal-footer`, `error-page`. Landing page sections use
`hero`, `hero-order`, `hero-mail`, `audience`, `landing-section`,
`section-intro`, `section-title`, `feature-row`, `feature-grid`,
`feature`, `showcase`, `review`, `ticks`, `band--ink`, `steps`, `step`,
`pricing`, `plan`, `faq`, `contact`, `cta`.

## Colour and themes

Every colour is a custom property on `:root` (for example `--surface`,
`--text`, `--muted`, `--line`, `--primary`, `--accent`, and per-status
`--pending-bg` / `--pending-fg` / `--pending-dot`). The dark theme
redefines the same properties under `prefers-color-scheme: dark`, and print
redefines them for black text on white. New styles must use these
properties, never a literal colour.

## JavaScript hooks

| Attribute | Behaviour |
| --- | --- |
| `data-confirm="Message"` on a form | Confirm in a dialog before submitting |
| `data-stepper` with `data-step="±1"` buttons | Quantity stepper |
| `data-autosubmit` on a select or checkbox | Submit its form on change |
| `data-copy="text"` on a button | Copy to clipboard |
| `data-print` on a button | Open the print dialog |
| `data-nav-toggle` / `data-sidebar-toggle` with `aria-controls` | Open and close menus (Escape or a click outside closes them) |
| `data-allow-resubmit` on a form | Don't disable its buttons after submit |
| `data-dismiss` inside an alert | Remove the alert |

Password inputs rendered with `field()` get a Show / Hide button
automatically. Controls with `data-dismiss`, `data-print`, `data-copy` and
`data-step` are hidden when JavaScript is off, so pages must still work
without them.
