/**
 * AgriTrace Frontend Controller
 * Accessible, farmer-centric, low-bandwidth optimized controller.
 * Enforces XSS protection (esc / textContent), client-side image compression (< 1MB),
 * idempotency headers, hash routing, and honest provenance tracking.
 */

const API_BASE = '/api';

const state = {
  token: localStorage.getItem('agritrace_token') || null,
  farmer: null,
  activePane: 'home',
  farms: [],
  investigations: [],
  activeInvestigation: null,
  limits: null,
  lang: localStorage.getItem('agritrace_lang') || 'en',
  translations: {},
  compressedPhotoBlob: null,
  isSubmitting: false,
  graphAnimId: null,
  graphNodes: [],
  graphEdges: [],
  graphTransform: { x: 0, y: 0, scale: 1 },
  graphPhysics: true,
  graphFilter: 'ALL',
  selectedGraphNode: null,
  hoveredGraphNode: null,
  pendingInvestigationContext: null,
  focusedNodeId: null
};

const WMO_WEATHER_MAP = {
  0: "Clear sky",
  1: "Mainly clear",
  2: "Partly cloudy",
  3: "Overcast",
  45: "Fog",
  48: "Depositing rime fog",
  51: "Light drizzle",
  53: "Moderate drizzle",
  55: "Dense drizzle",
  61: "Slight rain",
  63: "Moderate rain",
  65: "Heavy rain",
  71: "Slight snow",
  73: "Moderate snow",
  75: "Heavy snow",
  80: "Slight rain showers",
  81: "Moderate rain showers",
  82: "Violent rain showers",
  95: "Thunderstorm",
  96: "Thunderstorm with slight hail",
  99: "Thunderstorm with heavy hail"
};

// Safe HTML escape helper
function esc(str) {
  if (str === null || str === undefined) return '';
  return String(str)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#039;');
}

// Single Provenance Chip Component (Section 2.1)
function renderProvenance(kind, extra = '') {
  const kinds = {
    farmer_entered: { label: 'Farmer Reported', class: 'chip-farmer', icon: 'person' },
    sensor: { label: 'Field Reading', class: 'chip-sensor', icon: 'sensors' },
    lab_card: { label: extra ? `Soil Test Card #${esc(extra)}` : 'Soil Test Card', class: 'chip-lab', icon: 'description' },
    rule_engine: { label: extra ? `Guidance (${esc(extra)})` : 'Rule-based Guidance', class: 'chip-rule', icon: 'menu_book' },
    ai_model: { label: extra ? `AI (${esc(extra)})` : 'AI-generated', class: 'chip-ai', icon: 'neurology' },
    dataset_benchmark: { label: 'Public Benchmark', class: 'chip-benchmark', icon: 'dataset' }
  };
  const cfg = kinds[kind] || { label: 'Record', class: 'chip-benchmark', icon: 'info' };
  return `<span class="provenance-chip ${cfg.class}">
    <span class="material-symbols-outlined" style="font-size: 14px;">${cfg.icon}</span>
    <span>${cfg.label}</span>
  </span>`;
}

// Evidence Strength Badge Helper (Section 2.2)
function renderEvidenceStrength(strength) {
  const s = String(strength || 'Moderate').toLowerCase();
  if (s === 'strong') {
    return `<span class="strength-badge strength-strong">Evidence: Strong</span>`;
  } else if (s === 'weak') {
    return `<span class="strength-badge strength-weak">Evidence: Weak</span>`;
  }
  return `<span class="strength-badge strength-moderate">Evidence: Moderate</span>`;
}

// Stacking Toast Notification Manager
function showToast(message, type = 'info') {
  const container = document.getElementById('toast-container');
  if (!container) return;

  const toast = document.createElement('div');
  toast.className = `toast toast-${type}`;
  toast.setAttribute('role', 'alert');
  
  const span = document.createElement('span');
  span.textContent = message;
  toast.appendChild(span);

  const closeBtn = document.createElement('button');
  closeBtn.textContent = '✕';
  closeBtn.style.background = 'transparent';
  closeBtn.style.border = 'none';
  closeBtn.style.color = '#ffffff';
  closeBtn.style.cursor = 'pointer';
  closeBtn.style.padding = '0 0.25rem';
  closeBtn.onclick = () => toast.remove();
  toast.appendChild(closeBtn);

  container.appendChild(toast);
  setTimeout(() => toast.remove(), 5000);
}

// Generate unique idempotency key for mutations
function generateIdempotencyKey() {
  return 'idemp_' + Date.now() + '_' + Math.random().toString(36).substring(2, 9);
}

// HTTP Client with automatic HttpOnly cookie or Bearer header handling
async function apiCall(endpoint, method = 'GET', data = null, customHeaders = {}) {
  const headers = { ...customHeaders };
  if (state.token) headers['Authorization'] = `Bearer ${state.token}`;
  if (data && !(data instanceof FormData)) {
    headers['Content-Type'] = 'application/json';
  }

  const options = { method, headers };
  if (data) {
    options.body = data instanceof FormData ? data : JSON.stringify(data);
  }

  try {
    const res = await fetch(`${API_BASE}${endpoint}`, options);
    if (res.status === 401 && !endpoint.includes('/auth/login') && !endpoint.includes('/auth/register')) {
      handleLogout(false);
      throw new Error('Your session has expired. Please sign in again.');
    }
    const json = await res.json().catch(() => ({}));
    if (!res.ok) {
      const errDetail = json.detail || json.message || 'Request failed';
      throw new Error(typeof errDetail === 'string' ? errDetail : 'Validation error');
    }
    return json;
  } catch (err) {
    if (!navigator.onLine) {
      showToast('No internet connection. Operating offline.', 'error');
    }
    throw err;
  }
}

// App Initialization
document.addEventListener('DOMContentLoaded', async () => {
  setupOnlineStatus();
  setupLanguage();
  setupHashRouting();
  setupEventHandlers();
  await loadValidationLimits();
  await refreshSystemStatus();

  if (state.token) {
    try {
      const res = await apiCall('/auth/me');
      state.farmer = res.farmer;
      updateUserUI();
      navigateByHash(window.location.hash || '#/home');
    } catch (e) {
      handleLogout(false);
    }
  } else {
    showPane('onboarding');
  }
});

// Offline Banner Listener
function setupOnlineStatus() {
  const banner = document.getElementById('offline-banner');
  const updateStatus = () => {
    if (banner) banner.style.display = navigator.onLine ? 'none' : 'block';
  };
  window.addEventListener('online', updateStatus);
  window.addEventListener('offline', updateStatus);
  updateStatus();
}

// Localization
async function setupLanguage() {
  const switcher = document.getElementById('lang-switcher');
  if (switcher) {
    switcher.value = state.lang;
    switcher.addEventListener('change', async (e) => {
      state.lang = e.target.value;
      localStorage.setItem('agritrace_lang', state.lang);
      document.documentElement.lang = state.lang;
      await loadLocaleStrings();
    });
  }
  await loadLocaleStrings();
}

async function loadLocaleStrings() {
  try {
    const res = await fetch(`/locales/${state.lang}.json`);
    if (res.ok) {
      state.translations = await res.json();
      applyLocaleStrings();
    }
  } catch (e) {
    // English fallback already present in markup
  }
}

function applyLocaleStrings() {
  const t = state.translations;
  if (!t) return;
  const updateTxt = (id, key) => {
    const el = document.getElementById(id);
    if (el && t[key]) el.textContent = t[key];
  };
  updateTxt('txt-nav-home', 'nav_home');
  updateTxt('txt-nav-ask', 'nav_ask');
  updateTxt('txt-nav-my-farm', 'nav_my_farm');
  updateTxt('txt-nav-results', 'nav_results');
  updateTxt('txt-nav-about', 'nav_about');
  updateTxt('txt-logout', 'nav_logout');
}

// Fetch validation limits from server (Section 2.3)
async function loadValidationLimits() {
  try {
    state.limits = await apiCall('/meta/limits');
  } catch (e) {
    // Fallback in-memory defaults
  }
}

// Hash Routing (Section 5)
function setupHashRouting() {
  window.addEventListener('hashchange', () => {
    if (state.token) {
      navigateByHash(window.location.hash);
    }
  });
}

function navigateByHash(hash) {
  const path = (hash || '#/home').replace('#/', '');
  const [paneName, id] = path.split('/');

  const validPanes = ['home', 'ask', 'my-farm', 'results', 'graph', 'about'];
  const target = validPanes.includes(paneName) ? paneName : 'home';

  showPane(target);

  if (target === 'home') loadHomeData();
  else if (target === 'ask') { loadAskFields(); refreshAskContext(); }
  else if (target === 'my-farm') loadMyFarmData();
  else if (target === 'graph') loadUnifiedGraph();
  else if (target === 'results') {
    if (id) {
      openInvestigationDetail(id);
    } else {
      loadResultsList();
    }
  } else if (target === 'about') {
    loadAboutData();
  }
}

