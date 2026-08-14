document.addEventListener("DOMContentLoaded", () => {
const searchInput = document.getElementById("searchInput");
const filterButtons = document.querySelectorAll(".filter-btn");
const jobCards = document.querySelectorAll(".job-card");
const voiceBtn = document.getElementById("voiceSearchBtn");

let activeFilter = "all";

function filterJobs() {
    const searchTerm = searchInput.value.toLowerCase().trim();

    jobCards.forEach(card => {
        const title = card.querySelector(".job-title").textContent.toLowerCase();
        const company = card.querySelector(".job-meta span").textContent.toLowerCase();
        const type = card.dataset.type.toLowerCase();

        const matchesSearch =
            title.includes(searchTerm) ||
            company.includes(searchTerm);

        const matchesFilter =
            activeFilter === "all" ||
            type === activeFilter;

        if (matchesSearch && matchesFilter) {
            card.style.display = "";
        } else {
            card.style.display = "none";
        }
    });
}

// Live search
searchInput.addEventListener("input", filterJobs);

// Filter buttons
filterButtons.forEach(button => {
    button.addEventListener("click", () => {

        // Remove active state
        filterButtons.forEach(btn =>
            btn.classList.remove("active-btn")
        );

        // Activate clicked button
        button.classList.add("active-btn");

        // Update filter
        activeFilter = button.dataset.type.toLowerCase();

        filterJobs();
    });
});

// ============================================================
// Voice search
// ============================================================
if (voiceBtn) {
    var SpeechRecognitionAPI = window.SpeechRecognition || window.webkitSpeechRecognition;

    if (!SpeechRecognitionAPI) {
        // Browser doesn't support it (e.g. Firefox) — hide the button rather than offer a dead control
        voiceBtn.style.display = "none";
    } else {
        var recognition = new SpeechRecognitionAPI();
        recognition.lang = "en-ZA";
        recognition.interimResults = false;
        recognition.maxAlternatives = 1;
        var isListening = false;

        recognition.onstart = function () {
            isListening = true;
            voiceBtn.classList.add("listening");
            voiceBtn.setAttribute("title", "Listening…");
            voiceBtn.setAttribute("aria-label", "Listening…");
        };

        recognition.onresult = function (event) {
            var transcript = event.results[0][0].transcript;
            searchInput.value = transcript;
            filterJobs();
        };

        recognition.onerror = function (event) {
            if (event.error === "not-allowed" || event.error === "service-not-allowed") {
                alert("Voice search needs microphone permission. Please allow it in your browser settings and try again.");
            }
        };

        recognition.onend = function () {
            isListening = false;
            voiceBtn.classList.remove("listening");
            voiceBtn.setAttribute("title", "Search by voice");
            voiceBtn.setAttribute("aria-label", "Search by voice");
        };

        voiceBtn.addEventListener("click", function () {
            if (isListening) {
                recognition.stop();
            } else {
                recognition.start();
            }
        });
    }
}
})