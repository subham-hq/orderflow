"""Guards for the design system: templates, static files, icons, CSS and JS.

These scan every template, so pages added later are held to the same rules:
no inline styles or scripts (the Content Security Policy blocks them), no
links to endpoints or files that don't exist, and only icons and classes the
design system actually provides.
"""

import json
import re
import struct
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
TEMPLATES = ROOT / "orderflow" / "templates"
STATIC = ROOT / "orderflow" / "static"
CSS = (STATIC / "css" / "app.css").read_text()
JS = (STATIC / "js" / "app.js").read_text()
DOCS = (ROOT / "docs" / "ui.md").read_text()
ICON_IDS = set(re.findall(r'<symbol id="([\w-]+)"', (STATIC / "img" / "icons.svg").read_text()))

ALL_TEMPLATES = sorted(p for p in TEMPLATES.rglob("*") if p.is_file() and p.suffix in (".html", ".txt"))
PAGE_TEMPLATES = [p for p in ALL_TEMPLATES if "emails" not in p.relative_to(TEMPLATES).parts]


def rel(path):
    return str(path.relative_to(TEMPLATES))


def strip_jinja_comments(source):
    return re.sub(r"\{#.*?#\}", "", source, flags=re.S)


def html_tags(source):
    """The HTML tags in a template, with Jinja code blanked out first."""
    source = strip_jinja_comments(source)
    source = re.sub(r"\{%.*?%\}", " ", source, flags=re.S)
    source = re.sub(r"\{\{.*?\}\}", "x", source, flags=re.S)
    return re.findall(r"<[a-zA-Z][^<>]*>", source)


def csp_problems(source):
    """What the Content Security Policy would block in this template."""
    problems = []
    for tag in html_tags(source):
        name = re.match(r"<([a-zA-Z][\w-]*)", tag).group(1).lower()
        if re.search(r"\sstyle\s*=", tag, re.I):
            problems.append(f"style attribute: {tag}")
        if re.search(r"\son[a-z]+\s*=", tag, re.I):
            problems.append(f"event handler attribute: {tag}")
        if name == "style":
            problems.append("<style> block")
        if name == "script" and not re.search(r"\ssrc\s*=", tag, re.I):
            problems.append(f"inline script: {tag}")
        if re.search(r"""=\s*["']?\s*javascript:""", tag, re.I):
            problems.append(f"javascript: URL: {tag}")
    return problems


# --------------------------------------------------------------------------
# Templates: nothing the Content Security Policy would block
# --------------------------------------------------------------------------


@pytest.mark.parametrize("path", PAGE_TEMPLATES, ids=rel)
def test_templates_have_no_inline_styles_or_scripts(path):
    problems = csp_problems(path.read_text())
    assert not problems, problems


@pytest.mark.parametrize(
    "source",
    [
        '<p style="color:red">x</p>',
        "<p class=\"a\"\n   style='color:red'>x</p>",
        "<style>p{}</style>",
        "<script>alert(1)</script>",
        '<button type="button" onclick="go()">x</button>',
        '<a href="javascript:void(0)">x</a>',
        '<div {% if wide %}style="width:100%"{% endif %}>x</div>',
    ],
)
def test_the_guard_catches_inline_code(source):
    assert csp_problems(source), source


@pytest.mark.parametrize(
    "source",
    [
        '<script src="/static/js/app.js" defer></script>',
        "{% set onboarding = 'Get started' %}{% set style = 'compact' %}",
        '<p class="note">Turn reminders on = off</p>',
        '<a class="btn" href="{{ url_for(\'public.index\') }}">Home</a>',
    ],
)
def test_the_guard_allows_ordinary_markup(source):
    assert not csp_problems(source), source


def test_emails_use_inline_styles_and_no_scripts():
    for path in (TEMPLATES / "emails").glob("*.html"):
        source = path.read_text()
        assert "<script" not in source and "<style" not in source, path.name
        assert "<link" not in source, path.name


# --------------------------------------------------------------------------
# Links and files
# --------------------------------------------------------------------------

URL_FOR = re.compile(r"""url_for\(\s*['"]([\w.]+)['"]""")
STATIC_FILE = re.compile(r"""url_for\(\s*['"]static['"]\s*,\s*filename\s*=\s*['"]([^'"]+)['"]""")
NAV_ENDPOINT = re.compile(r"""['"]((?:admin|portal|auth|public)\.\w+)['"]""")


def test_every_url_for_in_templates_resolves(app):
    endpoints = set(app.view_functions)
    missing = []
    for path in ALL_TEMPLATES:
        source = path.read_text()
        for endpoint in set(URL_FOR.findall(source)) | set(NAV_ENDPOINT.findall(source)):
            if endpoint not in endpoints:
                missing.append(f"{rel(path)}: {endpoint}")
    assert not missing, missing