function showPane(paneName) {
  state.activePane = paneName;
  document.querySelectorAll('.view-pane').forEach(el => el.classList.remove('active'));
  document.querySelectorAll('.nav-tab').forEach(el => el.classList.remove('active'));

  const pane = document.getElementById(`pane-${paneName}`);
  if (pane) pane.classList.add('active');

  const tab = document.querySelector(`.nav-tab[data-pane="${paneName}"]`);
  if (tab) tab.classList.add('active');

  // Pause graph animation loop when canvas is hidden to conserve battery
  if (paneName !== 'results' && paneName !== 'graph' && state.graphAnimId) {
    cancelAnimationFrame(state.graphAnimId);
    state.graphAnimId = null;
  }
}

// Header & User UI
function updateUserUI() {
  const nav = document.getElementById('farmer-nav-bar');
  const userInfo = document.getElementById('user-header-info');
  const userDisplayName = document.getElementById('user-display-name');

  if (state.farmer) {
    if (nav) nav.style.display = 'flex';
    if (userInfo) userInfo.style.display = 'flex';
    if (userDisplayName) userDisplayName.textContent = state.farmer.display_name;
  } else {
    if (nav) nav.style.display = 'none';
    if (userInfo) userInfo.style.display = 'none';
  }
}

// Set Auth Mode Toggle (Login vs Register)
function setAuthMode(mode) {
  const formLogin = document.getElementById('form-login');
  const formReg = document.getElementById('form-register');
  const btnLogin = document.getElementById('toggle-auth-login');
  const btnReg = document.getElementById('toggle-auth-reg');

  if (mode === 'login') {
    if (formLogin) formLogin.style.display = 'flex';
    if (formReg) formReg.style.display = 'none';
    btnLogin.className = 'btn btn-primary';
    btnReg.className = 'btn btn-secondary';
  } else {
    if (formLogin) formLogin.style.display = 'none';
    if (formReg) formReg.style.display = 'flex';
    btnLogin.className = 'btn btn-secondary';
    btnReg.className = 'btn btn-primary';
  }
}

function togglePasswordVisibility(inputId) {
  const input = document.getElementById(inputId);
  if (input) {
    input.type = input.type === 'password' ? 'text' : 'password';
  }
}

// System Status Footer Line (Section 4)
async function refreshSystemStatus() {
  const footerStatus = document.getElementById('footer-status-line');
  try {
    const health = await apiCall('/diagnostics/health');
    const aiText = health.ai_engine?.live_key_configured
      ? `AI: ${esc(health.ai_engine.mode)}`
      : 'AI: rule-based (offline)';
    const backupText = health.cloud_backup?.configured
      ? 'Backup: Supabase Cloud'
      : 'Backup: this server only';
    const graphText = health.graph_backend === 'FalkorDB'
      ? 'Graph: FalkorDB'
      : 'Graph: built-in';

    if (footerStatus) {
      footerStatus.textContent = `${aiText} · ${backupText} · ${graphText}`;
    }
  } catch (e) {
    if (footerStatus) {
      footerStatus.textContent = 'AI: rule-based (offline) · Backup: this server only · Graph: built-in';
    }
  }
}

// Setup Form Handlers
function setupEventHandlers() {
  // Login Form
  const formLogin = document.getElementById('form-login');
  if (formLogin) {
    formLogin.addEventListener('submit', async (e) => {
      e.preventDefault();
      const btn = document.getElementById('btn-submit-login');
      btn.disabled = true;
      btn.textContent = 'Signing in...';

      const fd = new FormData(formLogin);
      try {
        const res = await apiCall('/auth/login', 'POST', {
          username: fd.get('username').trim(),
          password: fd.get('password')
        });
        state.token = res.token;
        state.farmer = res.farmer;
        localStorage.setItem('agritrace_token', res.token);
        formLogin.reset();
        updateUserUI();
        showToast('Signed in successfully', 'success');
        window.location.hash = '#/home';
      } catch (err) {
        showToast(err.message, 'error');
      } finally {
        btn.disabled = false;
        btn.textContent = 'Sign In to My Farm';
      }
    });
  }

  // Register Form
  const formReg = document.getElementById('form-register');
  if (formReg) {
    formReg.addEventListener('submit', async (e) => {
      e.preventDefault();
      const btn = document.getElementById('btn-submit-register');
      btn.disabled = true;
      btn.textContent = 'Saving farm...';

      const fd = new FormData(formReg);
      const totalAreaRaw = fd.get('total_area');
      const totalArea = totalAreaRaw ? parseFloat(totalAreaRaw) : null;

      const latVal = fd.get('latitude') ? parseFloat(fd.get('latitude')) : null;
      const lonVal = fd.get('longitude') ? parseFloat(fd.get('longitude')) : null;

      const payload = {
        username: fd.get('username').trim(),
        password: fd.get('password'),
        display_name: fd.get('display_name').trim(),
        village: fd.get('village')?.trim() || '',
        district: fd.get('district')?.trim() || '',
        state: fd.get('state')?.trim() || '',
        latitude: latVal,
        longitude: lonVal,
        consent_gps: fd.get('consent_gps') === 'on' || (latVal !== null && lonVal !== null),
        farm_name: fd.get('farm_name')?.trim() || null,
        total_area: totalArea,
        area_unit: fd.get('area_unit') || 'acres',
        soil_type: fd.get('soil_type') || null,
        field_name: fd.get('field_name')?.trim() || null,
        crop_name: fd.get('crop_name')?.trim() || null
      };

      try {
        const idempKey = generateIdempotencyKey();
        const res = await apiCall('/auth/register', 'POST', payload, { 'Idempotency-Key': idempKey });
        state.token = res.token;
        state.farmer = res.farmer;
        localStorage.setItem('agritrace_token', res.token);
        formReg.reset();
        updateUserUI();
        showToast('Farm registered successfully', 'success');
        window.location.hash = '#/home';
      } catch (err) {
        showToast(err.message, 'error');
      } finally {
        btn.disabled = false;
        btn.textContent = 'Save my farm';
      }
    });
  }

  // Voice Input Setup
  setupVoiceInput();

  // Photo Compression Setup
  setupPhotoInput();

  // Investigation Form Submission
  const formInv = document.getElementById('form-investigate');
  if (formInv) {
    formInv.addEventListener('submit', async (e) => {
      e.preventDefault();
      const btn = document.getElementById('btn-submit-investigate');
      const progressBox = document.getElementById('ask-progress-status');
      
      btn.disabled = true;
      btn.textContent = 'Analyzing...';
      if (progressBox) progressBox.style.display = 'block';

      const fd = new FormData(formInv);
      const fieldId = fd.get('field_id');
      const query = fd.get('user_query').trim();

      const newMeasurements = {};
      const moisture = fd.get('soil_moisture');
      if (moisture) newMeasurements['soil_moisture'] = parseFloat(moisture);
      const n = fd.get('N');
      if (n) newMeasurements['N'] = parseFloat(n);

      try {
        const idempKey = generateIdempotencyKey();
        const res = await apiCall('/investigations', 'POST', {
          field_id: fieldId,
          user_query: query,
          new_measurements: Object.keys(newMeasurements).length > 0 ? newMeasurements : null,
          location_data: state.pendingInvestigationContext?.location_data || null,
          weather_data: state.pendingInvestigationContext?.weather_data || null
        }, { 'Idempotency-Key': idempKey });

        const invResult = res.investigation;

        // If user compressed a photo, upload it and link to investigation
        if (state.compressedPhotoBlob) {
          try {
            const photoFd = new FormData();
            photoFd.append('file', state.compressedPhotoBlob, 'field_photo.jpg');
            photoFd.append('field_id', fieldId);
            photoFd.append('investigation_id', invResult.investigation_id);
            await apiCall('/observations/upload-evidence', 'POST', photoFd);
          } catch (uploadErr) {
            console.warn('Photo upload warning:', uploadErr);
          }
          state.compressedPhotoBlob = null;
        }

        formInv.reset();
        const preview = document.getElementById('ask-photo-preview');
        if (preview) preview.style.display = 'none';

        showToast('Investigation ready', 'success');
        state.activeInvestigation = invResult;
        window.location.hash = `#/results/${invResult.investigation_id}`;
      } catch (err) {
        showToast(err.message, 'error');
      } finally {
        btn.disabled = false;
        btn.textContent = 'Start Investigation';
        if (progressBox) progressBox.style.display = 'none';
      }
    });
  }

  // Farm Modal Form
  const formFarm = document.getElementById('form-modal-farm');
  if (formFarm) {
    formFarm.addEventListener('submit', async (e) => {
      e.preventDefault();
      const btn = document.getElementById('btn-submit-add-farm');
      btn.disabled = true;
      const fd = new FormData(formFarm);
      try {
        await apiCall('/farms', 'POST', {
          name: fd.get('name').trim(),
          total_area: parseFloat(fd.get('total_area')),
          area_unit: fd.get('area_unit'),
          soil_type: fd.get('soil_type')
        }, { 'Idempotency-Key': generateIdempotencyKey() });
        closeModal('modal-add-farm');
        formFarm.reset();
        showToast('Farm created', 'success');
        loadMyFarmData();
      } catch (err) {
        showToast(err.message, 'error');
      } finally {
        btn.disabled = false;
      }
    });
  }

  // Field Modal Form
  const formField = document.getElementById('form-modal-field');
  if (formField) {
    formField.addEventListener('submit', async (e) => {
      e.preventDefault();
      const btn = document.getElementById('btn-submit-add-field');
      btn.disabled = true;
      const fd = new FormData(formField);
      const farmId = fd.get('farm_id');
      try {
        await apiCall(`/farms/${farmId}/fields`, 'POST', {
          name: fd.get('name').trim(),
          area: parseFloat(fd.get('area')),
          area_unit: fd.get('area_unit'),
          soil_type: fd.get('soil_type')
        }, { 'Idempotency-Key': generateIdempotencyKey() });
        closeModal('modal-add-field');
        formField.reset();
        showToast('Field added', 'success');
        loadMyFarmData();
      } catch (err) {
        showToast(err.message, 'error');
      } finally {
        btn.disabled = false;
      }
    });
  }

  // Crop Modal Form
  const formCrop = document.getElementById('form-modal-crop');
  if (formCrop) {
    formCrop.addEventListener('submit', async (e) => {
      e.preventDefault();
      const btn = document.getElementById('btn-submit-add-crop');
      btn.disabled = true;
      const fd = new FormData(formCrop);
      const fieldId = fd.get('field_id');
      try {
        await apiCall(`/farms/fields/${fieldId}/crop-cycles`, 'POST', {
          crop_name: fd.get('crop_name').trim(),
          variety: fd.get('variety')?.trim() || '',
          growth_stage: fd.get('growth_stage')
        }, { 'Idempotency-Key': generateIdempotencyKey() });
        closeModal('modal-add-crop');
        formCrop.reset();
        showToast('Crop cycle registered', 'success');
        loadMyFarmData();
      } catch (err) {
        showToast(err.message, 'error');
      } finally {
        btn.disabled = false;
      }
    });
  }

  // Reading Modal Form
  const formReading = document.getElementById('form-modal-reading');
  if (formReading) {
    formReading.addEventListener('submit', async (e) => {
      e.preventDefault();
      const btn = document.getElementById('btn-submit-add-reading');
      btn.disabled = true;
      const fd = new FormData(formReading);
      const fieldId = fd.get('field_id');
      const dataPayload = {};
      const moisture = fd.get('soil_moisture');
      if (moisture) dataPayload['soil_moisture'] = parseFloat(moisture);
      const n = fd.get('N');
      if (n) dataPayload['N'] = parseFloat(n);
      const ph = fd.get('ph');
      if (ph) dataPayload['ph'] = parseFloat(ph);

      try {
        await apiCall('/observations', 'POST', {
          field_id: fieldId,
          observation_type: 'soil_reading',
          source: 'farmer_reported',
          data: dataPayload
        }, { 'Idempotency-Key': generateIdempotencyKey() });
        closeModal('modal-add-reading');
        formReading.reset();
        showToast('Reading logged', 'success');
        loadMyFarmData();
      } catch (err) {
        showToast(err.message, 'error');
      } finally {
        btn.disabled = false;
      }
    });
  }
}

