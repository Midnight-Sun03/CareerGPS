function csrfToken() {
  const match = document.cookie.match(/csrftoken=([^;]+)/);
  return match ? match[1] : "";
}

document.addEventListener("DOMContentLoaded", () => {
  const preview = document.getElementById("resumePreview");
  const form = document.getElementById("layoutForm");
  const status = document.getElementById("layoutStatus");
  const density = document.getElementById("densityControl");
  const options = Array.from(document.querySelectorAll("[data-template-option]"));
  const toggles = Array.from(document.querySelectorAll(".section-toggle"));
  const hiddenInputs = document.getElementById("hiddenInputs");

  function selectedTemplate() {
    return form.querySelector('input[name="template_key"]:checked')?.value || "azurill";
  }

  function applyTemplate() {
    const key = selectedTemplate();
    ["azurill", "bronzor", "chikorita", "ditto"].forEach((name) => preview.classList.remove(`rr-template-${name}`));
    preview.classList.add(`rr-template-${key}`);
    options.forEach((option) => option.classList.toggle("active", option.dataset.templateOption === key));
  }

  function applyDensity() {
    preview.classList.remove("density-comfortable", "density-compact");
    preview.classList.add(`density-${density.value}`);
  }

  function applySections() {
    toggles.forEach((toggle) => {
      const section = preview.querySelector(`[data-section="${toggle.value}"]`);
      if (section) section.hidden = !toggle.checked;
    });
  }

  options.forEach((option) => {
    option.addEventListener("click", () => {
      const radio = option.querySelector('input[type="radio"]');
      radio.checked = true;
      applyTemplate();
    });
  });
  density.addEventListener("change", applyDensity);
  toggles.forEach((toggle) => toggle.addEventListener("change", applySections));

  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    hiddenInputs.innerHTML = "";
    toggles.filter((toggle) => !toggle.checked).forEach((toggle) => {
      const input = document.createElement("input");
      input.type = "hidden";
      input.name = "hidden_sections";
      input.value = toggle.value;
      hiddenInputs.appendChild(input);
    });

    status.className = "layout-status";
    status.textContent = "Saving layout and rebuilding documents…";
    const submit = form.querySelector('button[type="submit"]');
    submit.disabled = true;
    try {
      const response = await fetch(form.action, {
        method: "POST",
        headers: { "X-CSRFToken": csrfToken() },
        body: new FormData(form),
      });
      const data = await response.json();
      if (!response.ok) throw new Error(data.error || "Could not save the layout.");
      status.classList.add("success");
      status.textContent = "Layout saved. Documents rebuilt without another AI call.";
      const pdfLink = document.getElementById("downloadPdfBtn");
      if (pdfLink && data.pdf_url) pdfLink.href = `${data.pdf_url}?v=${Date.now()}`;
    } catch (error) {
      status.classList.add("error");
      status.textContent = error.message;
    } finally {
      submit.disabled = false;
    }
  });

  applyTemplate();
  applyDensity();
  applySections();
});