def test_every_static_file_referenced_from_templates_exists():
    referenced = set()
    for path in ALL_TEMPLATES:
        referenced |= set(STATIC_FILE.findall(path.read_text()))
    for path in ALL_TEMPLATES:
        # Emails link to static files by absolute URL
        referenced |= set(re.findall(r"""\{\{\s*base_url\s*\}\}/static/([^"' ]+)""", path.read_text()))
    assert {"css/app.css", "js/app.js", "img/logo-mark.svg", "img/favicon.svg"} <= referenced
    missing = sorted(name for name in referenced if not (STATIC / name).is_file())
    assert not missing, missing


def png_size(path):
    data = path.read_bytes()
    assert data[:8] == b"\x89PNG\r\n\x1a\n", f"{path.name} is not a PNG"
    return struct.unpack(">II", data[16:24])


def test_web_manifest_icons_exist_with_the_right_sizes():
    manifest = json.loads((STATIC / "site.webmanifest").read_text())
    assert manifest["name"] == "OrderFlow" and manifest["start_url"] == "/start"
    assert re.fullmatch(r"#[0-9a-f]{6}", manifest["theme_color"])
    purposes = set()
    for icon in manifest["icons"]:
        assert icon["src"].startswith("/static/")
        path = STATIC / icon["src"].removeprefix("/static/")
        assert path.is_file(), icon["src"]
        if icon["type"] == "image/png":
            width, height = png_size(path)
            assert f"{width}x{height}" == icon["sizes"], icon["src"]
        purposes.add(icon.get("purpose", "any"))
    assert {"any", "maskable"} <= purposes


def test_app_icons_have_the_sizes_their_names_promise():
    assert png_size(STATIC / "img" / "apple-touch-icon.png") == (180, 180)
    assert png_size(STATIC / "img" / "icon-192.png") == (192, 192)
    assert png_size(STATIC / "img" / "icon-512.png") == (512, 512)


@pytest.mark.parametrize("name", ["logo-mark.svg", "favicon.svg", "icons.svg"])
def test_svg_files_are_well_formed(name):
    root = ET.parse(STATIC / "img" / name).getroot()
    assert root.tag.endswith("svg")
    if name != "icons.svg":
        assert root.get("viewBox") == "0 0 32 32"


def test_static_files_load_nothing_from_other_sites():
    assert "@import" not in CSS
    for url in re.findall(r"url\(([^)]*)\)", CSS):
        assert url.strip("\"'").startswith("data:"), url
    assert "http://" not in JS and "https://" not in JS


# --------------------------------------------------------------------------
# Icons
# --------------------------------------------------------------------------

ICON_USE = re.compile(r"""\bicon\(\s*['"]([\w-]+)['"]|icon_name\s*=\s*['"]([\w-]+)['"]""")


def test_every_icon_named_in_templates_exists():
    used = set()
    for path in ALL_TEMPLATES:
        for a, b in ICON_USE.findall(path.read_text()):
            used.add(a or b)
    assert {"menu", "x", "check", "arrow-right", "logout"} <= used
    missing = sorted(used - ICON_IDS)
    assert not missing, missing


def test_every_icon_on_rendered_pages_exists(client, login, owner, buyer):
    """Covers icons chosen from lists, like the navigation and the landing page features."""
    paths = ["/", "/login", "/signup", "/privacy", "/no-such-page"]
    used = set()
    for path in paths:
        used |= set(re.findall(r"icons\.svg#([\w-]+)", client.get(path).get_data(as_text=True)))
    login(owner)
    used |= set(re.findall(r"icons\.svg#([\w-]+)", client.get("/app/").get_data(as_text=True)))
    client.post("/logout")
    login(buyer)
    used |= set(re.findall(r"icons\.svg#([\w-]+)", client.get("/portal/").get_data(as_text=True)))
    assert {"dashboard", "team", "cart", "receipt", "shield", "plus"} <= used
    missing = sorted(used - ICON_IDS)
    assert not missing, missing


def test_every_icon_listed_in_the_docs_exists():
    listed = re.search(r"Icons in `icons.svg`:(.*?)\n\n", DOCS, re.S).group(1)
    names = set(re.findall(r"`([\w-]+)`", listed))
    assert names and names <= ICON_IDS
    assert names == ICON_IDS, sorted(ICON_IDS - names)


# --------------------------------------------------------------------------
# CSS: every documented class exists, colours come from theme tokens
# --------------------------------------------------------------------------

STATUSES = ("pending", "approved", "fulfilled", "rejected", "cancelled")
TONES = ("success", "info", "warning", "danger", "neutral")
VARIANTS = ("primary", "secondary", "ghost", "danger", "danger-soft", "accent")