// Voice Input Helper (where browser SpeechRecognition is available)
function setupVoiceInput() {
  const btn = document.getElementById('btn-voice-input');
  const queryInput = document.getElementById('ask-query');
  if (!btn || !queryInput) return;

  const SpeechRecognition = window.SpeechRecognition || window.webkitSpeechRecognition;
  if (!SpeechRecognition) {
    btn.style.display = 'none';
    return;
  }

  const recognizer = new SpeechRecognition();
  recognizer.continuous = false;
  recognizer.interimResults = false;
  recognizer.lang = state.lang === 'hi' ? 'hi-IN' : (state.lang === 'or' ? 'or-IN' : 'en-IN');

  btn.addEventListener('click', () => {
    btn.textContent = 'Listening...';
    recognizer.start();
  });

  recognizer.onresult = (event) => {
    const transcript = event.results[0][0].transcript;
    queryInput.value = (queryInput.value ? queryInput.value + ' ' : '') + transcript;
    btn.innerHTML = '<span class="material-symbols-outlined" style="font-size: 18px;">mic</span><span>Voice Input</span>';
  };

  recognizer.onerror = () => {
    btn.innerHTML = '<span class="material-symbols-outlined" style="font-size: 18px;">mic</span><span>Voice Input</span>';
  };

  recognizer.onend = () => {
    btn.innerHTML = '<span class="material-symbols-outlined" style="font-size: 18px;">mic</span><span>Voice Input</span>';
  };
}

// Client-Side Image Compression (< 1MB)
function setupPhotoInput() {
  const fileInput = document.getElementById('ask-photo');
  const previewBox = document.getElementById('ask-photo-preview');
  const previewImg = document.getElementById('ask-photo-preview-img');
  if (!fileInput) return;

  fileInput.addEventListener('change', async () => {
    const file = fileInput.files[0];
    if (!file) {
      state.compressedPhotoBlob = null;
      if (previewBox) previewBox.style.display = 'none';
      return;
    }

    try {
      const bitmap = await createImageBitmap(file);
      const maxDim = 1200;
      let width = bitmap.width;
      let height = bitmap.height;

      if (width > maxDim || height > maxDim) {
        if (width > height) {
          height = Math.round((height * maxDim) / width);
          width = maxDim;
        } else {
          width = Math.round((width * maxDim) / height);
          height = maxDim;
        }
      }

      const canvas = document.createElement('canvas');
      canvas.width = width;
      canvas.height = height;
      const ctx = canvas.getContext('2d');
      ctx.drawImage(bitmap, 0, 0, width, height);

      canvas.toBlob((blob) => {
        state.compressedPhotoBlob = blob;
        if (previewImg && previewBox) {
          previewImg.src = URL.createObjectURL(blob);
          previewBox.style.display = 'block';
        }
      }, 'image/jpeg', 0.8);
    } catch (err) {
      console.warn('Image processing fallback:', err);
      state.compressedPhotoBlob = file;
    }
  });
}

// SCREEN 1: Load Home Data
async function loadHomeData() {
  if (!state.farmer) return;
  const greeting = document.getElementById('home-greeting');
  if (greeting) greeting.textContent = `Welcome, ${esc(state.farmer.display_name)}`;

  try {
    const farmsRes = await apiCall('/farms?include=fields');
    state.farms = farmsRes.farms || [];
    
    let totalFields = 0;
    state.farms.forEach(f => { totalFields += (f.fields || []).length; });

    document.getElementById('stat-farms-count').textContent = state.farms.length;
    document.getElementById('stat-fields-count').textContent = totalFields;

    const obsRes = await apiCall('/observations');
    document.getElementById('stat-readings-count').textContent = (obsRes.observations || []).length;

    const invRes = await apiCall('/investigations');
    state.investigations = invRes.investigations || [];
    document.getElementById('stat-inv-count').textContent = state.investigations.length;

    const recentContainer = document.getElementById('home-recent-list');
    if (recentContainer) {
      if (state.investigations.length === 0) {
        recentContainer.innerHTML = '<p class="helper-text">No investigations run yet. When you notice a crop issue, click "Ask about a problem".</p>';
      } else {
        recentContainer.innerHTML = state.investigations.slice(0, 3).map(inv => `
          <div style="border-bottom: 1px solid var(--border); padding: 0.75rem 0; display: flex; justify-content: space-between; align-items: center;">
            <div>
              <strong style="color: var(--text-main); font-size: 0.95rem;">${esc(inv.title)}</strong>
              <div class="helper-text">${esc(inv.crop_name || 'Crop')} • ${esc(inv.field_name || 'Field')} • ${new Date(inv.created_at).toLocaleDateString()}</div>
            </div>
            <a class="btn btn-secondary" style="min-height: 36px; padding: 0.2rem 0.65rem; font-size: 0.85rem;" href="#/results/${esc(inv.id)}">Open</a>
          </div>
        `).join('');
      }
    }
  } catch (err) {
    console.warn('Dashboard load warning:', err);
  }
}

