// Theme switch (remembered per browser).
document.getElementById("theme-toggle")?.addEventListener("click", () => {
  const root = document.documentElement;
  const next = root.getAttribute("data-bs-theme") === "dark" ? "light" : "dark";
  root.setAttribute("data-bs-theme", next);
  try { localStorage.setItem("mam-theme", next); } catch (e) {}
});

// Select all rows on the current page.
document.addEventListener("change", (event) => {
  if (event.target.id === "select-page") {
    document.querySelectorAll(".row-select").forEach((box) => { box.checked = event.target.checked; });
  }
});

// Drag and drop upload.
const zone = document.getElementById("dropzone");
if (zone) {
  const input = document.getElementById("upload-input");
  ["dragenter", "dragover"].forEach((t) => zone.addEventListener(t, (e) => { e.preventDefault(); zone.classList.add("dragover"); }));
  ["dragleave", "drop"].forEach((t) => zone.addEventListener(t, (e) => { e.preventDefault(); zone.classList.remove("dragover"); }));
  zone.addEventListener("drop", (e) => {
    input.files = e.dataTransfer.files;
    if (input.files.length) zone.submit();
  });
}

// Per-filter clear buttons: reset one filter to its default and reload the list.
function syncClearButtons() {
  document.querySelectorAll(".clear-filter").forEach((btn) => {
    const field = document.getElementById(btn.dataset.target);
    if (!field) return;
    let isDefault = field.value === btn.dataset.default;
    if (btn.dataset.target === "sort") {
      isDefault = isDefault && !document.getElementById("desc")?.checked;
    }
    btn.disabled = isDefault;  // always visible; dimmed when there is nothing to clear
  });
}
document.addEventListener("click", (event) => {
  const btn = event.target.closest(".clear-filter");
  if (!btn) return;
  const field = document.getElementById(btn.dataset.target);
  field.value = btn.dataset.default;
  if (btn.dataset.target === "sort") {
    const desc = document.getElementById("desc");
    if (desc) desc.checked = false;
  }
  field.dispatchEvent(new Event("change", { bubbles: true }));
  syncClearButtons();
});
document.getElementById("filters")?.addEventListener("input", syncClearButtons);
document.getElementById("filters")?.addEventListener("change", syncClearButtons);

// Edit form (file and review pages): keep the filename and the table fields in step.
// Only values come back from the server and are filled into the existing fields, so the
// form and its buttons are never replaced while you click. Save waits for a running sync.
const editForm = document.getElementById("edit-form");
if (editForm) {
  let pending = null;

  function fill(values) {
    for (const [name, value] of Object.entries(values)) {
      if (name === "languages" || name === "baserow_row_id" || name === "notes") continue;
      const el = editForm.elements.namedItem(name);
      if (!el || el instanceof RadioNodeList) continue;
      if (el.tagName === "SELECT" && value && ![...el.options].some((o) => o.value === value)) {
        el.add(new Option(`${value} (not in Baserow)`, value), 1);
      }
      if (el.value !== (value ?? "")) el.value = value ?? "";
    }
  }

  function show(id, items) {
    const list = document.getElementById(id);
    list.replaceChildren(...items.map((text) => Object.assign(document.createElement("li"), { textContent: text })));
  }

  async function sync(changed) {
    const data = new FormData(editForm);
    data.set("changed", changed);
    const response = await fetch(editForm.dataset.sync, { method: "POST", body: data });
    if (!response.ok) return;
    const out = await response.json();
    fill(out.values);
    show("edit-problems", out.problems);
    show("edit-hints", out.hints);
    const warning = document.getElementById("place-warning");
    if (warning) {
      warning.textContent = out.place_warning || "";
      warning.hidden = !out.place_warning;
      document.getElementById("place")?.classList.toggle("is-invalid", Boolean(out.place_warning));
    }
    const countryWarning = document.getElementById("country-warning");
    if (countryWarning) {
      countryWarning.textContent = out.country_warning || "";
      countryWarning.hidden = !out.country_warning;
    }
    editForm.querySelectorAll(".edit-save").forEach((b) => { b.disabled = out.problems.length > 0; });
  }

  editForm.addEventListener("change", (event) => {
    const name = event.target.name;
    if (!["filename", "when", "place", "cc", "title", "category", "tag"].includes(name)) return;
    pending = sync(name).finally(() => { pending = null; });
  });

  editForm.addEventListener("submit", (event) => {
    if (!pending) return; // nothing to wait for: submit right away
    // A field change fired as you clicked Save: wait for its sync, then submit with the
    // updated filename. (Re-submitting must happen after this event, or browsers ignore it.)
    event.preventDefault();
    const submitter = event.submitter;
    pending.then(() => {
      if (submitter && submitter.disabled) return; // the sync found a problem
      editForm.requestSubmit(submitter);
    });
  });
}

// A message about queued work ("Scanning …", "Renamer: 10 file(s) queued") shows a spinner while
// jobs run; when the navbar poll reports no jobs left, it turns into a "Finished" message.
function finishLiveFlash() {
  const flash = document.getElementById("flash-live");
  const poll = document.getElementById("nav-poll");
  if (!flash || !poll || poll.dataset.active !== "0") return;
  flash.classList.replace("alert-info", "alert-success");
  flash.querySelector(".flash-spinner")?.setAttribute("hidden", "");
  flash.querySelectorAll(".flash-done, .flash-hint").forEach((el) => el.removeAttribute("hidden"));
}
document.body.addEventListener("htmx:oobAfterSwap", (event) => {
  if (event.detail.target?.id === "nav-poll") finishLiveFlash();
});

// The message is shown once: a reload or a bookmark does not bring it back.
(() => {
  const url = new URL(window.location.href);
  if (!url.searchParams.has("msg") && !url.searchParams.has("live")) return;
  url.searchParams.delete("msg");
  url.searchParams.delete("live");
  history.replaceState(history.state, "", url.pathname + url.search + url.hash);
})();

// Keep the sticky table header just below the (sticky) navbar, whatever its height.
(function () {
  const nav = document.querySelector("nav.sticky-top");
  if (!nav) return;
  const set = () => document.documentElement.style.setProperty("--nav-h", nav.offsetHeight + "px");
  set();
  window.addEventListener("resize", set);
  if (window.ResizeObserver) new ResizeObserver(set).observe(nav);
})();
