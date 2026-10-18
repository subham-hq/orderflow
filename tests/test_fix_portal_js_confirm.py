"""The confirmation dialog in app.js, run in Node against a small stand-in for the DOM.

The rest of the suite can only read app.js as text. These tests run it: they
load it into a minimal document (just enough DOM for its handlers), click
buttons and check what the dialog exposes to assistive technology, where
focus lands, and that a confirmed form asks again when the page comes back
from the back/forward cache. They are skipped where Node isn't installed.
"""

import json
import shutil
import subprocess
from pathlib import Path

import pytest

APP_JS = Path(__file__).resolve().parent.parent / "orderflow" / "static" / "js" / "app.js"
NODE = shutil.which("node")

needs_node = pytest.mark.skipif(NODE is None, reason="Node.js is needed to run app.js")

# A stand-in DOM: elements, text, a tiny HTML parser and selector matcher,
# events that bubble to the document, <form> submission, <dialog>, a fake
# clock and navigator.clipboard. Only what app.js uses.
HARNESS = r"""
"use strict";
const vm = require("vm");
const fs = require("fs");

const config = JSON.parse(fs.readFileSync(process.argv[3], "utf8"));
const unhandled = [];
process.on("unhandledRejection", (reason) => {
  unhandled.push(reason && reason.name ? reason.name + ": " + reason.message : String(reason));
});

const toData = (key) => "data-" + key.replace(/[A-Z]/g, (c) => "-" + c.toLowerCase());
const escapeText = (s) => s.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
const escapeAttr = (s) => escapeText(s).replace(/"/g, "&quot;");
const unescape = (s) =>
  s.replace(/&quot;/g, '"').replace(/&lt;/g, "<").replace(/&gt;/g, ">").replace(/&#39;/g, "'").replace(/&amp;/g, "&");

class Text {
  constructor(data) { this.nodeType = 3; this.data = data; this.parentNode = null; }
  get textContent() { return this.data; }
  get outerHTML() { return escapeText(this.data); }
}

class Event {
  constructor(type, init) {
    init = init || {};
    this.type = type;
    this.bubbles = !!init.bubbles;
    this.cancelable = init.cancelable !== false;
    this.defaultPrevented = false;
    this.target = null;
    this.stopped = false;
  }
  preventDefault() { if (this.cancelable) this.defaultPrevented = true; }
  stopPropagation() { this.stopped = true; }
}

function matchCompound(el, selector) {
  const head = selector.match(/^([a-zA-Z][\w-]*|\*)?/)[0];
  if (head && head !== "*" && el.tagName.toLowerCase() !== head.toLowerCase()) return false;
  const rest = selector.slice(head.length);
  const part = /#([\w-]+)|\.([\w-]+)|\[([\w-]+)(?:="([^"]*)")?\]|:not\(([^)]*)\)/g;
  let consumed = 0;
  let m;
  while ((m = part.exec(rest))) {
    if (m.index !== consumed) throw new Error("Unsupported selector: " + selector);
    consumed = part.lastIndex;
    if (m[1] !== undefined && el.id !== m[1]) return false;
    if (m[2] !== undefined && !el.classList.contains(m[2])) return false;
    if (m[3] !== undefined) {
      const value = el.getAttribute(m[3]);
      if (value === null || (m[4] !== undefined && value !== m[4])) return false;
    }
    if (m[5] !== undefined && matchCompound(el, m[5].trim())) return false;
  }
  if (consumed !== rest.length) throw new Error("Unsupported selector: " + selector);
  return true;
}

function matchesSelector(el, selector) {
  return selector.split(",").some((group) => {
    const steps = group.trim().split(/\s+/);
    if (!matchCompound(el, steps[steps.length - 1])) return false;
    let index = steps.length - 2;
    for (let node = el.parentNode; index >= 0 && node && node.nodeType === 1; node = node.parentNode) {
      if (matchCompound(node, steps[index])) index--;
    }
    return index < 0;
  });
}

const VOID = new Set(["input", "br", "img", "hr", "meta", "link"]);

function parseInto(parent, html) {
  const doc = parent.ownerDocument;
  const token = /<\/([a-zA-Z][\w-]*)\s*>|<([a-zA-Z][\w-]*)((?:\s+[^\s=>\/]+(?:\s*=\s*(?:"[^"]*"|'[^']*'|[^\s>]+))?)*)\s*(\/?)>|([^<]+)/g;
  const stack = [parent];
  let m;
  while ((m = token.exec(html))) {
    const top = stack[stack.length - 1];
    if (m[1]) {
      const tag = m[1].toUpperCase();
      for (let i = stack.length - 1; i > 0; i--) {
        if (stack[i].tagName === tag) { stack.length = i; break; }
      }
    } else if (m[2]) {
      const el = doc.createElement(m[2]);
      const attr = /([^\s=>\/]+)(?:\s*=\s*(?:"([^"]*)"|'([^']*)'|([^\s>]+)))?/g;
      let a;
      while ((a = attr.exec(m[3] || ""))) el.setAttribute(a[1], unescape(a[2] ?? a[3] ?? a[4] ?? ""));
      top.appendChild(el);
      if (!VOID.has(m[2].toLowerCase()) && !m[4]) stack.push(el);
    } else if (m[5]) {
      top.appendChild(new Text(unescape(m[5])));
    }
  }
}

class Element {
  constructor(doc, tag) {
    this.nodeType = 1;
    this.ownerDocument = doc;
    this.tagName = tag.toUpperCase();
    this.attrs = new Map();
    this.childNodes = [];
    this.parentNode = null;
    this.listeners = {};
    this.disabled = false;
    const el = this;
    this.dataset = new Proxy({}, {
      get: (_, key) => (typeof key === "string" && el.hasAttribute(toData(key)) ? el.getAttribute(toData(key)) : undefined),
      set: (_, key, value) => { el.setAttribute(toData(key), String(value)); return true; },
      deleteProperty: (_, key) => { el.removeAttribute(toData(key)); return true; },
      has: (_, key) => el.hasAttribute(toData(key)),
    });
    this.classList = {
      names: () => el.className.split(/\s+/).filter(Boolean),
      add(...names) {
        const list = this.names();
        names.forEach((name) => { if (!list.includes(name)) list.push(name); });
        el.className = list.join(" ");
      },
      remove(...names) { el.className = this.names().filter((name) => !names.includes(name)).join(" "); },
      contains(name) { return this.names().includes(name); },
      toggle(name, force) {
        const on = force === undefined ? !this.contains(name) : !!force;
        if (on) this.add(name); else this.remove(name);
        return on;
      },
    };
  }
  get children() { return this.childNodes.filter((node) => node.nodeType === 1); }
  get className() { return this.getAttribute("class") || ""; }
  set className(value) { this.setAttribute("class", value); }
  get id() { return this.getAttribute("id") || ""; }
  set id(value) { this.setAttribute("id", value); }
  get value() { return this.getAttribute("value") || ""; }
  set value(value) { this.setAttribute("value", value); }
  get type() { return this.getAttribute("type") || (this.tagName === "BUTTON" ? "submit" : "text"); }
  get form() {
    let node = this.parentNode;
    while (node && node.tagName !== "FORM") node = node.parentNode;
    return node && node.tagName === "FORM" ? node : null;
  }
  setAttribute(name, value) { this.attrs.set(name.toLowerCase(), String(value)); }
  getAttribute(name) { name = name.toLowerCase(); return this.attrs.has(name) ? this.attrs.get(name) : null; }
  hasAttribute(name) { return this.attrs.has(name.toLowerCase()); }
  removeAttribute(name) { this.attrs.delete(name.toLowerCase()); }
  appendChild(child) {
    if (child.parentNode) child.parentNode.removeChild(child);
    child.parentNode = this;
    this.childNodes.push(child);
    return child;
  }
  insertBefore(child, ref) {
    if (child.parentNode) child.parentNode.removeChild(child);
    child.parentNode = this;
    const index = this.childNodes.indexOf(ref);
    this.childNodes.splice(index < 0 ? this.childNodes.length : index, 0, child);
    return child;
  }
  removeChild(child) {
    const index = this.childNodes.indexOf(child);
    if (index >= 0) this.childNodes.splice(index, 1);
    child.parentNode = null;
    return child;
  }
  remove() { if (this.parentNode) this.parentNode.removeChild(this); }
  contains(other) {
    for (let node = other; node; node = node.parentNode) if (node === this) return true;
    return false;
  }
  get isConnected() {
    let node = this;
    while (node.parentNode) node = node.parentNode;
    return node === this.ownerDocument;
  }
  get textContent() { return this.childNodes.map((node) => node.textContent).join(""); }
  set textContent(value) {
    this.childNodes.forEach((node) => { node.parentNode = null; });
    this.childNodes = [];
    if (String(value)) this.appendChild(new Text(String(value)));
  }
  get innerHTML() { return this.childNodes.map((node) => node.outerHTML).join(""); }
  set innerHTML(html) {
    this.childNodes.forEach((node) => { node.parentNode = null; });
    this.childNodes = [];
    parseInto(this, String(html));
  }
  get outerHTML() {
    const tag = this.tagName.toLowerCase();
    const attrs = [...this.attrs].map(([name, value]) => ` ${name}="${escapeAttr(value)}"`).join("");
    return `<${tag}${attrs}>${this.innerHTML}</${tag}>`;
  }
  matches(selector) { return matchesSelector(this, selector); }
  closest(selector) {
    for (let node = this; node && node.nodeType === 1; node = node.parentNode) {
      if (node.matches(selector)) return node;
    }
    return null;
  }
  querySelectorAll(selector) {
    const found = [];
    const walk = (node) => node.children.forEach((child) => {
      if (child.matches(selector)) found.push(child);
      walk(child);
    });
    walk(this);
    return found;
  }
  querySelector(selector) { return this.querySelectorAll(selector)[0] || null; }
  addEventListener(type, listener) { (this.listeners[type] = this.listeners[type] || []).push(listener); }
  removeEventListener(type, listener) {
    this.listeners[type] = (this.listeners[type] || []).filter((fn) => fn !== listener);
  }
  dispatchEvent(event) {
    event.target = this;
    const path = [];
    for (let node = this; node; node = node.parentNode) path.push(node);
    for (const node of event.bubbles ? path : [this]) {
      event.currentTarget = node;
      for (const listener of [...(node.listeners[event.type] || [])]) listener.call(node, event);
      if (event.stopped) break;
    }
    return !event.defaultPrevented;
  }
  focus() { this.ownerDocument.activeElement = this; }
  blur() {}
  scrollIntoView() {}
  getBoundingClientRect() { return { top: 0 }; }
  get offsetParent() { return this.parentNode; }
}

class Form extends Element {
  constructor(doc) { super(doc, "form"); }
  requestSubmit(submitter) {
    if (submitter && submitter.form !== this) throw new Error("NotFoundError: the submitter isn't in this form");
    const event = new Event("submit", { bubbles: true, cancelable: true });
    event.submitter = submitter || null;
    if (this.dispatchEvent(event)) this.send(submitter);
  }
  submit() { this.send(null); }
  send(submitter) {
    if ((this.getAttribute("method") || "get").toLowerCase() === "dialog") {
      const dialog = this.closest("dialog");
      if (dialog) dialog.close(submitter ? submitter.value : undefined);
      return;
    }
    this.ownerDocument.submissions.push({ action: this.getAttribute("action"), button: submitter && submitter.textContent });
  }
}

class Dialog extends Element {
  constructor(doc) { super(doc, "dialog"); this.open = false; this.returnValue = ""; }
  showModal() {
    if (!this.isConnected) throw new Error("InvalidStateError: the dialog isn't in the document");
    this.open = true;
    this.setAttribute("open", "");
  }
  close(value) {
    if (!this.open) return;
    this.open = false;
    this.removeAttribute("open");
    if (value !== undefined) this.returnValue = value;
    this.dispatchEvent(new Event("close"));
  }
}

class Document extends Element {
  constructor() {
    super(null, "#document");
    this.ownerDocument = this;
    this.nodeType = 9;
    this.submissions = [];
    this.documentElement = this.appendChild(new Element(this, "html"));
    this.documentElement.className = "no-js";
    this.body = this.documentElement.appendChild(new Element(this, "body"));
    this.activeElement = this.body;
  }
  createElement(tag) {
    const name = tag.toLowerCase();
    if (name === "dialog") return new Dialog(this);
    if (name === "form") return new Form(this);
    return new Element(this, name);
  }
  getElementById(id) { return this.querySelectorAll("[id]").find((el) => el.id === id) || null; }
}

const document = new Document();
document.body.innerHTML = config.body;

let now = 0;
const timers = [];
const clipboardWrites = [];
const clipboards = {
  resolve: { writeText: (text) => { clipboardWrites.push(text); return Promise.resolve(); } },
  reject: {
    writeText: (text) => {
      clipboardWrites.push(text);
      const error = new Error("Write permission denied.");
      error.name = "NotAllowedError";
      return Promise.reject(error);
    },
  },
  throw: { writeText: () => { throw new TypeError("Clipboard is not available"); } },
  missing: undefined,
};
const windowListeners = {};
const sandbox = {
  document,
  navigator: { clipboard: clipboards[config.clipboard || "resolve"] },
  Event,
  HTMLDialogElement: Dialog,
  console,
  innerHeight: 800,
  setTimeout: (fn, ms) => { timers.push({ fn, at: now + (ms || 0) }); return timers.length; },
  clearTimeout: () => {},
  matchMedia: () => ({ matches: false, addEventListener() {}, addListener() {} }),
  addEventListener: (type, listener) => { (windowListeners[type] = windowListeners[type] || []).push(listener); },
  print: () => {},
};
sandbox.window = sandbox;
vm.createContext(sandbox);
vm.runInContext(fs.readFileSync(process.argv[2], "utf8"), sandbox, { filename: "app.js" });

const out = {};
const h = {
  document,
  $: (selector) => document.querySelector(selector),
  $$: (selector) => document.querySelectorAll(selector),
  report: (key, value) => { out[key] = value; },
  flush: () => new Promise((resolve) => setImmediate(resolve)),
  advance(ms) {
    now += ms;
    for (;;) {
      timers.sort((a, b) => a.at - b.at);
      if (!timers.length || timers[0].at > now) break;
      timers.shift().fn();
    }
  },
  // A user's click: the click event, then for a submit button the form submission
  click(el) {
    if (el.disabled) return;
    const proceed = el.dispatchEvent(new Event("click", { bubbles: true }));
    if (proceed && el.tagName === "BUTTON" && el.type === "submit" && el.form) el.form.requestSubmit(el);
  },
  escape(dialog) { if (dialog.dispatchEvent(new Event("cancel"))) dialog.close(); },
  pageshow(persisted) {
    const event = new Event("pageshow");
    event.persisted = persisted;
    (windowListeners.pageshow || []).forEach((listener) => listener(event));
  },
  submissions: () => document.submissions.map((s) => s.action),
  clipboardWrites,
  describe(dialog) {
    const byId = (id) => (id ? document.getElementById(id) : null);
    const textOf = (ids) => (ids ? ids.split(/\s+/).map((id) => (byId(id) ? byId(id).textContent : null)).join(" ") : null);
    const active = document.activeElement;
    return {
      open: dialog.open,
      role: dialog.getAttribute("role"),
      label: dialog.getAttribute("aria-label"),
      labelledby: textOf(dialog.getAttribute("aria-labelledby")),
      describedby: textOf(dialog.getAttribute("aria-describedby")),
      focused: active && dialog.contains(active) ? { text: active.textContent, value: active.value } : null,
      buttons: dialog.querySelectorAll("button").map((b) => ({ text: b.textContent, value: b.value, class: b.className })),
    };
  },
};

(async () => {
  const AsyncFunction = Object.getPrototypeOf(async function () {}).constructor;
  await new AsyncFunction("h", config.script)(h);
  await h.flush();
  await h.flush();
  process.stdout.write(JSON.stringify({ out, unhandled }));
})().catch((error) => {
  process.stderr.write(String(error && error.stack ? error.stack : error));
  process.exit(1);
});
"""


