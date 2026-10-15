function getCsrfToken() {
  const match = document.cookie.match(/csrftoken=([^;]+)/);
  return match ? match[1] : "";
}

document.addEventListener("DOMContentLoaded", () => {
  const tabs = document.querySelectorAll(".cv-tab");
  const panels = document.querySelectorAll(".cv-tab-panel");
  tabs.forEach((tab) => {
    tab.addEventListener("click", () => {
      tabs.forEach((t) => t.classList.remove("active-tab"));
      tab.classList.add("active-tab");
      const target = tab.dataset.tab;
      panels.forEach((panel) => { panel.hidden = panel.dataset.panel !== target; });
    });
  });

  const pasteText = document.getElementById("cvPasteText");
  const charCount = document.getElementById("cvCharCount");
  if (pasteText && charCount) {
    pasteText.addEventListener("input", () => { charCount.textContent = pasteText.value.length; });
  }

  const fileInput = document.getElementById("cvFileUpload");
  const fileNameEl = document.getElementById("cvFileUploadName");
  if (fileInput && fileNameEl) {
    fileInput.addEventListener("change", function () {
      fileNameEl.textContent = this.files.length ? "📄 " + this.files[0].name : "";
    });
  }

  const list = document.getElementById("savedOppList");
  const cards = document.querySelectorAll(".saved-opp-card");
  const selectedOppLabel = document.getElementById("selectedOppLabel");
  const clearSelectionBtn = document.getElementById("clearSelectionBtn");
  const jobDescriptionInput = document.getElementById("jobDescriptionInput");
  const jobInfoStatus = document.getElementById("jobInfoStatus");
  let selectedOppId = null;
  let resolving = false;

  async function selectOpportunity(card) {
    cards.forEach((item) => item.classList.remove("selected-card"));
    card.classList.add("selected-card");
    selectedOppId = card.dataset.oppId;
    selectedOppLabel.textContent = card.querySelector("h4")?.textContent || "Opportunity";
    if (jobDescriptionInput) {
      jobDescriptionInput.value = "";
      jobDescriptionInput.disabled = true;
      jobDescriptionInput.placeholder = "CareerGPS will use the selected opportunity's trusted job information.";
    }

    if (!card.dataset.resolveUrl || !jobInfoStatus) return;
    resolving = true;
    jobInfoStatus.classList.remove("status-error", "status-success");
    jobInfoStatus.textContent = "Checking CareerGPS job information…";
    try {
      const response = await fetch(card.dataset.resolveUrl, {
        method: "POST",
        headers: { "X-CSRFToken": getCsrfToken() },
      });
      const data = await response.json();
      if (!response.ok) {
        jobInfoStatus.classList.add("status-error");
        jobInfoStatus.textContent = data.error || "Couldn't prepare this opportunity.";
        return;
      }
      jobInfoStatus.classList.add(data.limited ? "status-error" : "status-success");
      jobInfoStatus.textContent = data.message;
    } catch (error) {
      jobInfoStatus.classList.add("status-error");
      jobInfoStatus.textContent = "Couldn't check the opportunity right now. CareerGPS will try again when you generate.";
    } finally {
      resolving = false;
    }
  }

  cards.forEach((card) => card.addEventListener("click", () => selectOpportunity(card)));
  if (clearSelectionBtn) {
    clearSelectionBtn.addEventListener("click", () => {
      cards.forEach((card) => card.classList.remove("selected-card"));
      selectedOppId = null;
      selectedOppLabel.textContent = "None";
      if (jobDescriptionInput) {
        jobDescriptionInput.disabled = false;
        jobDescriptionInput.placeholder = "Paste the job description here to personalize your cover letter...";
      }
      if (jobInfoStatus) jobInfoStatus.textContent = "";
    });
  }

  const preselectedId = list?.dataset.preselectedId;
  if (preselectedId) {
    const card = Array.from(cards).find((item) => item.dataset.oppId === preselectedId);
    if (card) selectOpportunity(card);
  }

  const generateBtn = document.getElementById("generateCoverBtn");
  const status = document.getElementById("coverGenStatus");
  const results = document.getElementById("coverGenResults");
  const toneSelect = document.getElementById("toneSelect");

  if (generateBtn) {
    generateBtn.addEventListener("click", async () => {
      const jobDescription = jobDescriptionInput ? jobDescriptionInput.value.trim() : "";
      const additionalNotes = document.getElementById("additionalNotesInput")?.value.trim() || "";
      const tone = toneSelect ? toneSelect.value : "professional";
      const cvText = pasteText ? pasteText.value.trim() : "";
      const uploadedFile = fileInput && fileInput.files.length ? fileInput.files[0] : null;

      if (!selectedOppId && !jobDescription) {
        status.classList.add("status-error");
        status.textContent = "Select a CareerGPS opportunity or paste a job description.";
        return;
      }
      if (resolving) {
        status.classList.add("status-error");
        status.textContent = "CareerGPS is still checking the selected opportunity.";
        return;
      }

      status.classList.remove("status-error", "status-success");
      status.textContent = "Generating your cover letter…";
      generateBtn.disabled = true;
      results.hidden = true;

      const formData = new FormData();
      if (!selectedOppId) formData.append("job_description", jobDescription);
      formData.append("additional_notes", additionalNotes);
      formData.append("tone", tone);
      if (selectedOppId) formData.append("opportunity_id", selectedOppId);
      if (uploadedFile) formData.append("cv_file", uploadedFile);
      else if (cvText) formData.append("cv_text", cvText);

      try {
        const response = await fetch("/generate-cover-letter/", {
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
          status.textContent = data.job_info_status === "limited_info"
            ? "Your cover letter is ready. Limited job info was used."
            : "Your cover letter is ready.";
          const previewLink = document.getElementById("coverPreviewLink");
          if (previewLink) previewLink.href = data.preview_url;
          results.hidden = false;
          if (data.preview_url) window.location.assign(data.preview_url);
        }
      } catch (error) {
        status.classList.add("status-error");
        status.textContent = "Couldn't reach the server. Please try again.";
      } finally {
        generateBtn.disabled = false;
      }
    });
  }
});