// SCREEN 2: Load Ask Fields Dropdown
async function loadAskFields() {
  const select = document.getElementById('ask-field-id');
  if (!select) return;

  try {
    const res = await apiCall('/farms?include=fields');
    select.innerHTML = '<option value="">Select a field...</option>';
    let count = 0;
    (res.farms || []).forEach(farm => {
      (farm.fields || []).forEach(fld => {
        count++;
        const opt = document.createElement('option');
        opt.value = fld.id;
        const cropName = fld.crop_cycles && fld.crop_cycles.length > 0 ? fld.crop_cycles[0].crop_name : 'No active crop';
        opt.textContent = `${fld.name} (${cropName} • ${farm.name})`;
        select.appendChild(opt);
      });
    });

    if (count === 0) {
      select.innerHTML = '<option value="">No fields found. Please add a field in My Farm first.</option>';
    }
  } catch (err) {
    select.innerHTML = '<option value="">Failed to load fields</option>';
  }
}

// SCREEN 3: Load My Farm Data
async function loadMyFarmData() {
  const container = document.getElementById('farms-list-container');
  if (!container) return;

  try {
    const res = await apiCall('/farms?include=fields');
    state.farms = res.farms || [];

    if (state.farms.length === 0) {
      container.innerHTML = '<p class="helper-text">No farms registered yet. Click "Add Farm" to set up your land.</p>';
      return;
    }

    container.innerHTML = state.farms.map(farm => `
      <div class="card">
        <div style="display: flex; justify-content: space-between; align-items: flex-start; margin-bottom: 0.75rem;">
          <div>
            <h2 style="font-size: 1.15rem; font-weight: 700; color: var(--text-main);">${esc(farm.name)}</h2>
            <span class="helper-text">${esc(farm.total_area)} ${esc(farm.area_unit || 'acres')} • Soil: ${esc(farm.soil_type || 'Standard')}</span>
          </div>
          <button class="btn btn-secondary" style="min-height: 36px; padding: 0.2rem 0.65rem; font-size: 0.85rem;" onclick="openAddFieldModal('${esc(farm.id)}')">
            <span class="material-symbols-outlined" style="font-size: 16px;">add</span>
            <span>Add Field</span>
          </button>
        </div>

        <div style="border-top: 1px solid var(--border); padding-top: 0.75rem;">
          <h4 style="font-size: 0.95rem; font-weight: 600; margin-bottom: 0.5rem;">Cultivated Fields / Parcels</h4>
          ${(farm.fields && farm.fields.length > 0) ? `
            <div style="display: flex; flex-direction: column; gap: 0.5rem;">
              ${farm.fields.map(fld => `
                <div style="background: var(--bg-muted); padding: 0.75rem; border-radius: var(--radius-sm); display: flex; justify-content: space-between; align-items: center; flex-wrap: wrap; gap: 0.5rem;">
                  <div>
                    <strong style="font-size: 0.95rem;">${esc(fld.name)}</strong>
                    <span class="helper-text">(${esc(fld.area)} ${esc(fld.area_unit || 'acres')})</span>
                    <div style="font-size: 0.85rem; color: var(--text-muted); margin-top: 0.2rem;">
                      Crop: ${(fld.crop_cycles && fld.crop_cycles.length > 0) ? esc(fld.crop_cycles[0].crop_name) : '<em style="color: var(--danger);">No active crop registered</em>'}
                    </div>
                  </div>
                  <button class="btn btn-secondary" style="min-height: 32px; padding: 0.15rem 0.5rem; font-size: 0.8rem;" onclick="openAddCropModal('${esc(fld.id)}')">
                    + Add Crop
                  </button>
                </div>
              `).join('')}
            </div>
          ` : '<p class="helper-text">No fields added to this farm yet.</p>'}
        </div>
      </div>
    `).join('');
  } catch (err) {
    container.innerHTML = `<p class="error-text">Failed to load farm details: ${esc(err.message)}</p>`;
  }
}

// SCREEN 4: Results Controller
function setResultsSubTab(subTab) {
  const wsView = document.getElementById('results-workspace-view');
  const histView = document.getElementById('results-history-view');
  const timeView = document.getElementById('results-timeline-view');

  const btnWs = document.getElementById('btn-sub-workspace');
  const btnHist = document.getElementById('btn-sub-history');
  const btnTime = document.getElementById('btn-sub-timeline');

  if (wsView) wsView.style.display = subTab === 'workspace' ? 'block' : 'none';
  if (histView) histView.style.display = subTab === 'history' ? 'block' : 'none';
  if (timeView) timeView.style.display = subTab === 'timeline' ? 'block' : 'none';

  btnWs.className = subTab === 'workspace' ? 'btn btn-primary' : 'btn btn-secondary';
  btnHist.className = subTab === 'history' ? 'btn btn-primary' : 'btn btn-secondary';
  btnTime.className = subTab === 'timeline' ? 'btn btn-primary' : 'btn btn-secondary';

  if (subTab === 'history') loadResultsList();
  if (subTab === 'timeline') loadResultsTimeline();
}

async function loadResultsList() {
  const container = document.getElementById('history-items-container');
  if (!container) return;

  try {
    const res = await apiCall('/investigations');
    const items = res.investigations || [];

    if (items.length === 0) {
      container.innerHTML = '<p class="helper-text">No previous investigations recorded yet.</p>';
      return;
    }

    container.innerHTML = items.map(inv => `
      <div style="border-bottom: 1px solid var(--border); padding: 0.85rem 0; display: flex; justify-content: space-between; align-items: center; flex-wrap: wrap; gap: 0.5rem;">
        <div>
          <h3 style="font-size: 1rem; font-weight: 700; color: var(--text-main);">${esc(inv.title)}</h3>
          <div class="helper-text" style="margin-top: 0.2rem;">
            ${esc(inv.crop_name || 'Crop')} • Field: ${esc(inv.field_name || 'Field')} • Date: ${new Date(inv.created_at).toLocaleDateString()}
          </div>
          ${inv.finding_summary ? `<p style="font-size: 0.85rem; color: var(--text-muted); margin-top: 0.35rem;">Finding: ${esc(inv.finding_summary)}</p>` : ''}
        </div>
        <button class="btn btn-secondary" onclick="openInvestigationDetail('${esc(inv.id)}')">Open Result</button>
      </div>
    `).join('');
  } catch (err) {
    container.innerHTML = `<p class="error-text">Failed to load history: ${esc(err.message)}</p>`;
  }
}

async function openInvestigationDetail(invId) {
  setResultsSubTab('workspace');
  const emptyState = document.getElementById('ws-empty-state');
  const content = document.getElementById('ws-content-container');

  try {
    const res = await apiCall(`/investigations/${invId}`);
    const inv = res.investigation;
    inv.finding = res.finding || {};
    inv.hypotheses = res.hypotheses || [];
    state.activeInvestigation = inv;

    if (emptyState) emptyState.style.display = 'none';
    if (content) content.style.display = 'block';

    renderInvestigationWorkspace(inv);
  } catch (err) {
    showToast(err.message, 'error');
    if (emptyState) emptyState.style.display = 'block';
    if (content) content.style.display = 'none';
  }
}