def run_js(tmp_path, body, script, clipboard="resolve", app_js=APP_JS):
    """Load app.js into a document with this body, run the async script, return what it reported."""
    harness = tmp_path / "harness.js"
    harness.write_text(HARNESS)
    config = tmp_path / "config.json"
    config.write_text(json.dumps({"body": body, "script": script, "clipboard": clipboard}))
    result = subprocess.run(
        [NODE, str(harness), str(app_js), str(config)], capture_output=True, text=True, timeout=30, check=False
    )
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


DELETE_FORM = (
    '<form method="post" action="/app/bulletins/7/delete" class="inline-form"'
    ' data-confirm="Delete “Diwali dispatch schedule”? Clients will stop seeing it, and this can&#39;t be undone."'
    ' data-confirm-label="Delete bulletin" data-confirm-variant="danger-soft">'
    '<button type="submit" class="btn btn--danger-soft"><span>Delete</span></button></form>'
)
APPROVE_FORM = (
    '<form method="post" action="/app/orders/ORD-000001/status" class="inline-form"'
    ' data-confirm="Approve ORD-000001 for ₹5,015.00?" data-confirm-label="Approve order"'
    ' data-confirm-variant="primary"><button type="submit" class="btn btn--primary"><span>Approve order</span></button>'
    "</form>"
)
MESSAGE = "Delete “Diwali dispatch schedule”? Clients will stop seeing it, and this can't be undone."

