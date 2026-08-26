const ParcelLedgerUI = (() => {
  function workflowStage({ documentId, confirmed, claimStatus }) {
    if (!documentId) return 'upload';
    if (claimStatus) return 'complete';
    return confirmed ? 'register' : 'review';
  }

  function renderWarnings(container, warnings, doc = document) {
    container.replaceChildren();
    (warnings || []).forEach((message) => {
      const warning = doc.createElement('div');
      warning.className = 'warning';
      warning.textContent = String(message);
      container.appendChild(warning);
    });
  }

  function renderCandidates(container, candidates, onSelect, doc = document) {
    container.replaceChildren();
    (candidates || []).forEach((candidate) => {
      const button = doc.createElement('button');
      const title = doc.createElement('strong');
      const village = doc.createElement('small');
      button.type = 'button';
      button.className = 'candidate';
      title.textContent = `Survey ${candidate.survey_number ?? '—'} / ${candidate.subdivision_number ?? '—'}`;
      village.textContent = String(candidate.village ?? 'Village unavailable');
      button.append(title, village);
      button.addEventListener('click', () => onSelect(candidate));
      container.appendChild(button);
    });
  }

  function setFeedback(element, type, message) {
    const layoutClasses = String(element.className || '')
      .split(/\s+/)
      .filter((name) => name && name !== 'feedback' && !name.startsWith('feedback--'));
    element.className = ['feedback', ...layoutClasses, type ? `feedback--${type}` : ''].filter(Boolean).join(' ');
    element.textContent = message || '';
  }

  function ocrQualityLabel(fields) {
    const confidence = fields?.confidence;
    if (confidence === null || confidence === undefined || !Number.isFinite(Number(confidence))) {
      return '— OCR quality';
    }
    return `${Math.round(Number(confidence) * 100)}% OCR quality`;
  }

  function matchLabel(status) {
    return {
      exact_match: 'Exact match',
      matched: 'Match found',
      needs_confirmation: 'Review match',
      ambiguous: 'Multiple matches',
      multiple_matches: 'Multiple matches',
      not_found: 'Not in registry',
      insufficient_data: 'Insufficient data',
    }[status] || String(status || 'Waiting').replaceAll('_', ' ');
  }

  function processingFeedback(resolution) {
    if (resolution?.status === 'insufficient_data') {
      const labels = {
        state: 'State', district: 'District', taluk: 'Taluk', village: 'Village',
        survey_number: 'Survey number', subdivision_number: 'Subdivision',
      };
      const missing = (resolution.missing_fields || []).map((field) => labels[field] || field);
      const fields = missing.length ? new Intl.ListFormat('en', { style: 'long', type: 'conjunction' }).format(missing) : 'Some fields';
      return { type: 'warning', message: `Text was read, but ${fields} could not be identified. Check these fields.` };
    }
    if (resolution?.status === 'not_found') {
      return {
        type: 'warning',
        message: 'Document fields were extracted, but this parcel is not available in the registry.',
      };
    }
    return { type: 'success', message: 'Document fields extracted. Verify them and the parcel match.' };
  }

  function parcelMapPresentation(resolution) {
    if (resolution?.parcel) return { showMap: true, emptyMessage: '' };
    return {
      showMap: false,
      emptyMessage: 'Import the official registry boundary to display this parcel.',
    };
  }

  async function ensureBrowserSession(fetchImpl, redirect) {
    const response = await fetchImpl('/api/auth/session');
    if (!response.ok) {
      if (response.status === 401) redirect('/login');
      return null;
    }
    return response.json();
  }

  return {
    ensureBrowserSession, matchLabel, ocrQualityLabel, parcelMapPresentation, processingFeedback,
    renderCandidates, renderWarnings, setFeedback, workflowStage,
  };
})();

if (typeof module !== 'undefined' && module.exports) module.exports = ParcelLedgerUI;

