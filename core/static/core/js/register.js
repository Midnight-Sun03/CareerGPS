// ============================================================
// CareerGPS - Registration JavaScript (WITH DEBUGGING)
// ============================================================

console.log('🚀 JavaScript loaded successfully');

// CSRF Token
function getCookie(name) {
    let cookieValue = null;
    if (document.cookie && document.cookie !== '') {
        const cookies = document.cookie.split(';');
        for (let i = 0; i < cookies.length; i++) {
            const cookie = cookies[i].trim();
            if (cookie.substring(0, name.length + 1) === (name + '=')) {
                cookieValue = decodeURIComponent(cookie.substring(name.length + 1));
                break;
            }
        }
    }
    return cookieValue;
}

const csrftoken = getCookie('csrftoken');
console.log('CSRF Token:', csrftoken ? '✅ Found' : '❌ Not Found');

// Check if DOM elements exist
document.addEventListener('DOMContentLoaded', function() {
    console.log('📄 DOM loaded');
    
    const registerForm = document.getElementById('registerForm');
    console.log('Register Form element:', registerForm ? '✅ Found' : '❌ Not Found');
    
    const firstName = document.getElementById('firstName');
    console.log('First Name input:', firstName ? '✅ Found' : '❌ Not Found');
    
    const lastName = document.getElementById('lastName');
    console.log('Last Name input:', lastName ? '✅ Found' : '❌ Not Found');
    
    const email = document.getElementById('email');
    console.log('Email input:', email ? '✅ Found' : '❌ Not Found');
    
    const password = document.getElementById('password');
    console.log('Password input:', password ? '✅ Found' : '❌ Not Found');
    
    const confirmPassword = document.getElementById('confirmPassword');
    console.log('Confirm Password input:', confirmPassword ? '✅ Found' : '❌ Not Found');
    
    // Attach event listener if form exists
    if (registerForm) {
        registerForm.addEventListener('submit', function(e) {
            console.log('🔄 Form submit event triggered!');
            e.preventDefault();
            handleRegistration(e);
        });
        console.log('✅ Event listener attached to form');
    } else {
        console.error('❌ Register form not found!');
    }
});

// Handle Registration
async function handleRegistration(event) {
    console.log('📝 Starting registration process...');
    
    const firstName = document.getElementById('firstName').value.trim();
    const lastName = document.getElementById('lastName').value.trim();
    const email = document.getElementById('email').value.trim();
    const password = document.getElementById('password').value;
    const confirmPassword = document.getElementById('confirmPassword').value;
    
    console.log('Form values:', {
        firstName: firstName || '(empty)',
        lastName: lastName || '(empty)',
        email: email || '(empty)',
        passwordLength: password.length,
        passwordsMatch: password === confirmPassword
    });
    
    // Clear previous errors
    document.querySelectorAll('.form-error').forEach(el => el.textContent = '');
    
    let hasError = false;
    
    // Validation
    if (!firstName) {
        showError('firstNameError', 'First name is required');
        hasError = true;
    }
    
    if (!lastName) {
        showError('lastNameError', 'Last name is required');
        hasError = true;
    }
    
    if (!email) {
        showError('emailError', 'Email address is required');
        hasError = true;
    } else {
        const emailRegex = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;
        if (!emailRegex.test(email)) {
            showError('emailError', 'Please enter a valid email address');
            hasError = true;
        }
    }
    
    if (!password) {
        showError('passwordError', 'Password is required');
        hasError = true;
    } else if (password.length < 8) {
        showError('passwordError', 'Password must be at least 8 characters');
        hasError = true;
    }
    
    if (password !== confirmPassword) {
        showError('confirmError', 'Passwords do not match');
        hasError = true;
    }
    
    if (hasError) {
        console.log('❌ Validation failed');
        return;
    }
    
    console.log('✅ Validation passed, sending to server...');
    showLoading();
    
    try {
        const requestBody = {
            first_name: firstName,
            last_name: lastName,
            email: email,
            password: password
        };
        
        console.log('Sending request to:', '/api/register/');
        console.log('Request body:', requestBody);
        
        const response = await fetch('/api/register/', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
                'X-CSRFToken': csrftoken
            },
            body: JSON.stringify(requestBody)
        });
        
        console.log('Response status:', response.status);
        
        const data = await response.json();
        console.log('Response data:', data);
        
        if (response.ok && data.success) {
            console.log('✅ Registration successful! Moving to step 2');
            userData.email = email;
            userData.first_name = firstName;
            userData.last_name = lastName;
            userData.password = password;
            
            const userEmailSpan = document.getElementById('userEmail');
            if (userEmailSpan) {
                userEmailSpan.textContent = email;
            }
            
            startCountdown();
            goToStep(2);
        } else {
            console.log('❌ Registration failed:', data.error);
            showError('emailError', data.error || 'Registration failed');
        }
    } catch (error) {
        console.error('❌ Network error:', error);
        showError('emailError', 'Network error. Please check if server is running.');
    } finally {
        hideLoading();
    }
}

