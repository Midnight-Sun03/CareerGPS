function getCsrfToken() {
  const match = document.cookie.match(/csrftoken=([^;]+)/);
  return match ? match[1] : "";
}

// ============================================================
// CV Generator
// ============================================================
const generateCvBtn = document.getElementById("generateCvBtn");
if (generateCvBtn) {
  generateCvBtn.addEventListener("click", async () => {
    const input = document.getElementById("jobRequirementsInput");
    const status = document.getElementById("cvGenStatus");
    const results = document.getElementById("cvGenResults");
    const jobRequirements = input.value.trim();

    if (!jobRequirements) {
      status.textContent = "Please paste the job requirements first.";
      return;
    }

    generateCvBtn.disabled = true;
    status.textContent = "Generating your CV — this can take a few seconds…";
    results.style.display = "none";

    try {
      const response = await fetch("/generate-cv/", {
        method: "POST",
        headers: {
          "Content-Type": "application/x-www-form-urlencoded",
          "X-CSRFToken": getCsrfToken(),
        },
        body: "job_requirements=" + encodeURIComponent(jobRequirements),
      });
      const data = await response.json();

      if (!response.ok) {
        status.textContent = data.error || "Something went wrong. Please try again.";
      } else {
        status.textContent = "Your CV is ready.";
        document.getElementById("cvDocxLink").href = data.docx_url;
        document.getElementById("cvPdfLink").href = data.pdf_url;
        results.style.display = "flex";
      }
    } catch (err) {
      status.textContent = "Couldn't reach the server. Please try again.";
    } finally {
      generateCvBtn.disabled = false;
    }
  });
}

// FILTER BUTTONS
const filterButtons = document.querySelectorAll(".filter-btn");
const cards = document.querySelectorAll(".resource-card");

filterButtons.forEach((btn) => {
  btn.addEventListener("click", () => {
    const type = btn.dataset.type;
    filterButtons.forEach((b) => b.classList.remove("active-btn"));
    btn.classList.add("active-btn");
    cards.forEach((card) => {
      card.style.display =
        type === "all" || card.dataset.category === type ? "block" : "none";
    });
  });
});

// SEARCH
const searchInput = document.getElementById("searchInput");
searchInput.addEventListener("input", () => {
  const val = searchInput.value.toLowerCase();
  cards.forEach((card) => {
    card.style.display = card.textContent.toLowerCase().includes(val) ? "block" : "none";
  });
});

// CV Upload
const cvUpload = document.getElementById("cvUpload");
const fileName = document.getElementById("fileName");

if (cvUpload && fileName) {
  cvUpload.addEventListener("change", function () {
    fileName.textContent = this.files.length ? "📄 " + this.files[0].name : "";
  });
}

// Cover Letter Upload (CV file for the cover letter card)
const coverLetterCvUpload = document.getElementById("coverLetterCvUpload");
const coverLetterCvFileName = document.getElementById("coverLetterCvFileName");

if (coverLetterCvUpload && coverLetterCvFileName) {
  coverLetterCvUpload.addEventListener("change", function () {
    coverLetterCvFileName.textContent = this.files.length ? "📄 " + this.files[0].name : "";
  });
}

// ============================================================
// Cover Letter Generator
// ============================================================
const generateCoverBtn = document.getElementById("generateCoverBtn");
if (generateCoverBtn) {
  generateCoverBtn.addEventListener("click", async () => {
    const input = document.getElementById("coverJobRequirementsInput");
    const status = document.getElementById("coverGenStatus");
    const results = document.getElementById("coverGenResults");
    const jobRequirements = input.value.trim();

    if (!jobRequirements) {
      status.textContent = "Please paste the job requirements first.";
      return;
    }

    if (!coverLetterCvUpload.files.length) {
      status.textContent = "Please upload your CV first.";
      return;
    }

    generateCoverBtn.disabled = true;
    status.textContent = "Generating your cover letter — this can take a few seconds…";
    results.style.display = "none";

    const formData = new FormData();
    formData.append("job_requirements", jobRequirements);
    formData.append("cv_file", coverLetterCvUpload.files[0]);

    try {
      const response = await fetch("/generate-cover-letter/", {
        method: "POST",
        headers: {
          "X-CSRFToken": getCsrfToken(),
        },
        body: formData,
      });
      const data = await response.json();

      if (!response.ok) {
        status.textContent = data.error || "Something went wrong. Please try again.";
      } else {
        status.textContent = "Your cover letter is ready.";
        document.getElementById("coverDocxLink").href = data.docx_url;
        document.getElementById("coverPdfLink").href = data.pdf_url;
        results.style.display = "flex";
      }
    } catch (err) {
      status.textContent = "Couldn't reach the server. Please try again.";
    } finally {
      generateCoverBtn.disabled = false;
    }
  });
}

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
