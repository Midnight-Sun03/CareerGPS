function getCsrfToken() {
  const match = document.cookie.match(/csrftoken=([^;]+)/);
  return match ? match[1] : "";
}

document.addEventListener("DOMContentLoaded", () => {
  // ── Paste / Upload tabs ──
  const tabs = document.querySelectorAll(".cv-tab");
  const panels = document.querySelectorAll(".cv-tab-panel");

  tabs.forEach((tab) => {
    tab.addEventListener("click", () => {
      tabs.forEach((t) => t.classList.remove("active-tab"));
      tab.classList.add("active-tab");
      const target = tab.dataset.tab;
      panels.forEach((p) => {
        p.hidden = p.dataset.panel !== target;
      });
    });
  });

  // ── Character counter ──
  const pasteText = document.getElementById("cvPasteText");
  const charCount = document.getElementById("cvCharCount");
  if (pasteText && charCount) {
    pasteText.addEventListener("input", () => {
      charCount.textContent = pasteText.value.length;
    });
  }

  // ── File upload name display ──
  const fileInput = document.getElementById("cvFileUpload");
  const fileNameEl = document.getElementById("cvFileUploadName");
  if (fileInput && fileNameEl) {
    fileInput.addEventListener("change", function () {
      fileNameEl.textContent = this.files.length ? "📄 " + this.files[0].name : "";
    });
  }

  // ── Saved opportunity selection ──
  const savedOppCards = document.querySelectorAll(".saved-opp-card");
  const selectedOppLabel = document.getElementById("selectedOppLabel");
  const clearSelectionBtn = document.getElementById("clearSelectionBtn");
  const jobDescriptionInput = document.getElementById("jobDescriptionInput");
  let selectedOppId = null;

  savedOppCards.forEach((card) => {
    card.addEventListener("click", () => {
      savedOppCards.forEach((c) => c.classList.remove("selected-card"));
      card.classList.add("selected-card");
      selectedOppId = card.dataset.oppId;
      const title = card.querySelector("h4")?.textContent || "Opportunity";
      selectedOppLabel.textContent = title;
      if (jobDescriptionInput && card.dataset.jobDesc) {
        jobDescriptionInput.value = card.dataset.jobDesc;
      }
    });
  });

  if (clearSelectionBtn) {
    clearSelectionBtn.addEventListener("click", () => {
      savedOppCards.forEach((c) => c.classList.remove("selected-card"));
      selectedOppId = null;
      selectedOppLabel.textContent = "None";
    });
  }

  // ── Generate Enhanced CV ──
  const generateBtn = document.getElementById("generateCvBtn");
  const status = document.getElementById("cvGenStatus");
  const results = document.getElementById("cvGenResults");

  if (generateBtn) {
    generateBtn.addEventListener("click", async () => {
      const jobDescription = jobDescriptionInput ? jobDescriptionInput.value.trim() : "";
      const additionalNotes = document.getElementById("additionalNotesInput")?.value.trim() || "";
      const cvText = pasteText ? pasteText.value.trim() : "";
      const uploadedFile = fileInput && fileInput.files.length ? fileInput.files[0] : null;

      status.classList.remove("status-error", "status-success");
      status.textContent = "Generating your enhanced CV — this can take a few seconds…";
      generateBtn.disabled = true;
      results.hidden = true;

      const formData = new FormData();
      formData.append("job_description", jobDescription);
      formData.append("additional_notes", additionalNotes);
      if (selectedOppId) formData.append("opportunity_id", selectedOppId);
      if (uploadedFile) {
        formData.append("cv_file", uploadedFile);
      } else if (cvText) {
        formData.append("cv_text", cvText);
      }

      try {
        const response = await fetch("/generate-cv/", {
          method: "POST",
          headers: { "X-CSRFToken": getCsrfToken() },
          body: formData,
        });
        const data = await response.json();

        if (!response.ok) {
          status.classList.add("status-error");
          status.textContent = data.error || "Something went wrong. Please try again.";
        } else {
          status.classList.add("status-success");
          status.textContent = "Your enhanced CV is ready.";
          document.getElementById("cvDocxLink").href = data.docx_url;
          document.getElementById("cvPdfLink").href = data.pdf_url;
          results.hidden = false;
        }
      } catch (err) {
        status.classList.add("status-error");
        status.textContent = "Couldn't reach the server. Please try again.";
      } finally {
        generateBtn.disabled = false;
      }
    });
  }
});