OPEN_DIALOG = """
h.click(h.$("form[data-confirm] button"));
await h.flush();
const dialog = h.$("dialog");
h.report("dialog", h.describe(dialog));
h.report("submittedWhileOpen", h.submissions());
"""


@needs_node
def test_the_dialog_is_named_and_described_by_its_question(tmp_path):
    result = run_js(tmp_path, DELETE_FORM, OPEN_DIALOG)
    dialog = result["out"]["dialog"]
    assert dialog["open"] is True
    assert dialog["role"] == "alertdialog"
    assert dialog["labelledby"] == "Please confirm"
    assert dialog["describedby"] == MESSAGE  # screen readers read the question as the dialog opens
    assert result["out"]["submittedWhileOpen"] == []


@needs_node
def test_a_destructive_confirmation_starts_on_cancel(tmp_path):
    dialog = run_js(tmp_path, DELETE_FORM, OPEN_DIALOG)["out"]["dialog"]
    assert dialog["focused"] == {"text": "Cancel", "value": "cancel"}
    assert [(b["text"], b["class"]) for b in dialog["buttons"]] == [
        ("Cancel", "btn btn--ghost"),
        ("Delete bulletin", "btn btn--danger"),  # still confirmed with a red button
    ]


@needs_node
def test_an_ordinary_confirmation_starts_on_its_confirm_button(tmp_path):
    dialog = run_js(tmp_path, APPROVE_FORM, OPEN_DIALOG)["out"]["dialog"]
    assert dialog["focused"] == {"text": "Approve order", "value": "ok"}
    assert dialog["describedby"] == "Approve ORD-000001 for ₹5,015.00?"