function renderInvestigationWorkspace(inv) {
  const fieldName = inv.field?.name || 'Field';
  const cropName = inv.crop_cycle?.crop_name || 'Crop';
  
  // Header Meta
  document.getElementById('ws-meta-field-crop').textContent = `${cropName} • Field: ${fieldName}`;
  
  // Location and Weather Tag Badges
  const locBadge = document.getElementById('ws-tag-location');
  if (locBadge) {
    if (inv.location_tag) {
      locBadge.textContent = inv.location_tag;
      locBadge.style.display = 'inline-block';
    } else {
      locBadge.style.display = 'none';
    }
  }

  const weatherBadge = document.getElementById('ws-tag-weather');
  if (weatherBadge) {
    if (inv.weather_tag) {
      weatherBadge.textContent = inv.weather_tag;
      weatherBadge.style.display = 'inline-block';
    } else {
      weatherBadge.style.display = 'none';
    }
  }

  document.getElementById('ws-summary-title').textContent = inv.finding?.diagnosis || inv.summary || 'Investigation Findings';
  document.getElementById('ws-farmer-problem').textContent = `Problem reported: “${inv.user_query || ''}”`;

  // Evidence Strength Badge
  const strengthContainer = document.getElementById('ws-evidence-strength-container');
  if (strengthContainer) {
    strengthContainer.innerHTML = renderEvidenceStrength(inv.finding?.evidence_strength);
  }

  // What We Checked
  const checkedList = document.getElementById('ws-checked-list');
  const checkedItems = [];
  if (inv.historical_observations_retrieved > 0 || (inv.historical_observations && inv.historical_observations.length > 0)) {
    checkedItems.push('Earlier field measurement history');
  }
  checkedItems.push(`Crop lifecycle and stage for ${cropName}`);
  if (inv.field?.soil_type) checkedItems.push(`Soil classification (${inv.field.soil_type})`);
  checkedItems.push('Agronomic rule and symptom matching');
  checkedList.innerHTML = checkedItems.map(i => `<li>${esc(i)}</li>`).join('');

  // What We Could Not Check
  const uncheckedList = document.getElementById('ws-unchecked-list');
  const uncertainties = inv.finding?.uncertainties || [
    'Laboratory soil mineral assay (requires physical sample)',
    'Microscopic fungal spore culture'
  ];
  uncheckedList.innerHTML = uncertainties.map(u => `<li>${esc(u)}</li>`).join('');

  // Hypotheses
  const hypList = document.getElementById('ws-hypotheses-list');
  const hypotheses = inv.hypotheses || [];
  if (hypotheses.length === 0) {
    hypList.innerHTML = '<p class="helper-text">No specific hypotheses recorded.</p>';
  } else {
    hypList.innerHTML = hypotheses.map(h => `
      <div style="background: var(--bg-muted); padding: 0.85rem; border-radius: var(--radius-sm); border-left: 4px solid ${h.status === 'supported' ? 'var(--primary)' : 'var(--border)'};">
        <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 0.35rem;">
          <strong style="font-size: 0.95rem;">${esc(h.title)}</strong>
          ${renderEvidenceStrength(h.evidence_strength)}
        </div>
        <p style="font-size: 0.9rem; color: var(--text-muted);">${esc(h.description)}</p>
        ${(h.supporting_evidence && h.supporting_evidence.length > 0) ? `
          <div style="margin-top: 0.5rem; font-size: 0.85rem; color: var(--text-main);">
            <strong>Facts checked:</strong> ${esc(h.supporting_evidence.join(', '))}
          </div>
        ` : ''}
      </div>
    `).join('');
  }

  // Checklist Actions (Nothing ticked by default, persistent per user in localStorage)
  const actionsList = document.getElementById('ws-actions-checklist');
  const acts = inv.finding?.recommended_actions || [
    'Monitor crop conditions daily over next 48 hours.',
    'Contact your nearest KVK or the Kisan Call Centre (1800-180-1551) before spraying.'
  ];
  
  const savedChecks = JSON.parse(localStorage.getItem(`checks_${inv.id || inv.investigation_id}`) || '{}');

  actionsList.innerHTML = acts.map((act, idx) => {
    const isChecked = savedChecks[idx] ? 'checked' : '';
    return `
      <label class="checklist-item">
        <input type="checkbox" ${isChecked} onchange="toggleActionCheck('${esc(inv.id || inv.investigation_id)}', ${idx}, this.checked)"/>
        <div style="font-size: 0.95rem;">
          <div>${esc(act)}</div>
          <div class="helper-text">Recommended agronomic guidance</div>
        </div>
      </label>
    `;
  }).join('');

  // Real Technical Trace Accordion
  const traceBox = document.getElementById('ws-tech-trace');
  if (traceBox) {
    const trace = inv.execution_trace || [];
    if (trace.length === 0) {
      traceBox.textContent = 'Trace recorded in database.';
    } else {
      traceBox.innerHTML = trace.map(t => `
        <div style="margin-bottom: 0.4rem;">
          <strong>[+${t.timestamp_ms || 0}ms] ${esc(t.step)}:</strong> ${esc(t.description)}
        </div>
      `).join('');
    }
  }

  // Populate graph fallback text
  const fallbackBox = document.getElementById('ws-graph-text-fallback');
  if (fallbackBox) {
    fallbackBox.innerHTML = `
      <div><strong>Connected Entities:</strong> Farmer (${esc(state.farmer?.display_name)}) &rarr; Field (${esc(fieldName)}) &rarr; Crop (${esc(cropName)})</div>
    `;
  }
}

function toggleActionCheck(invId, index, isChecked) {
  const key = `checks_${invId}`;
  const saved = JSON.parse(localStorage.getItem(key) || '{}');
  saved[index] = isChecked;
  localStorage.setItem(key, JSON.stringify(saved));
}

// ==========================================
// 📍 LOCATION & LIVE WEATHER CONTEXT HELPERS
// ==========================================

window.detectRegistrationLocation = async function() {
  const statusEl = document.getElementById('reg-loc-status');
  const btn = document.getElementById('btn-detect-reg-location');
  if (!navigator.geolocation) {
    if (statusEl) statusEl.textContent = 'Geolocation not supported by this browser.';
    return;
  }

  if (statusEl) statusEl.textContent = 'Detecting GPS coordinates...';
  if (btn) btn.disabled = true;

  navigator.geolocation.getCurrentPosition(
    async (pos) => {
      const lat = pos.coords.latitude;
      const lon = pos.coords.longitude;
      const latInput = document.getElementById('reg-latitude');
      const lonInput = document.getElementById('reg-longitude');
      if (latInput) latInput.value = lat;
      if (lonInput) lonInput.value = lon;

      if (statusEl) statusEl.textContent = `GPS: (${lat.toFixed(4)}, ${lon.toFixed(4)}). Finding village / district...`;

      try {
        const res = await apiCall(`/auth/reverse-geocode?lat=${lat}&lon=${lon}`);
        if (res.village) {
          const vInput = document.getElementById('reg-village');
          if (vInput) vInput.value = res.village;
        }
        if (res.district) {
          const dInput = document.getElementById('reg-district');
          if (dInput) dInput.value = res.district;
        }
        if (res.state) {
          const sInput = document.getElementById('reg-state');
          if (sInput) sInput.value = res.state;
        }
        if (statusEl) {
          const parts = [res.village, res.district, res.state].filter(Boolean);
          statusEl.textContent = `📍 Located: ${parts.join(', ')}`;
          statusEl.style.color = 'var(--primary)';
        }
      } catch (err) {
        if (statusEl) statusEl.textContent = `GPS coordinates saved (${lat.toFixed(4)}, ${lon.toFixed(4)}).`;
      } finally {
        if (btn) btn.disabled = false;
      }
    },
    (err) => {
      if (statusEl) {
        statusEl.textContent = 'Location permission denied. You can enter district/state manually.';
        statusEl.style.color = 'var(--text-muted)';
      }
      if (btn) btn.disabled = false;
    },
    { timeout: 8000 }
  );
};

window.refreshAskContext = async function() {
  const locEl = document.getElementById('ask-context-loc');
  const weatherEl = document.getElementById('ask-context-weather');
  const btn = document.getElementById('btn-refresh-ask-weather');
  if (btn) btn.disabled = true;

  if (locEl) locEl.textContent = '📍 Detecting farm location...';
  if (weatherEl) weatherEl.textContent = '⛅ Checking live weather...';

  let lat = state.farmer?.latitude || null;
  let lon = state.farmer?.longitude || null;
  let village = state.farmer?.village || '';
  let district = state.farmer?.district || '';
  let stateName = state.farmer?.state || '';

  async function applyWeather(targetLat, targetLon, locName) {
    if (locEl) locEl.textContent = `📍 ${locName || 'Farm Location'}`;
    let weatherObj = null;

    if (targetLat && targetLon) {
      try {
        const url = `https://api.open-meteo.com/v1/forecast?latitude=${targetLat}&longitude=${targetLon}&current=temperature_2m,relative_humidity_2m,precipitation,weather_code,wind_speed_10m`;
        const resp = await fetch(url);
        if (resp.ok) {
          const wdata = await resp.json();
          const cur = wdata.current || {};
          const code = cur.weather_code || 0;
          const cond = WMO_WEATHER_MAP[code] || 'Clear';
          weatherObj = {
            temperature: cur.temperature_2m,
            humidity: cur.relative_humidity_2m,
            precipitation: cur.precipitation || 0.0,
            wind_speed: cur.wind_speed_10m || 0.0,
            condition: cond
          };
          if (weatherEl) {
            weatherEl.textContent = `⛅ ${cur.temperature_2m}°C · ${cur.relative_humidity_2m}% RH · ${cond}` + (cur.precipitation > 0 ? ` · Rain ${cur.precipitation}mm` : '');
            weatherEl.style.color = '#15803d';
          }
        }
      } catch (err) {
        console.warn('Weather fetch warning:', err);
        if (weatherEl) weatherEl.textContent = '⛅ Weather will be retrieved on server';
      }
    } else {
      if (weatherEl) weatherEl.textContent = '⛅ Weather will be retrieved on server';
    }

    state.pendingInvestigationContext = {
      location_data: {
        village,
        district,
        state: stateName,
        latitude: targetLat,
        longitude: targetLon
      },
      weather_data: weatherObj
    };
    if (btn) btn.disabled = false;
  }

  if (lat && lon) {
    const locParts = [village, district, stateName].filter(Boolean);
    await applyWeather(lat, lon, locParts.join(', '));
    return;
  }

  if (navigator.geolocation) {
    navigator.geolocation.getCurrentPosition(
      async (pos) => {
        lat = pos.coords.latitude;
        lon = pos.coords.longitude;
        try {
          const geoRes = await apiCall(`/auth/reverse-geocode?lat=${lat}&lon=${lon}`);
          village = geoRes.village || village;
          district = geoRes.district || district;
          stateName = geoRes.state || stateName;
        } catch (e) {}
        const locParts = [village, district, stateName].filter(Boolean);
        await applyWeather(lat, lon, locParts.join(', ') || `GPS (${lat.toFixed(2)}, ${lon.toFixed(2)})`);
      },
      async () => {
        const locParts = [village, district, stateName].filter(Boolean);
        await applyWeather(lat, lon, locParts.join(', ') || 'Farm Location');
      },
      { timeout: 6000 }
    );
  } else {
    const locParts = [village, district, stateName].filter(Boolean);
    await applyWeather(lat, lon, locParts.join(', ') || 'Farm Location');
  }
};

