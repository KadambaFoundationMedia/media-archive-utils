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
    btn.classList.toggle("d-none", isDefault);
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
