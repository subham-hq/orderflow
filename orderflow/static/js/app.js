// OrderFlow front-end behaviour. Pages work without JavaScript; this adds
// confirmations, menus and small conveniences on top.
(function () {
  "use strict";

  document.documentElement.classList.remove("no-js");
  document.documentElement.classList.add("js");

  // Confirm before submitting forms marked with data-confirm, using a native <dialog>
  function confirmDialog(message, label, variant) {
    return new Promise(function (resolve) {
      var dialog = document.createElement("dialog");
      dialog.className = "dialog";
      dialog.setAttribute("aria-label", "Please confirm");
      dialog.innerHTML =
        '<form method="dialog" class="dialog__body">' +
        '<p class="dialog__text"></p>' +
        '<div class="dialog__actions">' +
        '<button value="cancel" class="btn btn--ghost">Cancel</button>' +
        '<button value="ok" class="btn"></button>' +
        "</div></form>";
      dialog.querySelector(".dialog__text").textContent = message;
      var ok = dialog.querySelector('button[value="ok"]');
      ok.textContent = label || "Confirm";
      ok.classList.add("btn--" + (variant === "danger" ? "danger" : "primary"));
      document.body.appendChild(dialog);
      dialog.addEventListener("close", function () {
        resolve(dialog.returnValue === "ok");
        dialog.remove();
      });
      // A click on the backdrop (outside the dialog's box) cancels
      dialog.addEventListener("click", function (event) {
        if (event.target === dialog) dialog.close("cancel");
      });
      dialog.showModal();
      ok.focus();
    });
  }

  document.addEventListener("submit", function (event) {
    var form = event.target;
    if (!form.matches("form[data-confirm]") || form.dataset.confirmed === "yes") return;
    if (typeof HTMLDialogElement !== "function") return; // old browsers: submit without asking
    event.preventDefault();
    var submitter = event.submitter;
    confirmDialog(form.dataset.confirm, form.dataset.confirmLabel, form.dataset.confirmVariant).then(function (yes) {
      if (!yes) return;
      form.dataset.confirmed = "yes";
      if (form.requestSubmit) form.requestSubmit(submitter || undefined);
      else form.submit();
    });
  });

  // Stop double submits: disable submit buttons once a form is on its way
  document.addEventListener("submit", function (event) {
    var form = event.target;
    if (event.defaultPrevented || form.hasAttribute("data-allow-resubmit")) return;
    window.setTimeout(function () {
      form.querySelectorAll('button[type="submit"], button:not([type])').forEach(function (button) {
        button.disabled = true;
        button.setAttribute("aria-busy", "true");
      });
    }, 0);
  });

  // A page restored from the back/forward cache keeps disabled buttons; re-enable them
  window.addEventListener("pageshow", function (event) {
    if (!event.persisted) return;
    document.querySelectorAll('button[aria-busy="true"]').forEach(function (button) {
      button.disabled = false;
      button.removeAttribute("aria-busy");
    });
  });

  // Menus: [data-nav-toggle] and [data-sidebar-toggle] open and close the
  // element named in aria-controls. Every toggle for the same element stays in sync.
  var toggleSelector = "[data-nav-toggle][aria-controls], [data-sidebar-toggle][aria-controls]";

  function togglesFor(target) {
    return Array.prototype.filter.call(document.querySelectorAll(toggleSelector), function (toggle) {
      return toggle.getAttribute("aria-controls") === target.id;
    });
  }

  function openMenus() {
    var seen = [];
    document.querySelectorAll(toggleSelector).forEach(function (toggle) {
      var target = document.getElementById(toggle.getAttribute("aria-controls"));
      if (target && target.classList.contains("is-open") && seen.indexOf(target) === -1) seen.push(target);
    });
    return seen;
  }

  function setOpen(target, open) {
    target.classList.toggle("is-open", open);
    togglesFor(target).forEach(function (toggle) {
      toggle.setAttribute("aria-expanded", String(open));
    });
    document.body.classList.toggle("nav-open", openMenus().length > 0);
    // While the sidebar drawer is open, the page behind it can't be focused or clicked
    if (target.hasAttribute("data-sidebar")) {
      var page = document.querySelector(".shell__main");
      if (page) page.inert = open;
    }
  }

  // Close a menu; if focus was inside it, hand focus back to its visible toggle
  function closeMenu(menu) {
    var hadFocus = menu.contains(document.activeElement);
    setOpen(menu, false);
    if (!hadFocus) return;
    var toggle = togglesFor(menu).filter(function (button) {
      return button.offsetParent !== null && !menu.contains(button);
    })[0];
    if (toggle) toggle.focus();
  }

  document.addEventListener("click", function (event) {
    var toggle = event.target.closest(toggleSelector);
    if (toggle) {
      var target = document.getElementById(toggle.getAttribute("aria-controls"));
      if (!target) return;
      if (target.classList.contains("is-open")) {
        closeMenu(target);
        return;
      }
      setOpen(target, true);
      if (toggle.hasAttribute("data-sidebar-toggle")) {
        var first = target.querySelector("a[href], button:not([disabled])");
        if (first) first.focus();
      }
      return;
    }
    openMenus().forEach(function (menu) {
      var link = event.target.closest("a[href]");
      if (link && menu.contains(link)) setOpen(menu, false); // following a link, e.g. an in-page anchor
      else if (!menu.contains(event.target)) closeMenu(menu); // a click outside the menu
    });
  });

  document.addEventListener("keydown", function (event) {
    if (event.key === "Escape") openMenus().forEach(closeMenu);
  });

  // Growing past the phone layout closes any open drawer or menu
  if (window.matchMedia) {
    var wide = window.matchMedia("(min-width: 960px)");
    var onWide = function () {
      if (wide.matches) openMenus().forEach(function (menu) { setOpen(menu, false); });
    };
    if (wide.addEventListener) wide.addEventListener("change", onWide);
    else if (wide.addListener) wide.addListener(onWide);
  }

  // Dismissible alerts
  document.addEventListener("click", function (event) {
    var close = event.target.closest("[data-dismiss]");
    if (!close) return;
    var alert = close.closest("[data-flash], .alert");
    if (alert) alert.remove();
  });

  // Quantity steppers: <div data-stepper><button data-step="-1"> <input> <button data-step="1"></div>
  document.addEventListener("click", function (event) {
    var button = event.target.closest("[data-step]");
    if (!button) return;
    var stepper = button.closest("[data-stepper]");
    var input = stepper && stepper.querySelector("input");
    if (!input) return;
    event.preventDefault();
    var value = parseInt(input.value || "0", 10) || 0;
    var min = input.min !== "" ? parseInt(input.min, 10) : 0;
    var max = input.max !== "" ? parseInt(input.max, 10) : Infinity;
    value = Math.min(max, Math.max(min, value + parseInt(button.dataset.step, 10)));
    input.value = String(value);
    input.dispatchEvent(new Event("input", { bubbles: true }));
    input.dispatchEvent(new Event("change", { bubbles: true }));
  });

  // Submit a form when a control marked data-autosubmit changes (filters)
  document.addEventListener("change", function (event) {
    var control = event.target.closest("[data-autosubmit]");
    if (control && control.form) control.form.requestSubmit ? control.form.requestSubmit() : control.form.submit();
  });

  // Copy buttons: <button data-copy="text">. The label reads "Copied" for a moment.
  document.addEventListener("click", function (event) {
    var button = event.target.closest("[data-copy]");
    if (!button || !navigator.clipboard) return;
    navigator.clipboard.writeText(button.dataset.copy).then(function () {
      if (button.dataset.copying) return;
      button.dataset.copying = "yes";
      var original = button.innerHTML;
      button.textContent = "Copied";
      window.setTimeout(function () {
        button.innerHTML = original;
        delete button.dataset.copying;
      }, 1500);
    });
  });

  // Print buttons
  document.addEventListener("click", function (event) {
    if (event.target.closest("[data-print]")) window.print();
  });

  // Password inputs get a Show / Hide button
  function revealPassword(input, button, show) {
    input.type = show ? "text" : "password";
    button.textContent = show ? "Hide" : "Show";
    button.setAttribute("aria-pressed", String(show));
    button.setAttribute("aria-label", show ? "Hide password" : "Show password");
  }

  document.querySelectorAll('.field input[type="password"]').forEach(function (input) {
    if (input.closest(".password")) return;
    var wrap = document.createElement("div");
    wrap.className = "password";
    input.parentNode.insertBefore(wrap, input);
    wrap.appendChild(input);
    var button = document.createElement("button");
    button.type = "button";
    button.className = "password__toggle";
    if (input.id) button.setAttribute("aria-controls", input.id);
    wrap.appendChild(button);
    revealPassword(input, button, false);
    button.addEventListener("click", function () {
      revealPassword(input, button, input.type === "password");
    });
  });

  // When a page comes back with form errors, move focus to the first one so
  // keyboard and screen reader users land on it (and it scrolls into view)
  var firstInvalid = document.querySelector('[aria-invalid="true"]');
  if (firstInvalid) {
    firstInvalid.focus({ preventScroll: true });
    firstInvalid.scrollIntoView({ block: "center" });
  }

  // Hide revealed passwords again when their form is sent, so browsers don't
  // store them as ordinary text
  document.addEventListener("submit", function (event) {
    event.target.querySelectorAll(".password").forEach(function (wrap) {
      var input = wrap.querySelector("input");
      var button = wrap.querySelector(".password__toggle");
      if (input && button) revealPassword(input, button, false);
    });
  });
})();
