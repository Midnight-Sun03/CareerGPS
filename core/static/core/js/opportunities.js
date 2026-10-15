document.addEventListener("DOMContentLoaded", () => {
const searchInput = document.getElementById("searchInput");
const voiceBtn = document.getElementById("voiceSearchBtn");

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
            searchInput.form.requestSubmit();
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
