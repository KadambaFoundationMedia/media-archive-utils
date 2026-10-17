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
