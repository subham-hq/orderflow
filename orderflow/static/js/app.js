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

  // Toggle menus: [data-nav-toggle] controls the element named in aria-controls
  document.querySelectorAll("[data-nav-toggle], [data-sidebar-toggle]").forEach(function (toggle) {
    var target = document.getElementById(toggle.getAttribute("aria-controls"));
    if (!target) return;
    toggle.addEventListener("click", function () {
      var open = toggle.getAttribute("aria-expanded") !== "true";
      toggle.setAttribute("aria-expanded", String(open));
      target.classList.toggle("is-open", open);
      document.body.classList.toggle("nav-open", open);
    });
  });

  document.addEventListener("keydown", function (event) {
    if (event.key !== "Escape") return;
    document.querySelectorAll('[aria-expanded="true"][aria-controls]').forEach(function (toggle) {
      toggle.click();
    });
  });

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

  // Copy buttons: <button data-copy="text">
  document.addEventListener("click", function (event) {
    var button = event.target.closest("[data-copy]");
    if (!button || !navigator.clipboard) return;
    navigator.clipboard.writeText(button.dataset.copy).then(function () {
      var original = button.textContent;
      button.textContent = "Copied";
      window.setTimeout(function () { button.textContent = original; }, 1500);
    });
  });

  // Print buttons
  document.addEventListener("click", function (event) {
    if (event.target.closest("[data-print]")) window.print();
  });
})();
