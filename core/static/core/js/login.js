 /* ── 2. Real-time email validation ─────────────────────────
       Shows feedback as soon as the user leaves the field OR
       while typing once they've already blurred once.
    ──────────────────────────────────────────────────────────── */
    const loginForm = document.getElementById("loginForm");
    const emailInput = document.getElementById("email");
    const emailErr = document.getElementById("emailError");
    const passwordInput = document.getElementById("password");
    const pwdErr = document.getElementById("pwdError");
    const togglePwd = document.getElementById("togglePwd");
    let emailTouched  = false;

    const EMAIL_RE = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;

    function validateEmail(value) {
      if (!value) {
        return 'Email address is required.';
      }
      if (!value.includes('@')) {
        return 'Missing "@" — a valid email must contain an @ symbol.';
      }
      const parts = value.split('@');
      if (parts[0].length === 0) {
        return 'Please enter something before the "@" symbol.';
      }
      if (!parts[1] || !parts[1].includes('.')) {
        return 'Please enter a valid domain after "@" (e.g. gmail.com).';
      }
      if (!EMAIL_RE.test(value)) {
        return 'That doesn\'t look like a valid email address.';
      }
      return ''; // valid
    }

    function showEmailFeedback() {
      const msg = validateEmail(emailInput.value);
      emailErr.textContent = msg;
      emailInput.classList.toggle('form__input--error', !!msg);
      emailInput.classList.toggle('form__input--valid', !msg && emailInput.value.length > 0);
    }

    // On blur (leaving the field) — mark as touched and validate
    emailInput.addEventListener('blur', function () {
      emailTouched = true;
      showEmailFeedback();
    });

    // On input (while typing) — only show live feedback after first blur
    emailInput.addEventListener('input', function () {
      if (emailTouched) showEmailFeedback();
    });

    /* ── Password validation ───────────────────────── */

    passwordInput.addEventListener("blur", function () {

    if (!passwordInput.value.trim()) {
        pwdErr.textContent = "Password is required.";
        passwordInput.classList.add("form__input--error");
    } else {
        pwdErr.textContent = "";
        passwordInput.classList.remove("form__input--error");
    }

    });

     passwordInput.addEventListener("input", function () {

    if (passwordInput.value.trim()) {
        pwdErr.textContent = "";
        passwordInput.classList.remove("form__input--error");
    }

    });

    /* ── 3. Password visibility toggle ─────────────────────────  */
      togglePwd.addEventListener("click", function () {

      const visible = passwordInput.type === "text";

      passwordInput.type = visible ? "password" : "text";

      this.innerHTML = visible
        ? `
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor"
             stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
            <path d="M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8"/>
            <circle cx="12" cy="12" r="3"/>
        </svg>
        `
        : `
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor"
             stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
            <path d="M17.94 17.94A10.94 10.94 0 0 1 12 20
                     C5 20 1 12 1 12
                     a21.77 21.77 0 0 1 5.06-5.94"/>
            <path d="M9.9 4.24A10.94 10.94 0 0 1 12 4
                     c7 0 11 8 11 8
                     a21.77 21.77 0 0 1-4.23 5.39"/>
            <path d="M1 1l22 22"/>
            <circle cx="12" cy="12" r="3"/>
        </svg>
        `;
     });

    /* ── 4. Submit validation ───────────────────────────────────  */
     loginForm.addEventListener("submit", function (e) {
      e.preventDefault();
      emailTouched = true; // force email feedback on submit
      showEmailFeedback();

      const pwd = passwordInput;
      pwdErr.textContent = '';

      let pwdValid = true;

      if (!pwd.value.trim()) {
        pwdErr.textContent = "Password is required.";
        pwd.classList.add("form__input--error");
        pwdValid = false;} 
      else {
        pwdErr.textContent = "";
        pwd.classList.remove("form__input--error");
           }
      const emailValid = !validateEmail(emailInput.value);

      if (emailValid && pwdValid) {
         loginForm.submit();
       }
    });