def documented_classes():
    section = DOCS[DOCS.index("## CSS classes") : DOCS.index("## Colour and themes")]
    names = set()
    for token in re.findall(r"`([^`]+)`", section):
        parts = [part.strip() for part in token.split("|")]
        if len(parts) > 1:
            # "alert--success|info|warning|danger" means alert--info and so on
            base = parts[0].rsplit("--", 1)[0] + "--"
            parts = [parts[0]] + [base + part for part in parts[1:]]
        for part in parts:
            if not re.fullmatch(r"[a-z][\w-]*(<\w+>|\*)?", part):
                continue
            if part.startswith("data-") or part in ("fill", "stroke") or re.search(r"[a-z]_[a-z]", part):
                continue  # attributes, SVG properties and macro names, not classes
            if part.endswith("<status>"):
                names |= {part.replace("<status>", s) for s in STATUSES}
            elif part.endswith("<tone>"):
                names |= {part.replace("<tone>", t) for t in TONES}
            elif part.endswith("<variant>"):
                names |= {part.replace("<variant>", v) for v in VARIANTS}
            else:
                names.add(part)
    return names


def css_selectors_text():
    no_comments = re.sub(r"/\*.*?\*/", "", CSS, flags=re.S)
    return " ".join(re.findall(r"([^{}]+)\{", no_comments))


def test_every_documented_class_is_styled():
    selectors = css_selectors_text()
    missing = []
    for name in sorted(documented_classes()):
        if name.endswith("*"):
            if f".{name[:-1]}" not in selectors:
                missing.append(name)
        elif not re.search(rf"\.{re.escape(name)}(?![\w-])", selectors):
            missing.append(name)
    assert not missing, missing


def test_docs_list_the_classes_other_pages_rely_on():
    documented = documented_classes()
    for name in (
        "card--flush",
        "table-wrap",
        "num",
        "table__primary",
        "toolbar",
        "search",
        "tab",
        "is-active",
        "badge--pending",
        "badge--neutral",
        "alert--danger",
        "kv__row",
        "timeline__dot",
        "stat__value",
        "chart__bar",
        "chart__axis",
        "stepper",
        "product-card",
        "summary__total",
        "invoice",
        "empty-state",
        "page-header",
        "pagination",
        "avatar",
        "text-link",
        "mono",
        "visually-hidden",
        "eyebrow",
    ):
        assert name in documented, name


def token_block(start_marker):
    start = CSS.index(start_marker)
    depth = 0
    for index in range(CSS.index("{", start), len(CSS)):
        depth += {"{": 1, "}": -1}.get(CSS[index], 0)
        if depth == 0:
            return CSS[start:index]
    raise AssertionError(start_marker)


def test_dark_theme_redefines_every_colour_token():
    light = dict(re.findall(r"(--[\w-]+):\s*([^;]+);", token_block(":root {")))
    dark = dict(re.findall(r"(--[\w-]+):\s*([^;]+);", token_block("@media (prefers-color-scheme: dark)")))
    colours = [name for name, value in light.items() if re.search(r"#[0-9a-fA-F]{3,8}\b|rgba?\(|url\(", value)]
    assert len(colours) > 60
    missing = [name for name in colours if name not in dark]
    assert not missing, missing


def test_components_use_tokens_not_literal_colours():
    no_comments = re.sub(r"/\*.*?\*/", "", CSS, flags=re.S)
    before_print = no_comments[: no_comments.index("@media print")]
    offenders = []
    for line in before_print.splitlines():
        stripped = line.strip()
        if stripped.startswith("--"):
            continue  # a token definition
        if re.search(r"#[0-9a-fA-F]{3,8}\b|rgba?\(|hsla?\(|:\s*(white|black)\b", stripped):
            offenders.append(stripped)
    assert not offenders, offenders


def test_every_var_reference_is_defined():
    defined = set(re.findall(r"(--[\w-]+)\s*:", CSS))
    # var(--name, fallback) is an optional hook; var(--name) must be defined
    used = set(re.findall(r"var\((--[\w-]+)\)", CSS))
    assert not used - defined, sorted(used - defined)


def test_css_is_balanced():
    no_comments = re.sub(r"/\*.*?\*/", "", CSS, flags=re.S)
    no_strings = re.sub(r'"[^"]*"', '""', no_comments)
    depth = 0
    for char in no_strings:
        depth += {"{": 1, "}": -1}.get(char, 0)
        assert depth >= 0
    assert depth == 0


def test_motion_and_print_rules():
    assert "@media (prefers-reduced-motion: reduce)" in CSS
    assert "@media (prefers-reduced-motion: no-preference)" in CSS
    # The hero animation only runs when motion is welcome
    no_pref = CSS.index("@media (prefers-reduced-motion: no-preference) {\n  .hero-order__state--1")
    assert CSS.index("animation: hero-stage-1") > no_pref
    print_block = token_block("@media print")
    assert "size: A4" in print_block
    assert ".no-print" in print_block and "display: none !important" in print_block
    assert "--text: #000000" in print_block