if (typeof document !== 'undefined') (() => {
  const state = {
    documentId: null,
    parcel: null,
    extracted: null,
    map: null,
    layers: [],
    confirmed: false,
    claimStatus: null,
    registeredClaimId: null,
  };
  const $ = (selector) => document.querySelector(selector);
  const fileInput = $('#pattaFile');
  const processButton = $('#processButton');
  const matchButton = $('#matchButton');
  const claimButton = $('#claimButton');
  const confirmParcel = $('#confirmParcel');
  const claimedLand = ClaimedLandUI.createController({
    fetchImpl: fetch,
    leaflet: L,
    doc: document,
    browserWindow: window,
    setFeedback: ParcelLedgerUI.setFeedback,
  });

  function headers(idempotencyKey) {
    const values = {};
    if (idempotencyKey) values['Idempotency-Key'] = idempotencyKey;
    return values;
  }

  async function jsonResponse(response) {
    let body;
    try { body = await response.json(); }
    catch (_) { body = {}; }
    if (!response.ok) {
      const error = new Error(body.message || body.detail || 'The request failed. Please try again.');
      error.status = response.status;
      error.reason = body.reason;
      throw error;
    }
    return body;
  }

  function showAppView(view, selectedClaimId = null) {
    const claimed = view === 'claimed';
    $('#newClaimView').hidden = claimed;
    $('#claimedLandView').hidden = !claimed;
    $('#newClaimTab').classList.toggle('active', !claimed);
    $('#claimedLandTab').classList.toggle('active', claimed);
    $('#newClaimTab').setAttribute('aria-selected', String(!claimed));
    $('#claimedLandTab').setAttribute('aria-selected', String(claimed));
    if (claimed) claimedLand.load(selectedClaimId);
  }

  function setStage(stage) {
    const activeStep = stage === 'upload' ? 1 : stage === 'review' ? 2 : 3;
    document.querySelectorAll('.step').forEach((node) => {
      const number = Number(node.dataset.step);
      const active = number === activeStep;
      const complete = stage === 'complete' || number < activeStep;
      node.classList.toggle('active', active);
      node.classList.toggle('complete', complete);
      if (active) node.setAttribute('aria-current', 'step');
      else node.removeAttribute('aria-current');
    });
  }

  function setButtonBusy(button, busy, busyLabel) {
    if (busy) {
      button.dataset.idleLabel = button.textContent;
      button.textContent = busyLabel;
      button.disabled = true;
      button.setAttribute('aria-busy', 'true');
    } else {
      button.textContent = button.dataset.idleLabel || button.textContent;
      button.removeAttribute('aria-busy');
    }
  }

  function populateFields(fields) {
    state.extracted = fields;
    Object.entries(fields).forEach(([key, value]) => {
      const input = document.querySelector(`[name="${key}"]`);
      if (input && value !== null) input.value = value;
    });
    Object.entries(fields.evidence || {}).forEach(([key, value]) => {
      const evidence = document.querySelector(`[data-evidence="${key}"]`);
      if (evidence) evidence.textContent = `OCR source: “${value}”`;
    });
    const qualityLabel = ParcelLedgerUI.ocrQualityLabel(fields);
    $('#confidenceBadge').textContent = qualityLabel;
    $('#confidenceBadge').setAttribute('aria-label', qualityLabel);
    document.querySelectorAll('.field-grid label').forEach((label) => {
      const input = label.querySelector('input');
      if (input) label.classList.toggle('low', !input.value);
    });
  }

  function formFields() {
    const raw = Object.fromEntries(new FormData($('#fieldForm')).entries());
    raw.document_area_sqm = raw.document_area_sqm ? Number(raw.document_area_sqm) : null;
    return raw;
  }

  function ensureMap() {
    if (!state.map) {
      state.map = L.map('parcelMap').setView([10.96, 79.38], 13);
      L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', {
        maxZoom: 19,
        attribution: '&copy; OpenStreetMap contributors',
      }).addTo(state.map);
    }
    window.setTimeout(() => state.map.invalidateSize(), 0);
  }

  function selectCandidate(candidate) {
    ['state', 'district', 'taluk', 'village', 'survey_number', 'subdivision_number'].forEach((name) => {
      document.querySelector(`[name="${name}"]`).value = candidate[name] ?? '';
    });
    $('#fieldForm').requestSubmit();
  }

  function updateParcelSummary() {
    const fields = formFields();
    $('#summarySurvey').textContent = state.parcel ? `${state.parcel.survey_number}/${state.parcel.subdivision_number}` : '—';
    $('#summaryVillage').textContent = state.parcel?.village || '—';
    $('#summaryOfficialArea').textContent = state.parcel?.official_area_sqm == null ? '—' : `${state.parcel.official_area_sqm} m²`;
    $('#summaryDocumentArea').textContent = fields.document_area_sqm == null ? '—' : `${fields.document_area_sqm} m²`;
  }

  function resetClaimControls() {
    state.confirmed = false;
    state.claimStatus = null;
    state.registeredClaimId = null;
    confirmParcel.checked = false;
    claimButton.disabled = true;
    confirmParcel.disabled = !state.parcel;
    $('#viewClaimedLandButton').hidden = true;
    $('#claimControls').hidden = false;
    $('#completionPanel').hidden = true;
    $('#completionPanel').classList.remove('review-required');
    ParcelLedgerUI.setFeedback($('#claimResult'), '', '');
  }

  function renderResolution(resolution) {
    resetClaimControls();
    state.layers.forEach((layer) => layer.remove());
    state.layers = [];
    state.parcel = resolution.parcel;

    const mapPresentation = ParcelLedgerUI.parcelMapPresentation(resolution);
    $('#parcelMap').hidden = !mapPresentation.showMap;
    $('#mapEmptyState').hidden = mapPresentation.showMap;
    $('#mapEmptyMessage').textContent = mapPresentation.emptyMessage;

    const matchPill = $('#matchPill');
    const resolutionLabel = ParcelLedgerUI.matchLabel(resolution.status);
    const resolutionStatus = String(resolution.status || 'waiting').replace(/[^a-z_]/g, '');
    matchPill.textContent = resolutionLabel;
    matchPill.className = `status-readout status-readout--match match-status--${resolutionStatus}`;
    matchPill.setAttribute('aria-label', `Registry status: ${resolutionLabel}`);
    ParcelLedgerUI.renderWarnings($('#warnings'), resolution.warnings);
    ParcelLedgerUI.renderCandidates($('#candidates'), resolution.alternatives, selectCandidate);

    if (state.parcel) {
      ensureMap();
      const styles = getComputedStyle(document.documentElement);
      const outline = styles.getPropertyValue('--green-800').trim();
      const fill = styles.getPropertyValue('--green-600').trim();
      const layer = L.geoJSON(state.parcel.geometry, {
        style: { color: outline, weight: 4, fillColor: fill, fillOpacity: .28 },
      }).addTo(state.map);
      state.layers.push(layer);
      state.map.fitBounds(layer.getBounds(), { padding: [28, 28] });
      confirmParcel.disabled = resolution.status === 'not_found';
    } else {
      confirmParcel.disabled = true;
      if (state.map) state.map.setView([10.96, 79.38], 13);
    }

    updateParcelSummary();
    setStage(ParcelLedgerUI.workflowStage(state));
  }

  function showReview(filename) {
    $('#uploadSection').hidden = true;
    $('#documentBar').hidden = false;
    $('#workspace').hidden = false;
    $('#documentName').textContent = filename;
    $('#documentStatus').textContent = 'Ready to review';
    $('#documentStatus').setAttribute('aria-label', 'Document status: Ready to review');
    setStage('review');
    $('#documentBar').scrollIntoView({ behavior: 'smooth', block: 'start' });
  }

  async function processFile() {
    if (!fileInput.files[0]) {
      ParcelLedgerUI.setFeedback($('#processStatus'), 'error', 'Choose a patta file first.');
      return;
    }
    setButtonBusy(processButton, true, 'Reading document…');
    ParcelLedgerUI.setFeedback($('#processStatus'), 'info', 'Reading the document and checking the parcel registry…');
    const body = new FormData();
    body.append('file', fileInput.files[0]);
    try {
      const result = await fetch('/api/pattas/process', {
        method: 'POST', headers: headers(crypto.randomUUID()), body,
      }).then(jsonResponse);
      state.documentId = result.document_id;
      populateFields(result.extracted_fields);
      showReview(fileInput.files[0].name);
      renderResolution(result.resolution);
      const feedback = ParcelLedgerUI.processingFeedback(result.resolution);
      ParcelLedgerUI.setFeedback($('#reviewFeedback'), feedback.type, feedback.message);
    } catch (error) {
      ParcelLedgerUI.setFeedback($('#processStatus'), 'error', error.message);
    } finally {
      setButtonBusy(processButton, false);
      processButton.disabled = !fileInput.files.length;
    }
  }

  function restoreDropLabel() {
    const label = $('#dropZone strong');
    const browse = document.createElement('span');
    browse.textContent = 'browse';
    label.replaceChildren('Drag and drop or ', browse);
  }

  function resetWorkflow() {
    state.documentId = null;
    state.parcel = null;
    state.extracted = null;
    state.confirmed = false;
    state.claimStatus = null;
    state.registeredClaimId = null;
    state.layers.forEach((layer) => layer.remove());
    state.layers = [];
    $('#fieldForm').reset();
    $('#fieldForm').classList.remove('show-evidence');
    $('#evidenceToggle').setAttribute('aria-expanded', 'false');
    $('#evidenceToggle').textContent = 'Show OCR sources';
    document.querySelectorAll('[data-evidence]').forEach((node) => { node.textContent = ''; });
    fileInput.value = '';
    processButton.disabled = true;
    $('#dropZone').classList.remove('has-file', 'dragging');
    restoreDropLabel();
    ParcelLedgerUI.renderWarnings($('#warnings'), []);
    ParcelLedgerUI.renderCandidates($('#candidates'), [], selectCandidate);
    ParcelLedgerUI.setFeedback($('#processStatus'), '', '');
    ParcelLedgerUI.setFeedback($('#reviewFeedback'), '', '');
    resetClaimControls();
    $('#confidenceBadge').textContent = '— OCR quality';
    $('#confidenceBadge').setAttribute('aria-label', 'OCR quality unavailable');
    $('#matchPill').className = 'status-readout status-readout--match match-status--waiting';
    $('#matchPill').textContent = 'Waiting';
    $('#matchPill').setAttribute('aria-label', 'Registry status: Waiting');
    ['#summarySurvey', '#summaryVillage', '#summaryOfficialArea', '#summaryDocumentArea'].forEach((selector) => { $(selector).textContent = '—'; });
    $('#workspace').hidden = true;
    $('#documentBar').hidden = true;
    $('#uploadSection').hidden = false;
    setStage('upload');
    if (state.map) state.map.setView([10.96, 79.38], 13);
    window.scrollTo({ top: 0, behavior: 'smooth' });
  }

  fileInput.addEventListener('change', () => {
    const hasFile = Boolean(fileInput.files.length);
    processButton.disabled = !hasFile;
    $('#dropZone').classList.toggle('has-file', hasFile);
    if (hasFile) $('#dropZone strong').textContent = fileInput.files[0].name;
    else restoreDropLabel();
    ParcelLedgerUI.setFeedback($('#processStatus'), '', '');
  });

  ['dragenter', 'dragover'].forEach((event) => $('#dropZone').addEventListener(event, (e) => {
    e.preventDefault();
    $('#dropZone').classList.add('dragging');
  }));
  ['dragleave', 'drop'].forEach((event) => $('#dropZone').addEventListener(event, (e) => {
    e.preventDefault();
    $('#dropZone').classList.remove('dragging');
  }));
  $('#dropZone').addEventListener('drop', (e) => {
    fileInput.files = e.dataTransfer.files;
    fileInput.dispatchEvent(new Event('change'));
  });

  $('#logoutButton').addEventListener('click', async () => {
    await fetch('/api/auth/logout', { method: 'POST' });
    window.location.assign('/login');
  });
  processButton.addEventListener('click', processFile);
  $('#newClaimTab').addEventListener('click', () => showAppView('new'));
  $('#claimedLandTab').addEventListener('click', () => showAppView('claimed'));
  $('#viewClaimedLandButton').addEventListener('click', () => showAppView('claimed'));
  $('#viewClaimMapButton').addEventListener('click', () => showAppView('claimed', state.registeredClaimId));
  $('#replaceButton').addEventListener('click', resetWorkflow);
  $('#startOverButton').addEventListener('click', resetWorkflow);

  $('#evidenceToggle').addEventListener('click', () => {
    const form = $('#fieldForm');
    const expanded = !form.classList.contains('show-evidence');
    form.classList.toggle('show-evidence', expanded);
    $('#evidenceToggle').setAttribute('aria-expanded', String(expanded));
    $('#evidenceToggle').textContent = expanded ? 'Hide OCR sources' : 'Show OCR sources';
  });

  $('#fieldForm').addEventListener('submit', async (event) => {
    event.preventDefault();
    setButtonBusy(matchButton, true, 'Updating match…');
    ParcelLedgerUI.setFeedback($('#reviewFeedback'), 'info', 'Checking the corrected fields against the registry…');
    try {
      const result = await fetch('/api/parcels/resolve', {
        method: 'POST',
        headers: { ...headers(), 'Content-Type': 'application/json' },
        body: JSON.stringify({ document_id: state.documentId, ...formFields() }),
      }).then(jsonResponse);
      renderResolution(result);
      const feedback = ParcelLedgerUI.processingFeedback(result);
      ParcelLedgerUI.setFeedback($('#reviewFeedback'), feedback.type, feedback.message);
    } catch (error) {
      ParcelLedgerUI.setFeedback($('#reviewFeedback'), 'error', error.message);
    } finally {
      setButtonBusy(matchButton, false);
      matchButton.disabled = false;
    }
  });

  confirmParcel.addEventListener('change', () => {
    state.confirmed = Boolean(confirmParcel.checked && state.parcel);
    claimButton.disabled = !state.confirmed;
    setStage(ParcelLedgerUI.workflowStage(state));
  });

  claimButton.addEventListener('click', async () => {
    setButtonBusy(claimButton, true, 'Registering claim…');
    ParcelLedgerUI.setFeedback($('#claimResult'), 'info', 'Registering the claim…');
    try {
      const result = await fetch('/api/claims', {
        method: 'POST',
        headers: { ...headers(crypto.randomUUID()), 'Content-Type': 'application/json' },
        body: JSON.stringify({
          document_id: state.documentId,
          parcel_id: state.parcel.id,
          confirmed_fields: formFields(),
        }),
      }).then(jsonResponse);
      state.claimStatus = result.status;
      state.registeredClaimId = result.claim_id;
      $('#claimControls').hidden = true;
      const completion = $('#completionPanel');
      completion.hidden = false;
      completion.classList.remove('review-required');
      $('#completionTitle').textContent = 'Claim registered';
      $('#completionMessage').textContent = 'This parcel is now protected from competing claims.';
      $('#documentStatus').textContent = 'Registered';
      $('#documentStatus').setAttribute('aria-label', 'Document status: Registered');
      setStage(ParcelLedgerUI.workflowStage(state));
    } catch (error) {
      ParcelLedgerUI.setFeedback($('#claimResult'), 'error', error.message);
      setButtonBusy(claimButton, false);
      if (error.status === 409) {
        state.confirmed = false;
        confirmParcel.checked = false;
        confirmParcel.disabled = true;
        claimButton.disabled = true;
        $('#viewClaimedLandButton').hidden = false;
        $('#documentStatus').textContent = 'Already claimed';
        $('#documentStatus').setAttribute('aria-label', 'Document status: Already claimed');
      } else {
        claimButton.disabled = !state.confirmed;
      }
    }
  });

  ParcelLedgerUI.ensureBrowserSession(fetch, (url) => window.location.assign(url))
    .then((staff) => {
      if (!staff) return;
      $('#staffName').textContent = staff.display_name || 'Registry staff';
      showAppView('new');
      setStage('upload');
    })
    .catch(() => window.location.assign('/login'));
})();
