document.addEventListener("DOMContentLoaded", () => {
  const form = document.querySelector(".document-text-form");
  if (!form) return;

  let dirty = false;
  form.addEventListener("input", () => { dirty = true; });
  form.addEventListener("submit", () => { dirty = false; });
  window.addEventListener("beforeunload", (event) => {
    if (!dirty) return;
    event.preventDefault();
    event.returnValue = "";
  });
});