def test_dark_theme_and_layout_breakpoints_exist():
    assert "@media (prefers-color-scheme: dark)" in CSS
    assert "@media (max-width: 959px)" in CSS  # sidebar drawer
    assert "@media (max-width: 899px)" in CSS  # marketing and portal menus
    assert ".page-app.nav-open::after" in CSS  # drawer backdrop


# --------------------------------------------------------------------------
# JavaScript hooks and macros stay in place
# --------------------------------------------------------------------------


def test_app_js_keeps_every_documented_hook():
    for hook in (
        "form[data-confirm]",
        "data-allow-resubmit",
        "[data-nav-toggle]",
        "[data-sidebar-toggle]",
        "[data-dismiss]",
        "[data-step]",
        "[data-stepper]",
        "[data-autosubmit]",
        "[data-copy]",
        "[data-print]",
        'dialog.className = "dialog"',
        "dialog__body",
        "dialog__text",
        "dialog__actions",
        "no-js",
    ):
        assert hook in JS, hook
    assert "eval(" not in JS and "innerHTML = message" not in JS
    assert ".textContent = message" in JS  # confirmation text is never parsed as HTML


def test_documented_macros_exist_with_their_parameters():
    source = (TEMPLATES / "macros" / "ui.html").read_text()
    macros = dict(re.findall(r"\{% macro (\w+)\((.*?)\) -?%\}", source))
    for name in (
        "icon",
        "field",
        "form_errors",
        "button",
        "link_button",
        "post_button",
        "status_badge",
        "badge",
        "money",
        "page_header",
        "empty_state",
        "stat",
        "pagination",
        "flashes",
        "csrf",
    ):
        assert name in macros, name
    assert "confirm=None" in macros["post_button"] and "fields=None" in macros["post_button"]
    assert "hint=None" in macros["field"]
    assert macros["stat"].startswith("label, value, hint=None, tone=None, href=None")
    assert macros["empty_state"].startswith("title, text=None, action_url=None, action_label=None, icon_name=")
    assert "badge=None" in macros["page_header"]


# --------------------------------------------------------------------------
# Button hierarchy and layout conventions
# --------------------------------------------------------------------------


def test_every_button_variant_is_documented_and_styled():
    listed = re.search(r"Button variants:(.*?)\n\n", DOCS, re.S).group(1)
    assert set(re.findall(r"`([\w-]+)`", listed)) >= set(VARIANTS)
    for variant in VARIANTS:
        assert f".btn--{variant} {{" in CSS, variant


def test_soft_danger_buttons_still_confirm_with_a_red_button():
    assert "/^danger/.test(variant" in JS


def test_pages_use_soft_danger_for_destructive_buttons():
    """Solid red is kept for the confirm dialog; on pages it would outshout the primary action."""
    offenders = []
    for path in PAGE_TEMPLATES:
        for match in re.finditer(r'(?<![\w-])variant="danger"', path.read_text()):
            offenders.append(f"{rel(path)}: {match.group(0)}")
    assert not offenders, offenders


def test_workspace_and_portal_pages_dont_use_the_public_section_band():
    """`section` adds the landing page's big padding; app pages are spaced by their layout."""
    offenders = []
    for path in PAGE_TEMPLATES:
        if rel(path).split("/")[0] not in ("admin", "portal", "shared", "auth"):
            continue
        for classes in re.findall(r'class="([^"]*)"', path.read_text()):
            if "section" in classes.split():
                offenders.append(f"{rel(path)}: {classes}")
    assert not offenders, offenders


def test_scroll_reveal_never_hides_content_without_js_or_with_reduced_motion():
    # Content is only hidden once app.js has marked the page reveal-ready,
    # and app.js skips that when people prefer less motion
    assert '"(prefers-reduced-motion: reduce)"' in JS and 'classList.add("reveal-ready")' in JS
    hiding = [m.start() for m in re.finditer(r"\[data-reveal[^\]]*\][^{]*\{\s*opacity: 0", CSS)]
    assert len(hiding) >= 2
    for start in hiding:
        rule_start = CSS.rindex("}", 0, start) + 1
        assert ".reveal-ready" in CSS[rule_start:start]
        nearest_media = CSS[CSS.rindex("@media", 0, start) :]
        assert nearest_media.startswith("@media (prefers-reduced-motion: no-preference)")
    print_block = token_block("@media print")
    assert "[data-reveal]" in print_block


def test_app_content_is_spaced_by_the_layout():
    flow = CSS[CSS.index(".main,\n.portal-main > .container {") :]
    assert "gap: var(--space-6);" in flow[: flow.index("}")]
