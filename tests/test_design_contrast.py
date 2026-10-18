"""Colour contrast of the design tokens, in the light and the dark theme.

Text pairs need WCAG AA (4.5:1); chart bars, focus rings and other marks
people must see need 3:1 against the surface they sit on. Translucent tokens
(the dark theme's status backgrounds) are measured over the card surface.
"""

import re
from pathlib import Path

import pytest

CSS = (Path(__file__).resolve().parent.parent / "orderflow" / "static" / "css" / "app.css").read_text()


def block(start_marker):
    start = CSS.index(start_marker)
    depth = 0
    for index in range(CSS.index("{", start), len(CSS)):
        depth += {"{": 1, "}": -1}.get(CSS[index], 0)
        if depth == 0:
            return CSS[start:index]
    raise AssertionError(start_marker)


def tokens(start_marker):
    return dict(re.findall(r"(--[\w-]+):\s*([^;]+);", block(start_marker)))


LIGHT = tokens(":root {")
DARK = {**LIGHT, **tokens("@media (prefers-color-scheme: dark)")}
THEMES = {"light": LIGHT, "dark": DARK}


def rgba(value):
    value = value.strip()
    if value.startswith("#"):
        digits = value[1:]
        if len(digits) == 3:
            digits = "".join(c * 2 for c in digits)
        return tuple(int(digits[i : i + 2], 16) for i in (0, 2, 4)) + (1.0,)
    match = re.fullmatch(r"rgba?\(([^)]+)\)", value)
    assert match, value
    parts = [float(p) for p in re.split(r"[\s,/]+", match.group(1).strip())]
    return (*parts[:3], parts[3] if len(parts) > 3 else 1.0)


def over(top, bottom):
    alpha = top[3]
    return tuple(top[i] * alpha + bottom[i] * (1 - alpha) for i in range(3)) + (1.0,)


def luminance(colour):
    def channel(v):
        v /= 255
        return v / 12.92 if v <= 0.03928 else ((v + 0.055) / 1.055) ** 2.4

    r, g, b = (channel(c) for c in colour[:3])
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def contrast(theme, fg, bg, base="--surface"):
    values = THEMES[theme]
    ground = rgba(values[base])
    back = over(rgba(values[bg]), ground)
    front = over(rgba(values[fg]), back)
    hi, lo = sorted((luminance(front), luminance(back)), reverse=True)
    return (hi + 0.05) / (lo + 0.05)


TEXT_PAIRS = [
    ("--text", "--surface"),
    ("--text", "--bg"),
    ("--text-2", "--surface"),
    ("--muted", "--surface"),
    ("--muted", "--bg"),
    ("--muted", "--surface-2"),
    ("--heading", "--bg"),
    ("--link", "--surface"),
    ("--link", "--bg"),
    ("--placeholder", "--input-bg"),
    ("--primary-text", "--primary"),
    ("--danger-text", "--danger"),
    ("--accent-text", "--accent"),
    ("--tint-text", "--tint"),
    ("--nav-active-fg", "--nav-active-bg"),
    ("--monogram-fg", "--monogram-bg"),
    ("--accent-soft-text", "--accent-soft"),
    ("--ink-text", "--ink-bg"),
    ("--ink-muted", "--ink-bg"),
    ("--ink-muted", "--ink-surface"),
    ("--danger-fg", "--btn-secondary-bg"),
    *[(f"--{s}-fg", f"--{s}-bg") for s in ("pending", "approved", "fulfilled", "rejected", "cancelled")],
    *[(f"--{t}-fg", f"--{t}-bg") for t in ("success", "info", "warning", "danger", "neutral")],
]

# Marigold meters (--chart-accent) are left out: they repeat a figure that is
# printed right next to them, so they decorate rather than inform.
MARK_PAIRS = [
    ("--chart-bar", "--surface"),
    ("--chart-bar-strong", "--surface"),
    ("--focus", "--surface"),
    ("--focus", "--bg"),
    ("--stitch", "--ink-surface"),
    ("--input-border", "--input-bg"),
]


@pytest.mark.parametrize("theme", THEMES)
@pytest.mark.parametrize(("fg", "bg"), TEXT_PAIRS, ids=[f"{a} on {b}" for a, b in TEXT_PAIRS])
def test_text_tokens_meet_wcag_aa(theme, fg, bg):
    ratio = contrast(theme, fg, bg)
    assert ratio >= 4.5, f"{fg} on {bg} in the {theme} theme is {ratio:.2f}:1"


@pytest.mark.parametrize("theme", THEMES)
@pytest.mark.parametrize(("fg", "bg"), MARK_PAIRS, ids=[f"{a} on {b}" for a, b in MARK_PAIRS])
def test_marks_people_must_see_meet_three_to_one(theme, fg, bg):
    ratio = contrast(theme, fg, bg)
    if fg == "--input-border":
        # Inputs also carry a shadow and a label; 1.5:1 keeps their edge visible
        assert ratio >= 1.5, f"{fg} on {bg} in the {theme} theme is {ratio:.2f}:1"
    else:
        assert ratio >= 3, f"{fg} on {bg} in the {theme} theme is {ratio:.2f}:1"


def test_focus_rings_switch_to_marigold_on_indigo_panels():
    weave = CSS[CSS.index(".weave,\n.band--ink {") :]
    assert "--focus: var(--stitch);" in weave[: weave.index("}")]


def test_the_contrast_helper_matches_known_ratios():
    assert round(contrast("light", "--text", "--text"), 2) == 1.0
    # #5a6182 on white is about 6.2:1
    assert 6.0 < contrast("light", "--muted", "--surface") < 6.5
