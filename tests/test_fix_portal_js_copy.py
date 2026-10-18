"""Copy buttons ([data-copy]) always say what happened, and never leave an unhandled rejection.

Run in Node with the stand-in DOM from test_fix_portal_js_confirm.
"""

import pytest

from tests.test_fix_portal_js_confirm import needs_node, run_js

BUTTON = (
    '<p>Clients sign in at <span class="mono">https://orderflow.example/login</span></p>'
    '<button type="button" class="btn btn--ghost btn--sm" data-copy="https://orderflow.example/login">'
    "<span>Copy client sign-in link</span></button>"
)
ORIGINAL = "<span>Copy client sign-in link</span>"

CLICK = """
const button = h.$("[data-copy]");
h.click(button);
await h.flush();
h.report("label", button.textContent);
h.report("writes", h.clipboardWrites.slice());
h.advance(1499);
h.report("labelLater", button.textContent);
h.advance(1501);
h.report("restored", button.innerHTML);
h.report("busy", button.hasAttribute("data-copying"));
"""


@needs_node
def test_a_successful_copy_says_copied_then_restores_the_label(tmp_path):
    result = run_js(tmp_path, BUTTON, CLICK, clipboard="resolve")
    out = result["out"]
    assert out["writes"] == ["https://orderflow.example/login"]
    assert out["label"] == "Copied"
    assert out["restored"] == ORIGINAL and out["busy"] is False
    assert result["unhandled"] == []


@needs_node
@pytest.mark.parametrize("clipboard", ["reject", "missing", "throw"])
def test_a_refused_copy_says_so_and_is_handled(tmp_path, clipboard):
    result = run_js(tmp_path, BUTTON, CLICK, clipboard=clipboard)
    out = result["out"]
    assert out["label"] == "Couldn't copy"
    assert out["labelLater"] == "Couldn't copy"  # stays long enough to read
    assert out["restored"] == ORIGINAL and out["busy"] is False
    assert result["unhandled"] == []  # no "Uncaught (in promise) NotAllowedError"


@needs_node
def test_clicking_again_while_the_result_shows_keeps_the_real_label(tmp_path):
    script = """
    const button = h.$("[data-copy]");
    h.click(button);
    await h.flush();
    h.click(button);
    await h.flush();
    h.report("label", button.textContent);
    h.advance(5000);
    h.report("restored", button.innerHTML);
    """
    out = run_js(tmp_path, BUTTON, script, clipboard="resolve")["out"]
    assert out["label"] == "Copied"
    assert out["restored"] == ORIGINAL