@needs_node
def test_each_dialog_gets_its_own_ids(tmp_path):
    script = """
    const form = h.$("form[data-confirm]");
    h.click(form.querySelector("button"));
    await h.flush();
    const first = h.$("dialog").getAttribute("aria-describedby");
    h.escape(h.$("dialog"));
    await h.flush();
    h.click(form.querySelector("button"));
    await h.flush();
    h.report("ids", [first, h.$("dialog").getAttribute("aria-describedby")]);
    h.report("dialogs", h.$$("dialog").length);
    h.report("duplicateIds", h.$$("[id]").length !== new Set(h.$$("[id]").map((el) => el.id)).size);
    """
    out = run_js(tmp_path, DELETE_FORM, script)["out"]
    assert out["ids"][0] != out["ids"][1]
    assert out["dialogs"] == 1 and out["duplicateIds"] is False


@needs_node
@pytest.mark.parametrize("answer", ["cancel-button", "escape", "backdrop"])
def test_cancelling_sends_nothing_and_closes_the_dialog(tmp_path, answer):
    close = {
        "cancel-button": "h.click(dialog.querySelector('button[value=\"cancel\"]'));",
        "escape": "h.escape(dialog);",
        "backdrop": "h.click(dialog);",  # a click outside the dialog's box lands on the dialog itself
    }[answer]
    script = (
        OPEN_DIALOG
        + close
        + """
    await h.flush();
    h.report("submitted", h.submissions());
    h.report("dialogs", h.$$("dialog").length);
    h.report("confirmedFlag", h.$("form").hasAttribute("data-confirmed"));
    """
    )
    out = run_js(tmp_path, DELETE_FORM, script)["out"]
    assert out["submitted"] == [] and out["dialogs"] == 0 and out["confirmedFlag"] is False


