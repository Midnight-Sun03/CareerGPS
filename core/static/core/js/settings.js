/* ============================================================
   Shared settings engine. Persists across pages via
   localStorage. Load this on EVERY page (ideally once, from
   dashboard_pages.html) so a choice made on Settings applies
   everywhere else too — no per-page slider/toggle markup required.
   ============================================================ */
(function () {
  var STORAGE_KEY = 'careergps-a11y-settings';

  // Boolean toggles. The ones in BODY_CLASS_KEYS also flip a class on <body>.
  var BODY_CLASS_KEYS = ['keyboard-mode', 'reduce-motion', 'read-aloud-mode', 'dark-mode'];
  var BOOL_DEFAULTS = {
    'keyboard-mode': false,
    'reduce-motion': false,
    'read-aloud-mode': false,
    'dark-mode': false,
    'notif-profile-completion': true,
    'notif-new-jobs': true,
    'notif-application-updates': true,
    'notif-saved-job-alerts': false,
    'notif-messages': true,
    'notif-connection-requests': true,
    'notif-weekly-digest': false,
    'notif-promotions': false
  };

  // Percentage sliders. 50 = normal / default, range 0–100, step 10.
  var SLIDER_DEFAULTS = { 'high-contrast-level': 50, 'font-size-level': 50 };
  var SLIDER_STEP = 10;

  function loadSettings() {
    try {
      var raw = localStorage.getItem(STORAGE_KEY);
      return raw ? JSON.parse(raw) : {};
    } catch (e) {
      return {};
    }
  }
  function saveSettings(settings) {
    try {
      localStorage.setItem(STORAGE_KEY, JSON.stringify(settings));
    } catch (e) {
      /* localStorage unavailable (e.g. private browsing) — settings just won't persist */
    }
  }

  var settings = loadSettings();
  var body = document.body;

  // ---- init booleans ----
  Object.keys(BOOL_DEFAULTS).forEach(function (key) {
    if (settings[key] === undefined) settings[key] = BOOL_DEFAULTS[key];
    if (BODY_CLASS_KEYS.indexOf(key) !== -1 && settings[key]) body.classList.add(key);
  });

  // ---- init sliders + rate ----
  Object.keys(SLIDER_DEFAULTS).forEach(function (key) {
    if (settings[key] === undefined) settings[key] = SLIDER_DEFAULTS[key];
  });
  if (settings['read-aloud-rate'] === undefined) settings['read-aloud-rate'] = '1';

  function syncSwitches(key, isOn) {
    document.querySelectorAll('[data-toggle="' + key + '"]').forEach(function (btn) {
      btn.setAttribute('aria-pressed', String(isOn));
      var label = btn.querySelector('.switch-state');
      if (label) label.textContent = isOn ? 'ON' : 'OFF';
    });
  }

  function setBool(key, isOn) {
    settings[key] = isOn;
    saveSettings(settings);
    if (BODY_CLASS_KEYS.indexOf(key) !== -1) body.classList.toggle(key, isOn);
    syncSwitches(key, isOn);
    if (key === 'read-aloud-mode') {
      if (isOn) enableReadAloud(); else disableReadAloud();
      syncRateControl();
    }
  }

  document.querySelectorAll('[data-toggle]').forEach(function (btn) {
    var key = btn.getAttribute('data-toggle');
    var isOn = !!settings[key];
    btn.setAttribute('aria-pressed', String(isOn));
    var label = btn.querySelector('.switch-state');
    if (label) label.textContent = isOn ? 'ON' : 'OFF';
    btn.addEventListener('click', function () {
      setBool(key, !settings[key]);
    });
  });

  document.addEventListener('keydown', function (e) {
    if (e.key === 'Tab' && !settings['keyboard-mode']) {
      setBool('keyboard-mode', true);
    }
  });

  // ============================================================
  // Percentage sliders (High Contrast / Font Size)
  // FIX: applySliderEffect() used to only run from inside the
  // [data-slider] UI loop further down, which only exists on the
  // Settings page. That meant contrast/font-size never actually
  // applied on Home, Opportunities, Saved, Resources, or Profile.
  // It's now also called unconditionally at init (right below),
  // so the effect applies on every page regardless of whether
  // that page renders the slider UI.
  // ============================================================
  function applySliderEffect(key, value) {
    if (key === 'high-contrast-level') {
      // 0% -> flat/no contrast, 50% -> normal (100%), 100% -> double contrast
      var contrastPct = value * 2;
      body.style.filter = contrastPct === 100 ? '' : 'contrast(' + contrastPct + '%)';
    }
    if (key === 'font-size-level') {
      // 0% -> 50% text size, 50% -> normal (100%), 100% -> 150% text size
      document.documentElement.style.fontSize = (50 + value) + '%';
    }
  }

  // ---- apply slider-driven effects everywhere, with or without slider UI ----
  Object.keys(SLIDER_DEFAULTS).forEach(function (key) {
    applySliderEffect(key, settings[key]);
  });

  function updateSliderUI(wrap, value) {
    var fill = wrap.querySelector('.slider-fill');
    var thumb = wrap.querySelector('.slider-thumb');
    var label = wrap.querySelector('.slider-value');
    var lo = Math.min(50, value);
    var hi = Math.max(50, value);
    fill.style.left = lo + '%';
    fill.style.width = (hi - lo) + '%';
    thumb.style.left = value + '%';
    thumb.setAttribute('aria-valuenow', String(value));
    thumb.setAttribute(
      'aria-valuetext',
      value === 50 ? 'Normal, 50%' : (value > 50 ? 'Increased to ' + value + '%' : 'Decreased to ' + value + '%')
    );
    label.innerHTML = value === 50 ? '<span class="is-normal">' + value + '%</span>' : value + '%';
  }

  document.querySelectorAll('[data-slider]').forEach(function (wrap) {
    var key = wrap.getAttribute('data-slider');
    var value = settings[key] !== undefined ? settings[key] : SLIDER_DEFAULTS[key];

    updateSliderUI(wrap, value);

    function change(newVal) {
      newVal = Math.max(0, Math.min(100, Math.round(newVal / SLIDER_STEP) * SLIDER_STEP));
      if (newVal === value) return;
      value = newVal;
      settings[key] = value;
      saveSettings(settings);
      updateSliderUI(wrap, value);
      applySliderEffect(key, value);
    }

    wrap.querySelectorAll('.slider-btn').forEach(function (btn) {
      btn.addEventListener('click', function () {
        var dir = parseInt(btn.getAttribute('data-dir'), 10);
        change(value + dir * SLIDER_STEP);
      });
    });

    var thumb = wrap.querySelector('.slider-thumb');
    thumb.addEventListener('keydown', function (e) {
      if (e.key === 'ArrowRight' || e.key === 'ArrowUp') { change(value + SLIDER_STEP); e.preventDefault(); }
      else if (e.key === 'ArrowLeft' || e.key === 'ArrowDown') { change(value - SLIDER_STEP); e.preventDefault(); }
      else if (e.key === 'Home') { change(0); e.preventDefault(); }
      else if (e.key === 'End') { change(100); e.preventDefault(); }
    });

    var track = wrap.querySelector('.slider-track');
    track.addEventListener('click', function (e) {
      var rect = track.getBoundingClientRect();
      var pct = ((e.clientX - rect.left) / rect.width) * 100;
      change(pct);
    });
  });

  // ============================================================
  // Read Aloud speed dropdown
  // ============================================================
  var rateSelect = document.querySelector('[data-a11y-select="read-aloud-rate"]');
  function syncRateControl() {
    if (rateSelect) rateSelect.disabled = !settings['read-aloud-mode'];
  }
  if (rateSelect) {
    rateSelect.value = settings['read-aloud-rate'];
    syncRateControl();
    rateSelect.addEventListener('change', function () {
      settings['read-aloud-rate'] = rateSelect.value;
      saveSettings(settings);
    });
  }

  // ============================================================
  // Speech synthesis (Read Aloud)
  // Works on any page: listens for clicks on [data-speak]
  // elements, wherever they exist on that page.
  // ============================================================
  var synth = window.speechSynthesis;
  var currentlySpeaking = null;

  function stopSpeaking() {
    if (synth) synth.cancel();
    if (currentlySpeaking) currentlySpeaking.classList.remove('speaking');
    currentlySpeaking = null;
  }

  function speak(el) {
    if (!synth) return;
    stopSpeaking();
    var text = el.getAttribute('data-speak') || el.textContent;
    var utterance = new SpeechSynthesisUtterance(text);
    utterance.rate = parseFloat(settings['read-aloud-rate']) || 1;
    el.classList.add('speaking');
    currentlySpeaking = el;
    utterance.onend = function () {
      el.classList.remove('speaking');
      if (currentlySpeaking === el) currentlySpeaking = null;
    };
    synth.speak(utterance);
  }

  function handleSpeakClick(e) {
    var target = e.target.closest('[data-speak]');
    if (!target) return;
    speak(target);
  }

  function enableReadAloud() {
    if (!synth) {
      alert("Sorry, this browser doesn't support the Speech Synthesis API needed for Read Aloud.");
      return;
    }
    document.addEventListener('click', handleSpeakClick);
  }

  function disableReadAloud() {
    document.removeEventListener('click', handleSpeakClick);
    stopSpeaking();
  }

  if (settings['read-aloud-mode']) enableReadAloud();
})();