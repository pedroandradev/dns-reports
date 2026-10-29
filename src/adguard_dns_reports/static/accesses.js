/* Refresh observations without discarding an in-progress filter edit. */
(() => {
  const page = document.getElementById("access-page");
  const form = document.getElementById("access-filters");
  if (!page || !form || page.dataset.refresh !== "1") return;
  let editing = false;
  form.addEventListener("input", () => { editing = true; });
  form.addEventListener("change", () => { editing = true; });
  window.setInterval(() => {
    if (!editing && document.visibilityState === "visible"
        && !page.querySelector("details[open]") && !form.contains(document.activeElement)) {
      window.location.reload();
    }
  }, 30000);
})();