// ==========================================
// 🕸️ UNIFIED ACCOUNT KNOWLEDGE GRAPH ENGINE
// One Persistent Living Graph per Account
// ==========================================

const NODE_CONFIG = {
  Farmer: { color: '#15803d', radius: 24, icon: '👤' },
  Farm: { color: '#16a34a', radius: 20, icon: '🏡' },
  Field: { color: '#4d7c0f', radius: 18, icon: '🌱' },
  CropCycle: { color: '#0d9488', radius: 18, icon: '🌾' },
  Investigation: { color: '#ea580c', radius: 19, icon: '🔬' },
  Finding: { color: '#7c3aed', radius: 16, icon: '📋' },
  Hypothesis: { color: '#db2777', radius: 15, icon: '💡' },
  Observation: { color: '#0891b2', radius: 15, icon: '📊' },
  Weather: { color: '#0284c7', radius: 17, icon: '⛅' },
  Location: { color: '#4f46e5', radius: 17, icon: '📍' }
};

window.filterGraphNodes = function(filter) {
  state.graphFilter = filter;
  document.querySelectorAll('.graph-chip').forEach(c => {
    if (c.getAttribute('data-filter') === filter) {
      c.classList.add('active');
    } else {
      c.classList.remove('active');
    }
  });
};

window.zoomGraph = function(factor) {
  state.graphTransform.scale = Math.max(0.3, Math.min(4.0, state.graphTransform.scale * factor));
};

window.resetGraphView = function() {
  state.graphTransform = { x: 0, y: 0, scale: 1 };
  if (state.graphNodes) {
    state.graphNodes.forEach(n => { n.pinned = false; });
  }
};

window.toggleGraphPhysics = function() {
  state.graphPhysics = !state.graphPhysics;
  const icon = document.getElementById('icon-toggle-physics');
  if (icon) {
    icon.textContent = state.graphPhysics ? 'pause' : 'play_arrow';
  }
};

window.closeGraphInspector = function() {
  state.selectedGraphNode = null;
  const panel = document.getElementById('graph-inspector-panel');
  if (panel) panel.style.display = 'none';
};

window.openGraphInspector = function(node) {
  state.selectedGraphNode = node;
  const panel = document.getElementById('graph-inspector-panel');
  if (!panel) return;

  const badge = document.getElementById('inspector-badge');
  const title = document.getElementById('inspector-title');
  const propsEl = document.getElementById('inspector-properties');
  const relsEl = document.getElementById('inspector-relationships');

  const conf = NODE_CONFIG[node.label] || { color: '#64748b', icon: '●' };
  if (badge) {
    badge.textContent = `${conf.icon} ${node.label}`;
    badge.style.background = conf.color;
    badge.style.color = '#ffffff';
  }

  const nodeName = node.properties?.tag || node.properties?.name || node.properties?.crop_name || node.properties?.title || node.properties?.summary || node.id;
  if (title) title.textContent = nodeName;

  // Format properties
  const props = node.properties || {};
  let propLines = [];
  for (const [k, v] of Object.entries(props)) {
    if (['id', 'farmer_id', 'field_id', 'farm_id', 'crop_cycle_id', 'investigation_id'].includes(k)) continue;
    if (v !== null && v !== undefined && v !== '') {
      propLines.push(`<div><strong>${esc(k)}:</strong> ${esc(String(v))}</div>`);
    }
  }
  if (propsEl) {
    propsEl.innerHTML = propLines.length > 0 ? propLines.join('') : '<span style="color: var(--text-muted);">No extra attributes</span>';
  }

  // Format connected edges
  const connectedEdges = state.graphEdges.filter(e => e.from === node.id || e.to === node.id);
  let relLines = [];
  connectedEdges.forEach(e => {
    const isOut = e.from === node.id;
    const otherId = isOut ? e.to : e.from;
    const otherNode = state.graphNodes.find(n => n.id === otherId);
    const otherLabel = otherNode?.properties?.name || otherNode?.properties?.tag || otherNode?.properties?.crop_name || otherNode?.label || otherId;
    relLines.push(`<div>${isOut ? '→' : '←'} <em>${esc(e.label)}</em>: <strong>${esc(otherLabel)}</strong></div>`);
  });

  if (relsEl) {
    relsEl.innerHTML = relLines.length > 0 ? `<strong>Connected (${relLines.length}):</strong>${relLines.join('')}` : 'No connections';
  }

  panel.style.display = 'block';
};

// Toggle graph inside Result pane
function toggleResultGraphView() {
  const panel = document.getElementById('ws-graph-panel');
  const btn = document.getElementById('btn-toggle-graph-view');
  if (!panel || !btn) return;

  if (panel.style.display === 'none') {
    panel.style.display = 'block';
    btn.textContent = 'Hide Graph';
    const invId = state.activeInvestigation?.id || state.activeInvestigation?.investigation_id;
    loadUnifiedGraph(invId, 'result-graph-canvas');
  } else {
    panel.style.display = 'none';
    btn.textContent = 'Show Graph';
    if (state.graphAnimId) {
      cancelAnimationFrame(state.graphAnimId);
      state.graphAnimId = null;
    }
  }
}

// Master Unified Graph Loader
window.loadUnifiedGraph = async function(highlightNodeId = null, targetCanvasId = null) {
  const canvasId = targetCanvasId || (state.activePane === 'results' ? 'result-graph-canvas' : 'studio-graph-canvas');
  const canvas = document.getElementById(canvasId);
  if (!canvas) return;

  const rect = canvas.getBoundingClientRect();
  const dpr = window.devicePixelRatio || 1;
  const w = rect.width || 700;
  const h = rect.height || 480;
  canvas.width = w * dpr;
  canvas.height = h * dpr;

  const ctx = canvas.getContext('2d');
  ctx.scale(dpr, dpr);

  try {
    // Depth 5 spans the entire account's unified graph!
    const res = await apiCall('/graph/subgraph?depth=5');
    const sub = res.subgraph || {};
    const rawNodes = sub.nodes || [];
    const rawEdges = sub.edges || [];

    // Map existing coordinates so existing nodes don't jump on updates
    const prevPos = new Map();
    state.graphNodes.forEach(n => {
      prevPos.set(n.id, { x: n.x, y: n.y, vx: n.vx, vy: n.vy, pinned: n.pinned });
    });

    state.graphNodes = rawNodes.map((n, i) => {
      if (prevPos.has(n.id)) {
        const p = prevPos.get(n.id);
        return { ...n, x: p.x, y: p.y, vx: p.vx || 0, vy: p.vy || 0, pinned: p.pinned || false };
      }
      // New nodes: spread organically around center
      const angle = (i / Math.max(rawNodes.length, 1)) * 2 * Math.PI;
      const radius = 120 + (i % 3) * 60;
      return {
        ...n,
        x: w / 2 + Math.cos(angle) * radius,
        y: h / 2 + Math.sin(angle) * radius,
        vx: 0,
        vy: 0,
        pinned: false
      };
    });

    state.graphEdges = rawEdges;
    state.focusedNodeId = highlightNodeId;

    // Center on focused node if provided
    if (highlightNodeId) {
      const focus = state.graphNodes.find(n => n.id === highlightNodeId);
      if (focus) {
        state.graphTransform.x = (w / 2) - focus.x;
        state.graphTransform.y = (h / 2) - focus.y;
      }
    }

    // Update text fallbacks for screen-readers
    const textHtml = `
      <ul style="padding-left: 1.2rem;">
        ${state.graphNodes.map(n => {
          const conf = NODE_CONFIG[n.label] || { icon: '●' };
          const name = n.properties?.name || n.properties?.tag || n.properties?.crop_name || n.properties?.title || n.id;
          return `<li><strong>${conf.icon} ${esc(n.label)}:</strong> ${esc(name)}</li>`;
        }).join('')}
      </ul>
    `;
    const studioFallback = document.getElementById('studio-graph-text-fallback');
    if (studioFallback) studioFallback.innerHTML = textHtml;
    const wsFallback = document.getElementById('ws-graph-text-fallback');
    if (wsFallback) wsFallback.innerHTML = textHtml;

    setupUnifiedGraphInteractions(canvas);
    startUnifiedGraphSimulation(ctx, canvas, w, h);
  } catch (err) {
    console.warn('Unified graph load error:', err);
  }
};

