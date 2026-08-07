
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
              type === "all" || card.dataset.category === type
                ? "block"
                : "none";
          });
        });
      });

      // SEARCH
      const searchInput = document.getElementById("searchInput");
      searchInput.addEventListener("input", () => {
        const val = searchInput.value.toLowerCase();
        cards.forEach((card) => {
          card.style.display = card.textContent.toLowerCase().includes(val)
            ? "block"
            : "none";
        });
      });

      // CV Upload
         const cvUpload = document.getElementById("cvUpload");
         const fileName = document.getElementById("fileName");

        if (cvUpload && fileName) {
          cvUpload.addEventListener("change", function () {
         fileName.textContent = this.files.length
          ? "📄 " + this.files[0].name
        : "";
      });
     }

      // Cover Letter Upload
         const coverUpload = document.getElementById("coverUpload");
         const coverFileName = document.getElementById("coverFileName");

       if (coverUpload && coverFileName) {
        coverUpload.addEventListener("change", function () {
        coverFileName.textContent = this.files.length
          ? "📄 " + this.files[0].name
          : "";
      });
     }