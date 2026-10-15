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