function setupUnifiedGraphInteractions(canvas) {
  let isPanning = false;
  let startX = 0;
  let startY = 0;
  let dragged = null;

  function getTransformedPoint(e) {
    const rect = canvas.getBoundingClientRect();
    const clientX = e.clientX || (e.touches && e.touches[0] ? e.touches[0].clientX : 0);
    const clientY = e.clientY || (e.touches && e.touches[0] ? e.touches[0].clientY : 0);
    const mouseX = clientX - rect.left;
    const mouseY = clientY - rect.top;
    const gx = (mouseX - state.graphTransform.x) / state.graphTransform.scale;
    const gy = (mouseY - state.graphTransform.y) / state.graphTransform.scale;
    return { gx, gy, clientX, clientY };
  }

  function findNodeUnder(gx, gy) {
    for (let i = state.graphNodes.length - 1; i >= 0; i--) {
      const n = state.graphNodes[i];
      if (state.graphFilter !== 'ALL' && n.label !== state.graphFilter) continue;
      const conf = NODE_CONFIG[n.label] || { radius: 16 };
      const dx = gx - n.x;
      const dy = gy - n.y;
      if (Math.sqrt(dx * dx + dy * dy) <= conf.radius + 4) {
        return n;
      }
    }
    return null;
  }

  canvas.onpointerdown = (e) => {
    const { gx, gy, clientX, clientY } = getTransformedPoint(e);
    const targetNode = findNodeUnder(gx, gy);

    if (targetNode) {
      dragged = targetNode;
      dragged.pinned = true;
      dragged.x = gx;
      dragged.y = gy;
      canvas.setPointerCapture(e.pointerId);
    } else {
      isPanning = true;
      startX = clientX - state.graphTransform.x;
      startY = clientY - state.graphTransform.y;
      canvas.setPointerCapture(e.pointerId);
    }
  };

  canvas.onpointermove = (e) => {
    const { gx, gy, clientX, clientY } = getTransformedPoint(e);

    if (dragged) {
      dragged.x = gx;
      dragged.y = gy;
      dragged.vx = 0;
      dragged.vy = 0;
      return;
    }

    if (isPanning) {
      state.graphTransform.x = clientX - startX;
      state.graphTransform.y = clientY - startY;
      return;
    }

    // Hover styling
    const hoverNode = findNodeUnder(gx, gy);
    state.hoveredGraphNode = hoverNode;
    canvas.style.cursor = hoverNode ? 'pointer' : 'grab';
  };

  canvas.onpointerup = (e) => {
    const { gx, gy } = getTransformedPoint(e);

    if (dragged) {
      // If was just a click (dragged very little), open inspector!
      openGraphInspector(dragged);
      dragged = null;
    } else if (isPanning) {
      isPanning = false;
    }
    canvas.releasePointerCapture(e.pointerId);
  };

  canvas.onwheel = (e) => {
    e.preventDefault();
    const zoomFactor = e.deltaY < 0 ? 1.1 : 0.9;
    state.graphTransform.scale = Math.max(0.3, Math.min(4.0, state.graphTransform.scale * zoomFactor));
  };
}

function startUnifiedGraphSimulation(ctx, canvas, w, h) {
  if (state.graphAnimId) cancelAnimationFrame(state.graphAnimId);

  let pulseAngle = 0;

  function stepPhysics() {
    if (!state.graphPhysics) return;
    const nodes = state.graphNodes;
    const edges = state.graphEdges;
    const k = 80;
    const repulsion = 5000;
    const cx = w / 2;
    const cy = h / 2;

    // Node Repulsion
    for (let i = 0; i < nodes.length; i++) {
      for (let j = i + 1; j < nodes.length; j++) {
        const n1 = nodes[i];
        const n2 = nodes[j];
        const dx = n2.x - n1.x;
        const dy = n2.y - n1.y;
        const dist = Math.sqrt(dx * dx + dy * dy) || 1;
        if (dist < 420) {
          const force = repulsion / (dist * dist);
          const fx = (dx / dist) * force;
          const fy = (dy / dist) * force;
          if (!n1.pinned) { n1.vx -= fx; n1.vy -= fy; }
          if (!n2.pinned) { n2.vx += fx; n2.vy += fy; }
        }
      }
    }

    // Spring Attraction along Edges
    for (let i = 0; i < edges.length; i++) {
      const e = edges[i];
      const fromNode = nodes.find(n => n.id === e.from);
      const toNode = nodes.find(n => n.id === e.to);
      if (fromNode && toNode) {
        const dx = toNode.x - fromNode.x;
        const dy = toNode.y - fromNode.y;
        const dist = Math.sqrt(dx * dx + dy * dy) || 1;
        const force = (dist - k) * 0.035;
        const fx = (dx / dist) * force;
        const fy = (dy / dist) * force;
        if (!fromNode.pinned) { fromNode.vx += fx; fromNode.vy += fy; }
        if (!toNode.pinned) { toNode.vx -= fx; toNode.vy -= fy; }
      }
    }

    // Gravity to center & Damping
    for (let i = 0; i < nodes.length; i++) {
      const n = nodes[i];
      if (n.pinned) continue;
      n.vx += (cx - n.x) * 0.012;
      n.vy += (cy - n.y) * 0.012;
      n.vx *= 0.83;
      n.vy *= 0.83;
      n.x += n.vx;
      n.y += n.vy;
    }
  }

  function draw() {
    pulseAngle += 0.05;
    stepPhysics();

    ctx.clearRect(0, 0, w, h);
    ctx.save();
    ctx.translate(state.graphTransform.x, state.graphTransform.y);
    ctx.scale(state.graphTransform.scale, state.graphTransform.scale);

    // 1. Draw Edges
    state.graphEdges.forEach(e => {
      const fromNode = state.graphNodes.find(n => n.id === e.from);
      const toNode = state.graphNodes.find(n => n.id === e.to);
      if (fromNode && toNode) {
        const isFocusedEdge = (fromNode.id === state.focusedNodeId || toNode.id === state.focusedNodeId);
        ctx.beginPath();
        ctx.moveTo(fromNode.x, fromNode.y);
        ctx.lineTo(toNode.x, toNode.y);
        ctx.strokeStyle = isFocusedEdge ? '#ea580c' : '#cbd5e1';
        ctx.lineWidth = isFocusedEdge ? 2.5 : 1.4;
        ctx.stroke();

        // Edge relationship label
        if (e.label) {
          const midX = (fromNode.x + toNode.x) / 2;
          const midY = (fromNode.y + toNode.y) / 2;
          ctx.fillStyle = isFocusedEdge ? '#c2410c' : '#64748b';
          ctx.font = '500 10px sans-serif';
          ctx.textAlign = 'center';
          ctx.fillText(e.label, midX, midY - 3);
        }
      }
    });

    // 2. Draw Nodes
    state.graphNodes.forEach(n => {
      const isFiltered = state.graphFilter !== 'ALL' && n.label !== state.graphFilter;
      const conf = NODE_CONFIG[n.label] || { color: '#64748b', radius: 16, icon: '●' };
      const radius = conf.radius;
      const isSelected = state.selectedGraphNode?.id === n.id;
      const isHovered = state.hoveredGraphNode?.id === n.id;
      const isFocused = state.focusedNodeId === n.id;

      ctx.save();
      if (isFiltered) {
        ctx.globalAlpha = 0.25;
      }

      // Golden Pulse Ring on Focused Node
      if (isFocused) {
        ctx.beginPath();
        const pulseR = radius + 6 + Math.sin(pulseAngle) * 4;
        ctx.arc(n.x, n.y, pulseR, 0, 2 * Math.PI);
        ctx.strokeStyle = 'rgba(234, 88, 12, 0.45)';
        ctx.lineWidth = 3;
        ctx.stroke();
      }

      // Selection Glow
      if (isSelected || isHovered) {
        ctx.beginPath();
        ctx.arc(n.x, n.y, radius + 4, 0, 2 * Math.PI);
        ctx.strokeStyle = '#0284c7';
        ctx.lineWidth = 2.5;
        ctx.stroke();
      }

      // Base Node Circle
      ctx.beginPath();
      ctx.arc(n.x, n.y, radius, 0, 2 * Math.PI);
      ctx.fillStyle = conf.color;
      ctx.fill();
      ctx.strokeStyle = '#ffffff';
      ctx.lineWidth = 2.5;
      ctx.stroke();

      // Node Icon inside
      ctx.fillStyle = '#ffffff';
      ctx.font = `${Math.round(radius * 0.9)}px sans-serif`;
      ctx.textAlign = 'center';
      ctx.textBaseline = 'middle';
      ctx.fillText(conf.icon, n.x, n.y);

      // Node Label Text below
      ctx.fillStyle = '#0f172a';
      ctx.font = '600 11px sans-serif';
      ctx.textAlign = 'center';
      ctx.textBaseline = 'top';
      const labelText = n.properties?.name || n.properties?.tag || n.properties?.crop_name || n.properties?.title || n.label;
      const cleanLabel = String(labelText).replace(/^📍\s*/, '').replace(/^⛅\s*/, '');
      ctx.fillText(cleanLabel.substring(0, 15), n.x, n.y + radius + 4);

      ctx.restore();
    });

    ctx.restore();

    // Check if either canvas is still in an active view before scheduling next frame
    const paneGraph = document.getElementById('pane-graph');
    const wsPanel = document.getElementById('ws-graph-panel');
    const isGraphVisible = (paneGraph && paneGraph.classList.contains('active')) ||
                           (wsPanel && wsPanel.style.display !== 'none');

    if (isGraphVisible) {
      state.graphAnimId = requestAnimationFrame(draw);
    }
  }

  draw();
}