// Other helper functions
function showError(elementId, message) {
    const element = document.getElementById(elementId);
    if (element) {
        element.textContent = message;
        console.log(`Error shown for ${elementId}: ${message}`);
        setTimeout(() => {
            element.textContent = '';
        }, 5000);
    }
}

function showLoading() {
    const overlay = document.getElementById('loadingOverlay');
    if (overlay) {
        overlay.classList.add('loading-overlay--active');
        console.log('Loading overlay shown');
    }
}

function hideLoading() {
    const overlay = document.getElementById('loadingOverlay');
    if (overlay) {
        overlay.classList.remove('loading-overlay--active');
        console.log('Loading overlay hidden');
    }
}

function goToStep(step) {
    console.log(`Moving to step ${step}`);
    const step1 = document.getElementById('step1');
    const step2 = document.getElementById('step2');
    const step3 = document.getElementById('step3');
    
    if (step1) step1.style.display = step === 1 ? 'block' : 'none';
    if (step2) step2.style.display = step === 2 ? 'block' : 'none';
    if (step3) step3.style.display = step === 3 ? 'block' : 'none';
    
    updateStepIndicator(step);
}

function updateStepIndicator(step) {
    const steps = document.querySelectorAll('.step');
    steps.forEach((s, index) => {
        const stepNum = index + 1;
        s.classList.remove('step--active', 'step--completed');
        if (stepNum < step) {
            s.classList.add('step--completed');
        } else if (stepNum === step) {
            s.classList.add('step--active');
        }
    });
}

let countdownTimer = null;
let countdownValue = 60;

function startCountdown() {
    countdownValue = 60;
    const countdownEl = document.getElementById('countdown');
    if (countdownTimer) clearInterval(countdownTimer);
    
    countdownTimer = setInterval(() => {
        countdownValue--;
        if (countdownValue <= 0) {
            clearInterval(countdownTimer);
            if (countdownEl) countdownEl.textContent = '';
        } else {
            if (countdownEl) countdownEl.textContent = `Resend available in ${countdownValue}s`;
        }
    }, 1000);
    console.log('Countdown started');
}

// User data storage
let userData = {
    email: null,
    first_name: null,
    last_name: null,
    password: null,
    degree: null,
    institution: null,
    location: null,
    skills: [],
    interests: [],
    other_skills: '',
    custom_skills: [],
    custom_interests: []
};

// Step 2: Verify Email
const verifyForm = document.getElementById('verifyForm');
if (verifyForm) {
    console.log('✅ Verify form found');
    verifyForm.addEventListener('submit', async (e) => {
        e.preventDefault();
        console.log('🔄 Verify form submitted');
        
        const code = document.getElementById('verificationCode').value.trim();
        console.log('Entered code:', code);
        
        if (!code || code.length !== 6) {
            showError('codeError', 'Please enter a valid 6-digit verification code');
            return;
        }
        
        showLoading();
        
        try {
            const response = await fetch('/api/verify-email/', {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                    'X-CSRFToken': csrftoken
                },
                body: JSON.stringify({
                    email: userData.email,
                    code: code
                })
            });
            
            const data = await response.json();
            console.log('Verify response:', data);
            
            if (response.ok && data.success) {
                console.log('✅ Email verified! Moving to step 3');
                goToStep(3);
            } else {
                showError('codeError', data.error || 'Invalid verification code');
            }
        } catch (error) {
            console.error('Verification error:', error);
            showError('codeError', 'Network error. Please try again.');
        } finally {
            hideLoading();
        }
    });
}

// Resend code
const resendBtn = document.getElementById('resendBtn');
if (resendBtn) {
    resendBtn.addEventListener('click', async () => {
        console.log('🔄 Resend code clicked');
        if (countdownValue > 0) {
            alert(`Please wait ${countdownValue} seconds before resending`);
            return;
        }
        
        showLoading();
        try {
            const response = await fetch('/api/resend-verification/', {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                    'X-CSRFToken': csrftoken
                },
                body: JSON.stringify({ email: userData.email })
            });
            
            if (response.ok) {
                startCountdown();
                alert('Verification code resent! Check your terminal.');
                console.log('✅ Code resent');
            } else {
                alert('Failed to resend code');
            }
        } catch (error) {
            alert('Network error');
        } finally {
            hideLoading();
        }
    });
}

