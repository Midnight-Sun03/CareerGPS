document.addEventListener("DOMContentLoaded", () => {
    const searchInput = document.getElementById("searchInput");
    const filterButtons = document.querySelectorAll(".filter-btn");
    const jobCards = document.querySelectorAll(".job-card");

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
});