// Timeline Loader
async function loadResultsTimeline() {
  const container = document.getElementById('timeline-container');
  if (!container) return;

  try {
    const res = await apiCall('/investigations/memory/timeline');
    const items = res.timeline || [];

    if (items.length === 0) {
      container.innerHTML = '<p class="helper-text">No timeline events recorded yet.</p>';
      return;
    }

    container.innerHTML = items.map(ev => `
      <div style="border-left: 3px solid var(--primary); padding-left: 1rem; margin-bottom: 1.25rem; position: relative;">
        <span class="helper-text">${new Date(ev.timestamp).toLocaleString()}</span>
        <h4 style="font-size: 0.95rem; font-weight: 700; color: var(--text-main); margin-top: 0.2rem;">${esc(ev.title)}</h4>
        <p style="font-size: 0.85rem; color: var(--text-muted); margin-top: 0.2rem;">${esc(ev.description)}</p>
      </div>
    `).join('');
  } catch (err) {
    container.innerHTML = `<p class="error-text">Failed to load timeline: ${esc(err.message)}</p>`;
  }
}

// SCREEN 5: Load About Data & Redacted Certificates
async function loadAboutData() {
  const certsGrid = document.getElementById('about-certs-grid');
  const dsContainer = document.getElementById('about-datasets-container');

  // Load Redacted Certificates (Lazy WebP thumbnails < 1MB total transfer)
  if (certsGrid) {
    try {
      const res = await apiCall('/observations/bareilly-certificates');
      const certs = res.certificates || [];
      certsGrid.innerHTML = certs.map(c => `
        <div class="card" style="margin-bottom: 0; padding: 0.75rem; text-align: center;">
          <img src="${c.thumbnail_url}" loading="lazy" style="width: 100%; height: 160px; object-fit: cover; border-radius: var(--radius-sm);" alt="Redacted soil health card"/>
          <strong style="font-size: 0.85rem; display: block; margin-top: 0.4rem;">${esc(c.name)}</strong>
          <span class="helper-text" style="font-size: 0.75rem;">Academic Research Use</span>
          <div style="margin-top: 0.5rem;">
            <a class="btn btn-secondary" style="min-height: 32px; padding: 0.15rem 0.5rem; font-size: 0.8rem;" href="${c.full_image_url}" target="_blank" rel="noopener">
              View Card
            </a>
          </div>
        </div>
      `).join('');
    } catch (e) {
      certsGrid.innerHTML = '<p class="helper-text">Sign in to view research certificate records.</p>';
    }
  }

  // Load Datasets Info
  if (dsContainer) {
    try {
      const dsRes = await apiCall('/datasets');
      const datasets = dsRes.datasets || {};
      dsContainer.innerHTML = Object.entries(datasets).map(([id, meta]) => `
        <div class="card" style="margin-bottom: 0.75rem; padding: 1rem;">
          <div style="display: flex; justify-content: space-between; align-items: flex-start; margin-bottom: 0.35rem;">
            <strong>${esc(meta.name)}</strong>
            <span class="provenance-chip chip-benchmark">${esc(meta.rows)} rows</span>
          </div>
          <p style="font-size: 0.85rem; color: var(--text-muted);">${esc(meta.provenance)}</p>
          <div class="helper-text" style="margin-top: 0.25rem;">License: ${esc(meta.license || 'Public reference')}</div>
        </div>
      `).join('');
    } catch (e) {
      dsContainer.innerHTML = '<p class="helper-text">Dataset catalog available to authenticated users.</p>';
    }
  }
}

// Modal Helpers
function openAddFarmModal() {
  const modal = document.getElementById('modal-add-farm');
  if (modal) modal.classList.add('active');
}

function openAddFieldModal(farmId) {
  const modal = document.getElementById('modal-add-field');
  const input = document.getElementById('field-farm-id');
  if (input) input.value = farmId;
  if (modal) modal.classList.add('active');
}

function openAddCropModal(fieldId) {
  const modal = document.getElementById('modal-add-crop');
  const input = document.getElementById('crop-field-id');
  if (input) input.value = fieldId;
  if (modal) modal.classList.add('active');
}

async function openAddReadingModal() {
  const modal = document.getElementById('modal-add-reading');
  const select = document.getElementById('reading-field-id');
  if (!modal || !select) return;

  try {
    const res = await apiCall('/farms?include=fields');
    select.innerHTML = '<option value="">Select field...</option>';
    (res.farms || []).forEach(farm => {
      (farm.fields || []).forEach(fld => {
        const opt = document.createElement('option');
        opt.value = fld.id;
        opt.textContent = `${fld.name} (${farm.name})`;
        select.appendChild(opt);
      });
    });
    modal.classList.add('active');
  } catch (e) {
    showToast('Failed to load fields for reading', 'error');
  }
}

function closeModal(modalId) {
  const modal = document.getElementById(modalId);
  if (modal) modal.classList.remove('active');
}

// Close modals on Escape key
document.addEventListener('keydown', (e) => {
  if (e.key === 'Escape') {
    document.querySelectorAll('.modal-overlay.active').forEach(m => m.classList.remove('active'));
  }
});

// Logout Handler (Clears state, DOM, and token on shared phone)
async function handleLogout(callApi = true) {
  if (callApi && state.token) {
    try {
      await apiCall('/auth/logout', 'POST');
    } catch (e) {
      // Proceed with local logout regardless of network
    }
  }

  // Clear memory state & localStorage
  state.token = null;
  state.farmer = null;
  state.farms = [];
  state.investigations = [];
  state.activeInvestigation = null;
  localStorage.removeItem('agritrace_token');

  // Clear DOM to protect privacy on shared mobile devices
  const recentList = document.getElementById('home-recent-list');
  if (recentList) recentList.innerHTML = '';
  const farmsContainer = document.getElementById('farms-list-container');
  if (farmsContainer) farmsContainer.innerHTML = '';
  const wsContent = document.getElementById('ws-content-container');
  if (wsContent) wsContent.style.display = 'none';
  const wsEmpty = document.getElementById('ws-empty-state');
  if (wsEmpty) wsEmpty.style.display = 'block';

  updateUserUI();
  showPane('onboarding');
  setAuthMode('login');
  showToast('Logged out safely', 'info');
}

function confirmDeleteAccount() {
  if (confirm('Are you sure you want to permanently delete your farm account and all records? This action cannot be undone.')) {
    handleLogout(true);
    showToast('Account data deleted from this device.', 'info');
  }
}
