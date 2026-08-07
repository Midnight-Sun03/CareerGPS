document.addEventListener("DOMContentLoaded", () => {

    const registerForm = document.getElementById("registerForm");

    const firstName = document.getElementById("firstName");
    const lastName = document.getElementById("lastName");
    const email = document.getElementById("email");
    const password = document.getElementById("password");
    const confirmPassword = document.getElementById("confirmPassword");

    const firstNameError = document.getElementById("firstNameError");
    const lastNameError = document.getElementById("lastNameError");
    const emailError = document.getElementById("emailError");
    const passwordError = document.getElementById("passwordError");
    const confirmPasswordError = document.getElementById("confirmPasswordError");

    const togglePassword = document.getElementById("togglePassword");
    const toggleConfirmPassword = document.getElementById("toggleConfirmPassword");

    const passwordStrength = document.getElementById("passwordStrength");

    const EMAIL_REGEX = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;

    /* ===============================
       First Name Validation
    =============================== */

    function validateFirstName() {
        if (firstName.value.trim() === "") {
            firstNameError.textContent = "First name is required.";
            return false;
        }

        firstNameError.textContent = "";
        return true;
    }

    /* ===============================
       Last Name Validation
    =============================== */

    function validateLastName() {
        if (lastName.value.trim() === "") {
            lastNameError.textContent = "Last name is required.";
            return false;
        }

        lastNameError.textContent = "";
        return true;
    }

    /* ===============================
       Email Validation
    =============================== */

    function validateEmail() {

        const value = email.value.trim();

        if (value === "") {
            emailError.textContent = "Email address is required.";
            return false;
        }

        if (!value.includes("@")) {
            emailError.textContent = 'Email must contain "@"';
            return false;
        }

        if (!EMAIL_REGEX.test(value)) {
            emailError.textContent = "Please enter a valid email address.";
            return false;
        }

        emailError.textContent = "";
        return true;
    }

    /* ===============================
       Password Strength
    =============================== */

    function validatePassword() {

    const pwd = password.value;

    if (pwd.trim() === "") {
        passwordStrength.textContent = "";
        passwordError.textContent = "Password is required.";
        return false;
    }

    if (pwd.length < 8) {
        passwordStrength.textContent = "Password Strength: Weak";
        passwordStrength.style.color = "#d32f2f";
        passwordError.textContent = "Password must be at least 8 characters.";
        return false;
    }

    let score = 0;

    if (/[A-Z]/.test(pwd)) score++;
    if (/[a-z]/.test(pwd)) score++;
    if (/\d/.test(pwd)) score++;
    if (/[^A-Za-z0-9]/.test(pwd)) score++;

    if (score <= 2) {
        passwordStrength.textContent = "Password Strength: Fair";
        passwordStrength.style.color = "#f57c00";
    }
    else if (score === 3) {
        passwordStrength.textContent = "Password Strength: Good";
        passwordStrength.style.color = "#2e7d32";
    }
    else {
        passwordStrength.textContent = "Password Strength: Strong";
        passwordStrength.style.color = "#1b5e20";
    }

    passwordError.textContent = "";

    return true;
}

    /* ===============================
       Confirm Password
    =============================== */

    function validateConfirmPassword() {

        if (confirmPassword.value === "") {
            confirmPasswordError.textContent = "Please confirm your password.";
            return false;
        }

        if (password.value !== confirmPassword.value) {
            confirmPasswordError.textContent = "Passwords do not match.";
            return false;
        }

        confirmPasswordError.textContent = "";
        return true;
    }

    /* ===============================
       Password Toggle
    =============================== */

    togglePassword.addEventListener("click", function () {

    const icon = this.querySelector("i");

    if(password.type === "password"){

        password.type = "text";

        icon.classList.remove("fa-eye");
        icon.classList.add("fa-eye-slash");

    }else{

        password.type = "password";

        icon.classList.remove("fa-eye-slash");
        icon.classList.add("fa-eye");

    }

    });

    toggleConfirmPassword.addEventListener("click", function () {

    const icon = this.querySelector("i");

    if(confirmPassword.type === "password"){

        confirmPassword.type = "text";

        icon.classList.remove("fa-eye");
        icon.classList.add("fa-eye-slash");

    }else{

        confirmPassword.type = "password";

        icon.classList.remove("fa-eye-slash");
        icon.classList.add("fa-eye");

    }

    });

    /* ===============================
       Live Validation
    =============================== */

    firstName.addEventListener("blur", validateFirstName);
    lastName.addEventListener("blur", validateLastName);
    email.addEventListener("blur", validateEmail);
    password.addEventListener("blur", validatePassword);
    confirmPassword.addEventListener("blur", validateConfirmPassword);
    firstName.addEventListener("input", validateFirstName);
    lastName.addEventListener("input", validateLastName);
    email.addEventListener("input", validateEmail);
    password.addEventListener("input", validatePassword);
    confirmPassword.addEventListener("input", validateConfirmPassword);

    password.addEventListener("input", () => {
        validatePassword();

        if (confirmPassword.value.length > 0) {
            validateConfirmPassword();
        }
    });

    confirmPassword.addEventListener("input", validateConfirmPassword);

    /* ===============================
       Form Submit
    =============================== */

    registerForm.addEventListener("submit", function(e){

        e.preventDefault();

        const firstValid = validateFirstName();
        const lastValid = validateLastName();
        const emailValid = validateEmail();
        const passwordValid = validatePassword();
        const confirmValid = validateConfirmPassword();

        if(!firstValid){
          firstName.focus();
          return;
        }

        if(!lastValid){
         lastName.focus();
         return;
        }

       if(!emailValid){
        email.focus();
        return;
        }

       if(!passwordValid){
        password.focus();
        return;
        }

      if(!confirmValid){
       confirmPassword.focus();
       return;
        }

      registerForm.submit();

    });

});