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

  // ---------------------------------------------------------------------
  // Sidebar active indicator (Item 1). Finds the menu link for the current
  // page, tints it, and slides a bar there from where it was on the last
  // page (remembered only for this browser tab). If that memory is not
  // available, the bar simply appears in place - no jump.
  // ---------------------------------------------------------------------
  function currentNavLink(nav) {
    var path = location.pathname, best = null, bestLen = -1;
    nav.querySelectorAll("a[href]").forEach(function (a) {
      var p = a.pathname;
      if (p === "/") return;
      if ((path === p || path.indexOf(p) === 0) && p.length > bestLen) { best = a; bestLen = p.length; }
    });
    return best;
  }

  function placeIndicator(nav, link, animateFrom) {
    var ind = nav.querySelector(".nav-indicator");
    if (!ind) {
      ind = document.createElement("span");
      ind.className = "nav-indicator";
      ind.setAttribute("aria-hidden", "true");
      nav.appendChild(ind);
    }
    var top = link.offsetTop, h = link.offsetHeight;
    if (animateFrom && animateFrom.top !== top) {
      ind.classList.add("no-anim");
      ind.style.top = animateFrom.top + "px";
      ind.style.height = animateFrom.h + "px";
      void ind.offsetWidth;  // apply the start position first
      ind.classList.remove("no-anim");
    } else {
      ind.classList.add("no-anim");
    }
    ind.style.top = top + "px";
    ind.style.height = h + "px";
    if (!animateFrom) requestAnimationFrame(function () { ind.classList.remove("no-anim"); });
    return { top: top, h: h };
  }

  document.addEventListener("DOMContentLoaded", function () {
    var nav = document.querySelector(".app-sidebar nav");
    if (!nav) return;
    var link = currentNavLink(nav);
    if (!link) return;
    link.classList.add("is-current");
    link.setAttribute("aria-current", "page");
    var prev = null;
    try { prev = JSON.parse(sessionStorage.getItem("bdh-nav") || "null"); } catch (e) { prev = null; }
    var pos = placeIndicator(nav, link, prev);
    try { sessionStorage.setItem("bdh-nav", JSON.stringify(pos)); } catch (e) { /* not available: no slide */ }
  });

  // ---------------------------------------------------------------------
  // Logout (Item 2). The sign-out request is sent at once (the server ends
  // the session immediately); the screen fades with "Signing out..." for
  // at least ~0.6 s and then opens the login page. If the request fails,
  // the overlay is removed and an error is shown - never a blank screen.
  // ---------------------------------------------------------------------
  var LOGOUT_MIN_MS = 600;

  function signOutOverlay() {
    var o = document.createElement("div");
    o.className = "signout-overlay";
    o.setAttribute("role", "status");
    o.innerHTML = '<div class="signout-box"><span class="signout-spinner" aria-hidden="true"></span>Signing out…</div>';
    document.body.appendChild(o);
    requestAnimationFrame(function () { o.classList.add("is-visible"); });
    return o;
  }

  document.addEventListener("submit", function (e) {
    var form = e.target;
    if (!form.classList || !form.classList.contains("logout-form") || !window.fetch) return;
    e.preventDefault();
    e.stopImmediatePropagation();
    var started = Date.now();
    var overlay = signOutOverlay();
    fetch(form.action, { method: "POST", body: new FormData(form), credentials: "same-origin" })
      .then(function (r) {
        var landed = new URL(r.url, location.href).pathname;
        if (!r.ok) throw new Error("status " + r.status);
        if (landed.indexOf("/hris/login") !== 0) {
          // e.g. the page had expired: let the browser do a normal sign-out
          overlay.remove();
          form.submit();
          return;
        }
        setTimeout(function () { location.replace(r.url); }, Math.max(0, LOGOUT_MIN_MS - (Date.now() - started)));
      })
      .catch(function () {
        overlay.remove();
        BDH.toast("Could not sign out — the server did not answer. Check the network connection and try again.", "error");
      });
  }, true);

  // ---------------------------------------------------------------------
  // Confirmation dialog (Item 6). Any form with data-confirm="message"
  // asks first. Optional attributes:
  //   data-confirm-title="Archive this record?"
  //   data-confirm-ok="Archive"            (the red button's label)
  //   data-confirm-reason="Reason"         (adds a required reason box; its
  //                                          text is sent as field "reason")
  // Cancel has the focus, so Enter/Esc never does the destructive action by
  // accident. The server still checks everything.
  // ---------------------------------------------------------------------
  var dialog = null, pending = null;

  function buildDialog() {
    dialog = document.createElement("dialog");
    dialog.className = "confirm-dialog";
    dialog.setAttribute("aria-labelledby", "confirm-title");
    dialog.setAttribute("aria-describedby", "confirm-text");
    dialog.innerHTML =
      '<div class="confirm-icon" aria-hidden="true">!</div>' +
      '<h2 id="confirm-title"></h2>' +
      '<p id="confirm-text"></p>' +
      '<label class="confirm-reason"><span></span><textarea rows="2" maxlength="255"></textarea></label>' +
      '<div class="confirm-actions">' +
      '<button type="button" class="btn-secondary confirm-cancel">Cancel</button>' +
      '<button type="button" class="btn-danger confirm-ok"></button></div>';
    document.body.appendChild(dialog);
    var reason = dialog.querySelector("textarea"), ok = dialog.querySelector(".confirm-ok");
    reason.addEventListener("input", function () { ok.disabled = !reason.value.trim(); });
    dialog.querySelector(".confirm-cancel").addEventListener("click", function () { dialog.close(); });
    dialog.addEventListener("close", function () { pending = null; });
    ok.addEventListener("click", function () {
      if (!pending) return;
      var form = pending.form, submitter = pending.submitter;
      if (form.hasAttribute("data-confirm-reason")) {
        var field = form.querySelector("input[name=reason][data-from-dialog]");
        if (!field) {
          field = document.createElement("input");
          field.type = "hidden"; field.name = "reason"; field.setAttribute("data-from-dialog", "");
          form.appendChild(field);
        }
        field.value = reason.value.trim();
      }
      form.setAttribute("data-confirmed", "");
      dialog.close();
      if (form.requestSubmit) form.requestSubmit(submitter || undefined); else form.submit();
    });
  }

  BDH.confirm = function (form, submitter) {
    if (!dialog) buildDialog();
    pending = { form: form, submitter: submitter };
    dialog.querySelector("#confirm-title").textContent = form.getAttribute("data-confirm-title") || "Are you sure?";
    dialog.querySelector("#confirm-text").textContent = form.getAttribute("data-confirm");
    var ok = dialog.querySelector(".confirm-ok"), box = dialog.querySelector(".confirm-reason");
    var reason = box.querySelector("textarea");
    ok.textContent = form.getAttribute("data-confirm-ok") || "Confirm";
    var needsReason = form.hasAttribute("data-confirm-reason");
    box.hidden = !needsReason;
    box.querySelector("span").textContent = form.getAttribute("data-confirm-reason") || "Reason";
    reason.value = ""; reason.required = needsReason;
    ok.disabled = needsReason;
    var icon = dialog.querySelector(".confirm-icon");
    icon.classList.remove("is-shaking"); void icon.offsetWidth; icon.classList.add("is-shaking");  // once
    dialog.showModal();
    dialog.querySelector(needsReason ? "textarea" : ".confirm-cancel").focus();
  };

  document.addEventListener("submit", function (e) {
    var form = e.target;
    if (!form.hasAttribute || !form.hasAttribute("data-confirm")) return;
    if (form.hasAttribute("data-confirmed")) { form.removeAttribute("data-confirmed"); return; }
    if (!window.HTMLDialogElement) return;  // very old browser: plain submit, server still checks
    e.preventDefault();
    e.stopImmediatePropagation();
    BDH.confirm(form, e.submitter);
  }, true);

  // ---------------------------------------------------------------------
  // Undo snackbar (Item 5). Rendered by the page right after archiving;
  // shows a shrinking bar for the remaining seconds, then disappears.
  // Leaving the page or letting it run out keeps the archive.
  // ---------------------------------------------------------------------
  document.addEventListener("DOMContentLoaded", function () {
    document.querySelectorAll("[data-snackbar]").forEach(function (bar) {
      var secs = Math.max(1, parseInt(bar.getAttribute("data-snackbar"), 10) || 8);
      var fill = bar.querySelector(".snackbar-timer span");
      if (fill) {
        fill.style.transitionDuration = secs + "s";
        requestAnimationFrame(function () { requestAnimationFrame(function () { fill.style.transform = "scaleX(0)"; }); });
      }
      setTimeout(function () {
        bar.classList.add("is-leaving");
        setTimeout(function () { bar.remove(); }, 300);
      }, secs * 1000);
    });
  });

  // ---------------------------------------------------------------------
  // Live results (Item 7). A GET filter form with data-live-results="#id"
  // fetches the same page in the background when the user searches or
  // changes a filter, then swaps in just the results box with a fade. The
  // old results stay on screen until the new ones arrive (no blank flash).
  // Without scripts it is an ordinary search form.
  // ---------------------------------------------------------------------
  function liveResults(form) {
    var sel = form.getAttribute("data-live-results"), seq = 0, timer = null;
    function run() {
      var url = form.action.split("?")[0] + "?" + new URLSearchParams(new FormData(form)).toString();
      var mine = ++seq;
      fetch(url, { credentials: "same-origin", headers: { "X-Requested-With": "fetch" } })
        .then(function (r) { return r.ok ? r.text() : Promise.reject(r.status); })
        .then(function (html) {
          if (mine !== seq) return;  // an older search finished late: ignore it
          var fresh = new DOMParser().parseFromString(html, "text/html").querySelector(sel);
          var old = document.querySelector(sel);
          if (!fresh || !old) { location.href = url; return; }
          fresh.classList.add("results-fade");
          old.replaceWith(fresh);
          history.replaceState(null, "", url);
        })
        .catch(function () { location.href = url; });
    }
    form.addEventListener("submit", function (e) { e.preventDefault(); e.stopImmediatePropagation(); clearTimeout(timer); run(); }, true);
    form.addEventListener("change", function () { clearTimeout(timer); run(); });
    form.addEventListener("input", function (e) {
      if (e.target.type !== "text" && e.target.type !== "search") return;
      clearTimeout(timer); timer = setTimeout(run, 300);
    });
  }
  document.addEventListener("DOMContentLoaded", function () {
    if (!window.fetch || !window.DOMParser) return;
    document.querySelectorAll("form[data-live-results]").forEach(liveResults);
  });

  // ---------------------------------------------------------------------
  // File upload (Item 8). Enhances forms marked data-upload: shows the
  // chosen file (icon, name, size, Remove), checks type and size at once
  // with the SAME limits as the server (which still checks everything),
  // sends the file with a REAL progress bar, then a green check - or a red
  // message with the reason and "Try again".
  // ---------------------------------------------------------------------
  function fmtSize(n) {
    return n >= 1048576 ? (n / 1048576).toFixed(1) + " MB" : Math.max(1, Math.round(n / 1024)) + " KB";
  }

  function uploadForm(form) {
    var input = form.querySelector("input[type=file]");
    if (!input || !window.XMLHttpRequest || !window.FormData) return;
    var maxBytes = parseInt(form.getAttribute("data-max-bytes"), 10) || 0;
    var allowed = (form.getAttribute("data-allowed") || "").split(",");
    var box = document.createElement("div");
    box.className = "upload-box";
    box.setAttribute("aria-live", "polite");
    box.hidden = true;
    input.insertAdjacentElement("afterend", box);

    function show(state, html) {
      box.hidden = false;
      box.className = "upload-box is-" + state;
      box.innerHTML = html;
    }
    function esc(t) { var d = document.createElement("div"); d.textContent = t; return d.innerHTML; }
    function fileLine(f, extra) {
      return '<span class="upload-icon" aria-hidden="true"></span><span class="upload-name">' + esc(f.name) +
             ' <small>' + fmtSize(f.size) + '</small></span>' + (extra || "");
    }
    function problem(f, reason) {
      show("error", (f ? fileLine(f) : "") + '<p class="upload-msg">' + esc(reason) + '</p>' +
           '<button type="button" class="btn-secondary btn-sm upload-retry">Try again</button>');
      box.querySelector(".upload-retry").addEventListener("click", function () { reset(); input.click(); });
    }
    function reset() { input.value = ""; box.hidden = true; box.innerHTML = ""; }
    function check(f) {
      var ext = "." + (f.name.split(".").pop() || "").toLowerCase();
      if (allowed.length && allowed.indexOf(ext) === -1) return "Wrong file type — only PDF, JPG or PNG.";
      if (maxBytes && f.size > maxBytes) return "File is too large — maximum is " + fmtSize(maxBytes) + ".";
      return "";
    }

    input.addEventListener("change", function () {
      var f = input.files[0];
      if (!f) { reset(); return; }
      var why = check(f);
      if (why) { problem(f, why); input.value = ""; return; }
      show("ready", fileLine(f, '<button type="button" class="upload-remove" aria-label="Remove ' + esc(f.name) + '">Remove</button>'));
      box.querySelector(".upload-remove").addEventListener("click", reset);
    });

    form.addEventListener("submit", function (e) {
      var f = input.files[0];
      if (!f) return;  // let the server say what's missing
      e.preventDefault();
      e.stopImmediatePropagation();
      var why = check(f);
      if (why) { problem(f, why); return; }
      var buttons = form.querySelectorAll("button[type=submit]");
      buttons.forEach(function (b) { b.disabled = true; });
      show("uploading", fileLine(f) + '<div class="upload-bar" role="progressbar" aria-valuemin="0" aria-valuemax="100"><span></span></div><p class="upload-msg">Uploading… 0%</p>');
      var bar = box.querySelector(".upload-bar"), fill = bar.querySelector("span"), msg = box.querySelector(".upload-msg");
      var xhr = new XMLHttpRequest();
      xhr.open("POST", form.action || location.href);
      xhr.upload.onprogress = function (ev) {
        if (!ev.lengthComputable) { msg.textContent = "Uploading…"; return; }
        var pct = Math.round(ev.loaded / ev.total * 100);
        fill.style.width = pct + "%";
        bar.setAttribute("aria-valuenow", pct);
        msg.textContent = "Uploading… " + pct + "%";
      };
      xhr.onload = function () {
        buttons.forEach(function (b) { b.disabled = false; });
        var target = xhr.responseURL || "";
        var stayed = target.split("?")[0] === (form.action || location.href).split("?")[0];
        if (xhr.status >= 200 && xhr.status < 300 && !stayed) {
          show("done", fileLine(f, '<span class="upload-check" aria-hidden="true">✓</span>') + '<p class="upload-msg">Uploaded.</p>');
          setTimeout(function () { location.href = target; }, 700);
          return;
        }
        // The server refused it: show its reason.
        var reason = "The upload was not accepted.";
        try {
          var doc = new DOMParser().parseFromString(xhr.responseText, "text/html");
          var err = doc.querySelector(".errorlist");
          if (err) reason = err.textContent.trim();
          else if (xhr.status === 403) reason = "You are not allowed to upload here, or the page expired. Reload and try again.";
        } catch (ignore) { /* keep the general reason */ }
        problem(f, reason);
      };
      xhr.onerror = function () {
        buttons.forEach(function (b) { b.disabled = false; });
        problem(f, "Network error — the file did not reach the server.");
      };
      xhr.send(new FormData(form));
    }, true);
  }
  document.addEventListener("DOMContentLoaded", function () {
    document.querySelectorAll("form[data-upload]").forEach(uploadForm);
  });

  // ---------------------------------------------------------------------
  // Progress (Item 9). The imports and reports finish in one step on the
  // server and can't report a real percentage, so we show an honest
  // "Working..." bar (no made-up numbers, owner decision 2026-10-07).
  //   form[data-working="Text..."]  - shown while the form is processed;
  //                                   the next page shows the summary.
  //   a[data-download="Text..."]    - fetches the file, shows Working...,
  //                                   then saves it; on a problem shows the
  //                                   server's message instead of a stuck bar.
  // ---------------------------------------------------------------------
  BDH.working = function (text, anchor) {
    var panel = document.createElement("div");
    panel.className = "working-panel";
    panel.setAttribute("role", "status");
    panel.innerHTML = '<span class="working-text"></span><span class="working-bar" aria-hidden="true"><span></span></span>';
    panel.querySelector(".working-text").textContent = text + " Working…";
    if (anchor) anchor.insertAdjacentElement("afterend", panel); else document.body.appendChild(panel);
    return {
      done: function (msg) { panel.classList.add("is-done"); panel.querySelector(".working-text").textContent = msg || "100% Complete"; setTimeout(function () { panel.remove(); }, 1500); },
      fail: function () { panel.remove(); },
    };
  };

  document.addEventListener("submit", function (e) {
    var form = e.target;
    if (e.defaultPrevented || !form.hasAttribute || !form.hasAttribute("data-working")) return;
    if (form.querySelector(".working-panel")) return;
    var w = BDH.working(form.getAttribute("data-working"), form.lastElementChild || form);
    window.addEventListener("pageshow", function () { w.fail(); }, { once: true });  // came back with Back
  });

  function filenameFrom(r, url) {
    var cd = r.headers.get("content-disposition") || "", m = /filename\*?=(?:UTF-8'')?"?([^";]+)"?/i.exec(cd);
    return m ? decodeURIComponent(m[1]) : url.split("/").pop().split("?")[0] || "download";
  }

  document.addEventListener("click", function (e) {
    var a = e.target.closest && e.target.closest("a[data-download]");
    if (!a || e.button !== 0 || e.metaKey || e.ctrlKey || !window.fetch) return;
    e.preventDefault();
    e.stopImmediatePropagation();
    if (a.getAttribute("aria-busy") === "true") return;
    a.setAttribute("aria-busy", "true");
    var w = BDH.working(a.getAttribute("data-download"), a);
    fetch(a.href, { credentials: "same-origin" })
      .then(function (r) {
        var type = r.headers.get("content-type") || "";
        if (!r.ok || type.indexOf("text/html") === 0) {
          // The server sent a page instead of the file: show its message.
          return r.text().then(function (html) {
            var doc = new DOMParser().parseFromString(html, "text/html");
            var msg = doc.querySelector(".msg.error, .msg.warning, .msg");
            throw new Error(msg ? msg.textContent.trim() : "The file could not be prepared.");
          });
        }
        return r.blob().then(function (blob) {
          var link = document.createElement("a");
          link.href = URL.createObjectURL(blob);
          link.download = filenameFrom(r, a.href);
          document.body.appendChild(link); link.click(); link.remove();
          setTimeout(function () { URL.revokeObjectURL(link.href); }, 10000);
          w.done("100% Complete — " + link.download + " saved.");
        });
      })
      .catch(function (err) {
        w.fail();
        BDH.toast(err && err.message && err.message !== "Failed to fetch" ? err.message : "Network error — the file could not be downloaded.", "error");
      })
      .then(function () { a.removeAttribute("aria-busy"); });
  }, true);
})();
