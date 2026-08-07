 /* ── Sticky nav shadow ────────────────────────────────── */
      const nav = document.getElementById("mainNav");
      window.addEventListener("scroll", () => {
        nav.classList.toggle("nav--scrolled", window.scrollY > 10);
      });

      /* ── Mobile burger ────────────────────────────────────── */
      const burger = document.getElementById("burger");
      const mobileMenu = document.getElementById("mobileMenu");
      burger.addEventListener("click", () => {
        const open = mobileMenu.classList.toggle("mobile-menu--open");
        burger.classList.toggle("burger--open", open);
      });
      mobileMenu.querySelectorAll("a").forEach((a) =>
        a.addEventListener("click", () => {
          mobileMenu.classList.remove("mobile-menu--open");
          burger.classList.remove("burger--open");
        }),
      );

      /* ── Job listings data ────────────────────────────────── */
      const JOBS = [
        {
          title: "Software Development Intern",
          company: "Tech Template",
          location: "Cape Town",
          type: "Internship",
          field: "Tech",
          closing: "31 Aug 2025",
        },
        {
          title: "Business Analyst Learnership",
          company: "Finance House",
          location: "Johannesburg",
          type: "Learnership",
          field: "Finance",
          closing: "15 Jul 2025",
        },
        {
          title: "Data Science Graduate Programme",
          company: "DataCo",
          location: "Remote",
          type: "Graduate",
          field: "Tech",
          closing: "30 Sep 2025",
        },
        {
          title: "Marketing Intern",
          company: "BrandWorks",
          location: "Durban",
          type: "Internship",
          field: "Business",
          closing: "20 Jun 2025",
        },
        {
          title: "Finance Learnership",
          company: "CapitalGroup",
          location: "Pretoria",
          type: "Learnership",
          field: "Finance",
          closing: "10 Aug 2025",
        },
        {
          title: "UX Design Graduate",
          company: "Creative Studio",
          location: "Cape Town",
          type: "Graduate",
          field: "Tech",
          closing: "25 Jul 2025",
        },
        {
          title: "HR Intern",
          company: "PeopleFirst",
          location: "Johannesburg",
          type: "Internship",
          field: "Business",
          closing: "5 Aug 2025",
        },
        {
          title: "Accounting Learnership",
          company: "AuditPlus",
          location: "Port Elizabeth",
          type: "Learnership",
          field: "Finance",
          closing: "18 Jul 2025",
        },
        {
          title: "Cybersecurity Intern",
          company: "SecureNet",
          location: "Remote",
          type: "Internship",
          field: "Tech",
          closing: "22 Sep 2025",
        },
        {
          title: "Mechanical Engineering Graduate",
          company: "BuildCo",
          location: "Durban",
          type: "Graduate",
          field: "Tech",
          closing: "1 Oct 2025",
        },
        {
          title: "Graphic Design Intern",
          company: "VisualArts Agency",
          location: "Cape Town",
          type: "Internship",
          field: "Business",
          closing: "14 Jun 2025",
        },
        {
          title: "Supply Chain Learnership",
          company: "LogisticsSA",
          location: "Johannesburg",
          type: "Learnership",
          field: "Business",
          closing: "30 Jun 2025",
        },
        {
          title: "Cloud Engineering Graduate",
          company: "CloudBase",
          location: "Remote",
          type: "Graduate",
          field: "Tech",
          closing: "15 Oct 2025",
        },
        {
          title: "Legal Intern",
          company: "LexFirm",
          location: "Pretoria",
          type: "Internship",
          field: "Business",
          closing: "8 Jul 2025",
        },
        {
          title: "Banking Learnership",
          company: "NationalBank",
          location: "Johannesburg",
          type: "Learnership",
          field: "Finance",
          closing: "20 Aug 2025",
        },
      ];

      const TYPE_COLOURS = {
        Internship: { bg: "#E3F2FD", color: "#1565C0" },
        Learnership: { bg: "#FFF3E0", color: "#E65100" },
        Graduate: { bg: "#E8F5E9", color: "#2E7D32" },
      };

      function buildResultCard(job) {
        const tc = TYPE_COLOURS[job.type] || { bg: "#F5F5F5", color: "#555" };
        return `
        <div class="result-card">
          <div class="result-card__left">
            <div class="result-card__logo">${job.company.charAt(0)}</div>
          </div>
          <div class="result-card__body">
            <div class="result-card__title">${job.title}</div>
            <div class="result-card__company">${job.company}</div>
            <div class="result-card__meta">
              <span class="result-card__location">
                <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" style="width:13px;height:13px;vertical-align:-2px;margin-right:3px"><path d="M21 10c0 7-9 13-9 13s-9-6-9-13a9 9 0 0 1 18 0z"/><circle cx="12" cy="10" r="3"/></svg>
                ${job.location}
              </span>
              <span class="result-card__closing">Closes ${job.closing}</span>
            </div>
          </div>
          <div class="result-card__right">
            <span class="result-card__type" style="background:${tc.bg};color:${tc.color}">${job.type}</span>
            <a href="/login/" class="result-card__apply">Apply</a>
          </div>
        </div>`;
      }

      /* ── Search logic ─────────────────────────────────────── */
      const searchInput = document.getElementById("searchInput");
      const searchBtn = document.getElementById("searchBtn");
      const resultsPanel = document.getElementById("resultsPanel");
      const resultsList = document.getElementById("resultsList");
      const resultsCount = document.getElementById("resultsCount");
      const resultsEmpty = document.getElementById("resultsEmpty");
      const closeResults = document.getElementById("closeResults");

      function runSearch() {
        const q = searchInput.value.trim().toLowerCase();
        if (!q) return;

        const hits = JOBS.filter(
          (j) =>
            j.title.toLowerCase().includes(q) ||
            j.company.toLowerCase().includes(q) ||
            j.location.toLowerCase().includes(q) ||
            j.type.toLowerCase().includes(q) ||
            j.field.toLowerCase().includes(q),
        );

        resultsPanel.hidden = false;
        if (hits.length === 0) {
          resultsList.innerHTML = "";
          resultsEmpty.hidden = false;
          resultsCount.textContent = "0 results";
        } else {
          resultsEmpty.hidden = true;
          resultsCount.textContent = `${hits.length} result${hits.length !== 1 ? "s" : ""} for "${searchInput.value.trim()}"`;
          resultsList.innerHTML = hits.map(buildResultCard).join("");
        }
        // Scroll panel into view smoothly
        setTimeout(
          () =>
            resultsPanel.scrollIntoView({
              behavior: "smooth",
              block: "nearest",
            }),
          50,
        );
      }

      searchBtn.addEventListener("click", runSearch);
      searchInput.addEventListener("keydown", (e) => {
        if (e.key === "Enter") runSearch();
      });
      closeResults.addEventListener("click", () => {
        resultsPanel.hidden = true;
        searchInput.value = "";
      });

      /* ── Counter animation ────────────────────────────────── */
      function animateCounter(el) {
        const target = parseInt(el.dataset.target);
        const step = target / (1600 / 16);
        let current = 0;
        const timer = setInterval(() => {
          current += step;
          if (current >= target) {
            current = target;
            clearInterval(timer);
          }
          el.textContent = Math.floor(current).toLocaleString();
        }, 16);
      }
      const statsObserver = new IntersectionObserver(
        (entries) => {
          entries.forEach((entry) => {
            if (entry.isIntersecting) {
              entry.target
                .querySelectorAll("[data-target]")
                .forEach(animateCounter);
              statsObserver.unobserve(entry.target);
            }
          });
        },
        { threshold: 0.4 },
      );
      const statsStrip = document.querySelector(".stats-strip");
      if (statsStrip) statsObserver.observe(statsStrip);

      /* ── Scroll reveal ────────────────────────────────────── */
      const revealObserver = new IntersectionObserver(
        (entries) => {
          entries.forEach((entry) => {
            if (entry.isIntersecting) {
              entry.target.classList.add("reveal--visible");
              revealObserver.unobserve(entry.target);
            }
          });
        },
        { threshold: 0.15 },
      );
      document
        .querySelectorAll(".reveal")
        .forEach((el) => revealObserver.observe(el));

      /* ── Smooth scroll anchors ────────────────────────────── */
      document.querySelectorAll('a[href^="#"]').forEach((a) => {
        a.addEventListener("click", function (e) {
          const t = document.querySelector(this.getAttribute("href"));
          if (t) {
            e.preventDefault();
            t.scrollIntoView({ behavior: "smooth", block: "start" });
          }
        });
      });