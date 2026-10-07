/*
  Small screen animations approved by the project owner on 2026-10-07
  (Batch 2, Item 3 - the four "recommended" ones):
    1. Button feedback - the clicked submit button shows "Saving..." and the
       form can't be sent twice.
    2. Messages - fade in; green success messages fade out after 5 seconds
       (warnings and errors stay until the page changes).
    3. Loading bar - thin bar at the top while the next page loads.
    4. Bell pulse - the red bubble pulses twice when the count goes up
       (done in base.html with the notification count).
  Plain JavaScript, no library. With "Reduce motion" turned on in the
  device settings, the CSS switches the movement off (app.css).
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

  // 2: success messages fade away after 5 seconds
  document.addEventListener("DOMContentLoaded", function () {
    document.querySelectorAll(".app-main .msg.success").forEach(function (m) {
      setTimeout(function () {
        m.classList.add("is-leaving");
        setTimeout(function () { m.remove(); }, 400);
      }, 5000);
    });
  });
})();