@needs_node
def test_confirming_sends_the_form_once(tmp_path):
    script = (
        OPEN_DIALOG
        + """
    h.click(dialog.querySelector('button[value="ok"]'));
    await h.flush();
    h.report("submitted", h.submissions());
    h.report("dialogs", h.$$("dialog").length);
    """
    )
    out = run_js(tmp_path, DELETE_FORM, script)["out"]
    assert out["submitted"] == ["/app/bulletins/7/delete"]
    assert out["dialogs"] == 0


# Coming back with Back: a page restored from the back/forward cache keeps its
# DOM, so nothing a confirmation left behind may let the next click skip it

CONFIRM_THEN_RESTORE = (
    OPEN_DIALOG
    + """
h.click(dialog.querySelector('button[value="ok"]'));
await h.flush();
h.advance(0);  // the double-submit guard disables the buttons
const form = h.$("form[data-confirm]");
h.report("flagAfterSending", form.hasAttribute("data-confirmed"));
h.pageshow(true);  // Back: the page comes out of the back/forward cache
const button = form.querySelector("button");
h.report("buttonEnabled", !button.disabled);
h.click(button);
await h.flush();
h.report("submitted", h.submissions());
h.report("askedAgain", h.$$("dialog").length === 1);
"""
)


@needs_node
def test_a_restored_page_asks_again_before_sending(tmp_path):
    out = run_js(tmp_path, DELETE_FORM, CONFIRM_THEN_RESTORE)["out"]
    assert out["flagAfterSending"] is False
    assert out["buttonEnabled"] is True
    assert out["submitted"] == ["/app/bulletins/7/delete"]  # only the first, confirmed one
    assert out["askedAgain"] is True


