(() => {
  const state = { documentId: null, parcel: null, extracted: null, map: null, layers: [] };
  const $ = (selector) => document.querySelector(selector);
  const fileInput = $('#pattaFile');
  const processButton = $('#processButton');
  const claimButton = $('#claimButton');
  const confirmParcel = $('#confirmParcel');

  function headers(idempotencyKey) {
    const token = $('#authToken').value.trim();
    const values = { Authorization: `Bearer ${token}` };
    if (idempotencyKey) values['Idempotency-Key'] = idempotencyKey;
    return values;
  }
  async function jsonResponse(response) {
    const body = await response.json();
    if (!response.ok) throw new Error(body.message || body.detail || 'The request failed.');
    return body;
  }
  function setStep(number) {
    document.querySelectorAll('.step').forEach((node) => {
      const active = Number(node.dataset.step) === number;
      node.classList.toggle('active', active);
      if (active) node.setAttribute('aria-current', 'step'); else node.removeAttribute('aria-current');
    });
  }
  function populateFields(fields) {
    state.extracted = fields;
    Object.entries(fields).forEach(([key, value]) => {
      const input = document.querySelector(`[name="${key}"]`);
      if (input && value !== null) input.value = value;
    });
    Object.entries(fields.evidence || {}).forEach(([key, value]) => {
      const evidence = document.querySelector(`[data-evidence="${key}"]`);
      if (evidence) evidence.textContent = `Source: “${value}”`;
    });
    $('#confidenceBadge').textContent = `${Math.round((fields.confidence || 0) * 100)}% confidence`;
    document.querySelectorAll('.field-grid label').forEach((label) => label.classList.toggle('low', !label.querySelector('input')?.value));
  }
  function formFields() {
    const raw = Object.fromEntries(new FormData($('#fieldForm')).entries());
    raw.document_area_sqm = raw.document_area_sqm ? Number(raw.document_area_sqm) : null;
    return raw;
  }
  function ensureMap() {
    if (!state.map) {
      state.map = L.map('parcelMap').setView([10.96, 79.38], 13);
      L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', { maxZoom: 19, attribution: '&copy; OpenStreetMap contributors' }).addTo(state.map);
    }
  }
  function renderResolution(resolution) {
    confirmParcel.checked = false;
    claimButton.disabled = true;
    $('#claimResult').className = 'claim-result';
    $('#claimResult').textContent = '';
    ensureMap();
    state.layers.forEach((layer) => layer.remove()); state.layers = [];
    state.parcel = resolution.parcel;
    $('#matchPill').textContent = resolution.status.replaceAll('_', ' ');
    $('#warnings').innerHTML = (resolution.warnings || []).map((item) => `<div class="warning">${item}</div>`).join('');
    $('#candidates').innerHTML = (resolution.alternatives || []).map((item, index) => `<button type="button" class="candidate" data-candidate="${index}">Candidate ${item.survey_number}/${item.subdivision_number} · ${item.village}<br><small>Select and resolve</small></button>`).join('');
    document.querySelectorAll('[data-candidate]').forEach((button) => button.addEventListener('click', () => {
      const candidate = resolution.alternatives[Number(button.dataset.candidate)];
      ['state','district','taluk','village','survey_number','subdivision_number'].forEach((name) => { document.querySelector(`[name="${name}"]`).value = candidate[name] ?? ''; });
      $('#fieldForm').requestSubmit();
    }));
    if (state.parcel) {
      const layer = L.geoJSON(state.parcel.geometry, { style: { color: '#164c3b', weight: 4, fillColor: '#2b765d', fillOpacity: .28 } }).addTo(state.map);
      state.layers.push(layer); state.map.fitBounds(layer.getBounds(), { padding: [28, 28] });
      const values = [`${state.parcel.survey_number}/${state.parcel.subdivision_number}`, state.parcel.village, `${state.parcel.official_area_sqm ?? '—'} m²`, `${formFields().document_area_sqm ?? '—'} m²`];
      document.querySelectorAll('#parcelSummary dd').forEach((node, index) => { node.textContent = values[index]; });
      confirmParcel.disabled = resolution.status === 'not_found';
    } else { confirmParcel.disabled = true; confirmParcel.checked = false; claimButton.disabled = true; }
    setStep(state.parcel ? 3 : 2);
  }
  async function processFile() {
    if (!fileInput.files[0] || !$('#authToken').value.trim()) { $('#processStatus').textContent = 'Choose a file and provide a signed access token.'; return; }
    processButton.disabled = true; $('#processStatus').textContent = 'Reading and locating the parcel…';
    const body = new FormData(); body.append('file', fileInput.files[0]);
    try {
      const result = await fetch('/api/pattas/process', { method: 'POST', headers: headers(crypto.randomUUID()), body }).then(jsonResponse);
      state.documentId = result.document_id; populateFields(result.extracted_fields);
      $('#workspace').hidden = false; renderResolution(result.resolution); setStep(2);
      $('#processStatus').textContent = 'Extraction complete. Confirm the transcription below.';
    } catch (error) { $('#processStatus').textContent = error.message; }
    finally { processButton.disabled = false; }
  }
  fileInput.addEventListener('change', () => { processButton.disabled = !fileInput.files.length; $('#dropZone strong').textContent = fileInput.files[0]?.name || 'Drop the patta here'; });
  ['dragenter','dragover'].forEach((event) => $('#dropZone').addEventListener(event, (e) => { e.preventDefault(); $('#dropZone').classList.add('dragging'); }));
  ['dragleave','drop'].forEach((event) => $('#dropZone').addEventListener(event, (e) => { e.preventDefault(); $('#dropZone').classList.remove('dragging'); }));
  $('#dropZone').addEventListener('drop', (e) => { fileInput.files = e.dataTransfer.files; fileInput.dispatchEvent(new Event('change')); });
  processButton.addEventListener('click', processFile);
  $('#fieldForm').addEventListener('submit', async (event) => {
    event.preventDefault();
    try {
      const result = await fetch('/api/parcels/resolve', { method: 'POST', headers: { ...headers(), 'Content-Type': 'application/json' }, body: JSON.stringify({ document_id: state.documentId, ...formFields() }) }).then(jsonResponse);
      renderResolution(result);
    } catch (error) { $('#warnings').innerHTML = `<div class="warning">${error.message}</div>`; }
  });
  confirmParcel.addEventListener('change', () => { claimButton.disabled = !(confirmParcel.checked && state.parcel); if (confirmParcel.checked) setStep(4); });
  claimButton.addEventListener('click', async () => {
    claimButton.disabled = true;
    try {
      const result = await fetch('/api/claims', { method: 'POST', headers: { ...headers(crypto.randomUUID()), 'Content-Type': 'application/json' }, body: JSON.stringify({ document_id: state.documentId, parcel_id: state.parcel.id, confirmed_fields: formFields() }) }).then(jsonResponse);
      const conflict = result.status === 'conflicting';
      $('#claimResult').className = `claim-result${conflict ? ' conflicting' : ''}`;
      $('#claimResult').textContent = conflict ? 'Claim recorded. Another active claim requires administrative review; no claimant details have been disclosed.' : 'Claim recorded successfully in the central registry.';
    } catch (error) { $('#claimResult').textContent = error.message; claimButton.disabled = false; }
  });
})();