// Step 3: Complete Profile
const profileForm = document.getElementById('profileForm');
if (profileForm) {
    profileForm.addEventListener('submit', async (e) => {
        e.preventDefault();
        console.log('🔄 Profile form submitted');
        
        // Get values
        let degree = document.getElementById('degree').value;
        const customDegree = document.getElementById('customDegree').value;
        if (degree === 'other') degree = customDegree;
        
        let institution = document.getElementById('institution').value;
        const customInstitution = document.getElementById('customInstitution').value;
        if (institution === 'other') institution = customInstitution;
        
        let location = document.getElementById('location').value;
        const customLocation = document.getElementById('customLocation').value;
        if (location === 'other') location = customLocation;
        
        const skillsSelect = document.getElementById('skills');
        const interestsSelect = document.getElementById('interests');
        
        const selectedSkills = Array.from(skillsSelect.selectedOptions).map(opt => opt.value);
        const selectedInterests = Array.from(interestsSelect.selectedOptions).map(opt => opt.value);
        const otherSkills = document.getElementById('otherSkills').value;
        
        // Validation
        if (!degree || !institution || !location) {
            alert('Please fill in all required fields');
            return;
        }
        
        userData.degree = degree;
        userData.institution = institution;
        userData.location = location;
        userData.skills = selectedSkills;
        userData.interests = selectedInterests;
        userData.other_skills = otherSkills;
        
        showLoading();
        
        try {
            const response = await fetch('/api/complete-profile/', {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                    'X-CSRFToken': csrftoken
                },
                body: JSON.stringify(userData)
            });
            
            if (response.ok) {
                console.log('✅ Profile completed!');
                document.getElementById('successPopup').classList.add('success-popup--active');
            } else {
                const data = await response.json();
                alert('Error: ' + (data.error || 'Please try again'));
            }
        } catch (error) {
            console.error('Profile error:', error);
            alert('Network error');
        } finally {
            hideLoading();
        }
    });
}

// Custom select handlers
function setupCustomSelect(selectId, customId) {
    const select = document.getElementById(selectId);
    const custom = document.getElementById(customId);
    if (select && custom) {
        select.addEventListener('change', () => {
            custom.style.display = select.value === 'other' ? 'block' : 'none';
        });
    }
}

setupCustomSelect('degree', 'customDegree');
setupCustomSelect('institution', 'customInstitution');
setupCustomSelect('location', 'customLocation');

// Password strength checker (simplified)
const passwordInput = document.getElementById('password');
if (passwordInput) {
    passwordInput.addEventListener('input', function() {
        const strengthDiv = document.getElementById('passwordStrength');
        const val = this.value;
        if (!val) {
            strengthDiv.innerHTML = '';
            return;
        }
        
        let strength = 0;
        if (val.length >= 8) strength++;
        if (/[a-z]/.test(val)) strength++;
        if (/[A-Z]/.test(val)) strength++;
        if (/[0-9]/.test(val)) strength++;
        
        let text = '';
        let className = '';
        if (strength <= 2) {
            text = 'Weak';
            className = 'strength-weak';
        } else if (strength <= 3) {
            text = 'Medium';
            className = 'strength-medium';
        } else {
            text = 'Strong';
            className = 'strength-strong';
        }
        
        strengthDiv.innerHTML = `<div class="${className}">${text} password</div>`;
    });
}

// Mobile menu
const burger = document.getElementById('burger');
const mobileMenu = document.getElementById('mobileMenu');
if (burger && mobileMenu) {
    burger.addEventListener('click', () => {
        mobileMenu.classList.toggle('mobile-menu--open');
        burger.classList.toggle('burger--open');
    });
}

// Go to dashboard
const goToDashboard = document.getElementById('goToDashboard');
if (goToDashboard) {
    goToDashboard.addEventListener('click', () => {
        window.location.href = '/welcome/';
    });
}

// Custom skills/interests (simplified)
let customSkills = [];
let customInterests = [];

function addCustomItem(type) {
    const input = document.getElementById(`custom${type.charAt(0).toUpperCase() + type.slice(1)}`);
    const value = input.value.trim();
    if (value) {
        if (type === 'skill') {
            customSkills.push(value);
            renderCustomTags('customSkillsList', customSkills, 'skill');
        } else {
            customInterests.push(value);
            renderCustomTags('customInterestsList', customInterests, 'interest');
        }
        input.value = '';
        userData.custom_skills = customSkills;
        userData.custom_interests = customInterests;
    }
}

function renderCustomTags(containerId, items, type) {
    const container = document.getElementById(containerId);
    if (container) {
        container.innerHTML = items.map(item => `
            <span class="custom-tag">
                ${item}
                <span class="custom-tag__remove" onclick="removeCustomItem('${type}', '${item}')">×</span>
            </span>
        `).join('');
    }
}

window.removeCustomItem = function(type, item) {
    if (type === 'skill') {
        customSkills = customSkills.filter(s => s !== item);
        renderCustomTags('customSkillsList', customSkills, 'skill');
        userData.custom_skills = customSkills;
    } else {
        customInterests = customInterests.filter(i => i !== item);
        renderCustomTags('customInterestsList', customInterests, 'interest');
        userData.custom_interests = customInterests;
    }
};

// Add button listeners
const addSkillBtn = document.getElementById('addSkillBtn');
const addInterestBtn = document.getElementById('addInterestBtn');
if (addSkillBtn) addSkillBtn.onclick = () => addCustomItem('skill');
if (addInterestBtn) addInterestBtn.onclick = () => addCustomItem('interest');