@needs_node
def test_a_restored_page_clears_a_confirmation_left_behind(tmp_path):
    # For example from a submission that was stopped before the next page loaded
    script = """
    const form = h.$("form[data-confirm]");
    form.dataset.confirmed = "yes";
    h.pageshow(false);  // an ordinary load leaves the page alone
    h.report("afterOrdinaryLoad", form.hasAttribute("data-confirmed"));
    h.pageshow(true);
    h.report("afterRestore", form.hasAttribute("data-confirmed"));
    h.click(form.querySelector("button"));
    await h.flush();
    h.report("submitted", h.submissions());
    h.report("askedAgain", h.$$("dialog").length === 1);
    """
    out = run_js(tmp_path, DELETE_FORM, script)["out"]
    assert out["afterOrdinaryLoad"] is True
    assert out["afterRestore"] is False
    assert out["submitted"] == [] and out["askedAgain"] is True


@needs_node
def test_confirming_one_form_never_skips_the_question_on_another(tmp_path):
    script = (
        OPEN_DIALOG
        + """
    h.click(dialog.querySelector('button[value="ok"]'));
    await h.flush();
    const other = h.$$("form[data-confirm]")[1];
    h.click(other.querySelector("button"));
    await h.flush();
    h.report("submitted", h.submissions());
    h.report("askedAgain", h.$$("dialog").length === 1);
    """
    )
    out = run_js(tmp_path, DELETE_FORM + APPROVE_FORM, script)["out"]
    assert out["submitted"] == ["/app/bulletins/7/delete"]
    assert out["askedAgain"] is True
