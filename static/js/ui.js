/*
  BDH HRIS - the ONE shared file for screen animations and small
  components (Batch 2 Item 3 and Batch 3, approved by the project owner
  2026-10-07). Plain JavaScript + the existing Alpine.js; no other library
  and nothing loaded from the internet. Styles: static/css/animations.css.

  What is here (each part is independent):
    - Busy buttons and the top loading bar (Batch 2)
    - BDH.toast()        toast messages, top right (Batch 3 Item 11)
    - BDH.confirm        confirmation dialog for destructive actions (Item 6)
    - BDH.snackbar       "... Undo" bar at the bottom (Item 5)
    - BDH.working()      honest "Working..." progress panel (Item 9)
    - sidebar indicator, logout, accordions, uploads (Items 1, 2, 10, 8)

  Nothing here decides anything: every action is still checked and done by
  the server. With "Reduce motion" on, the CSS removes the movement.
*/
(function () {
  "use strict";

  var bar = null;
  function showBar() {
    if (!bar) {
      bar = document.createElement("div");
      bar.className = "page-loading-bar";
      bar.setAttribute("aria-hidden", "true");
      document.body.appendChild(bar);
    }
    bar.classList.add("is-active");
  }
  function hideBar() { if (bar) bar.classList.remove("is-active"); }

  // 1 + 3: form submit (POST forms only get the "Saving..." button)
  document.addEventListener("submit", function (e) {
    var form = e.target;
    if (e.defaultPrevented || form.target === "_blank" || form.hasAttribute("data-no-busy")) return;
    showBar();
    if ((form.method || "get").toLowerCase() !== "post") return;
    var btn = e.submitter || form.querySelector("button[type=submit], input[type=submit]");
    // (the login button has its own spinner)
    if (btn && btn.tagName === "BUTTON" && !btn.closest(".logout-form") && !btn.querySelector(".spinner")) {
      btn.classList.add("is-busy");
    }
    // Disable after the browser has collected the form values (a disabled
    // button would otherwise drop its name/value, e.g. action=approve).
    setTimeout(function () {
      form.querySelectorAll("button[type=submit], input[type=submit], button:not([type])").forEach(function (b) {
        if (!b.disabled) { b.disabled = true; b.setAttribute("data-busy-disabled", ""); }
      });
    }, 0);
    // A file download doesn't leave the page - give the buttons back.
    setTimeout(function () { restore(form); }, 8000);
  });

  function restore(scope) {
    (scope || document).querySelectorAll("button.is-busy").forEach(function (b) { b.classList.remove("is-busy"); });
    (scope || document).querySelectorAll("[data-busy-disabled]").forEach(function (b) {
      b.disabled = false;
      b.removeAttribute("data-busy-disabled");
    });
    hideBar();
  }

  // 3: ordinary links to another page of this site
  document.addEventListener("click", function (e) {
    var a = e.target.closest && e.target.closest("a[href]");
    if (!a || e.defaultPrevented || e.button !== 0 || e.metaKey || e.ctrlKey || e.shiftKey || e.altKey) return;
    if (a.target === "_blank" || a.hasAttribute("download") || a.origin !== location.origin) return;
    var href = a.getAttribute("href");
    if (!href || href.charAt(0) === "#" || /\/print\/|export|download/.test(a.pathname)) return;
    showBar();
  });

  // Back/Forward restores a page from memory: undo the busy state.
  window.addEventListener("pageshow", function (e) { if (e.persisted) restore(); else hideBar(); });

  var BDH = (window.BDH = window.BDH || {});

  // ---------------------------------------------------------------------
  // Toasts (Item 11). One stack, top right, below the header (so the bell
  // and the header buttons are never covered). Success/info fade out after
  // ~4.5 s, warnings after ~7 s; errors stay until closed.
  // Django messages are turned into toasts by base.html (data-toast).
  // ---------------------------------------------------------------------
  var TOAST_MS = { success: 4500, info: 5000, warning: 7000, error: 0 };
  var ICONS = { success: "\u2713", info: "i", warning: "!", error: "\u00d7" };

  function toastStack() {
    var stack = document.querySelector(".toast-stack");
    if (!stack) {
      stack = document.createElement("div");
      stack.className = "toast-stack";
      document.body.appendChild(stack);
    }
    return stack;
  }

  function closeToast(t) {
    if (!t || t.classList.contains("is-leaving")) return;
    t.classList.add("is-leaving");
    setTimeout(function () { t.remove(); }, 250);
  }

  BDH.toast = function (text, type) {
    type = TOAST_MS.hasOwnProperty(type) ? type : "info";
    var t = document.createElement("div");
    t.className = "toast toast-" + type;
    t.setAttribute("role", type === "error" ? "alert" : "status");
    var icon = document.createElement("span");
    icon.className = "toast-icon";
    icon.setAttribute("aria-hidden", "true");
    icon.textContent = ICONS[type];
    var msg = document.createElement("span");
    msg.className = "toast-text";
    msg.textContent = text;
    var x = document.createElement("button");
    x.type = "button";
    x.className = "toast-close";
    x.setAttribute("aria-label", "Close message");
    x.textContent = "\u00d7";
    x.addEventListener("click", function () { closeToast(t); });
    t.appendChild(icon); t.appendChild(msg); t.appendChild(x);
    toastStack().appendChild(t);
    if (TOAST_MS[type]) {
      var timer = setTimeout(function () { closeToast(t); }, TOAST_MS[type]);
      // keep it while the mouse is on it, so it can be read
      t.addEventListener("mouseenter", function () { clearTimeout(timer); });
      t.addEventListener("mouseleave", function () { timer = setTimeout(function () { closeToast(t); }, 2000); });
    }
    return t;
  };

  function djangoTag(tags) {
    if (/error/.test(tags)) return "error";
    if (/warning/.test(tags)) return "warning";
    if (/success/.test(tags)) return "success";
    return "info";
  }

  document.addEventListener("DOMContentLoaded", function () {
    document.querySelectorAll("[data-toast]").forEach(function (el) {
      BDH.toast(el.textContent.trim(), djangoTag(el.getAttribute("data-toast")));
      el.remove();
    });
  });
})();
