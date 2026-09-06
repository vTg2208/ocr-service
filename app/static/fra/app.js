const FRAWorkspace = (() => {
  const SECTIONS = ['dashboard', 'atlas', 'cases', 'assets', 'planner', 'reports'];
  const VIEWS = [...SECTIONS, 'archive'];
  function initialState() { return { section: 'dashboard', context: { state: 'TN', district: '', block: '', village: '' }, archive: { records: [], selected: null, search: '', loading: false } }; }
  function reduce(state, action) {
    if (action?.type === 'section' && VIEWS.includes(action.value)) return { ...state, section: action.value };
    if (action?.type === 'context') return { ...state, context: { ...state.context, ...(action.value || {}), state: 'TN' } };
    if (action?.type === 'archive') return { ...state, archive: { ...state.archive, ...(action.value || {}) } };
    return state;
  }
  function preferredRecord(records, selectedId) {
    if (!Array.isArray(records) || !records.length) return null;
    return records.find((record) => record.id === selectedId) || records[0];
  }
  async function ensureBrowserSession(fetchImpl, redirect) { try { return await FRAApi.request('/api/auth/session', {}, fetchImpl); } catch (error) { if (error.status === 401) redirect('/login'); return null; } }
  return { SECTIONS, VIEWS, ensureBrowserSession, initialState, preferredRecord, reduce };
})();
if (typeof module !== 'undefined' && module.exports) module.exports = FRAWorkspace;

if (typeof document !== 'undefined') (() => {
  let state = FRAWorkspace.initialState(); let archiveBatchKey = crypto.randomUUID(); let tabularBatchKey = crypto.randomUUID(); const $ = (selector) => document.querySelector(selector); const status = $('#workspaceStatus'); const filterWidget = $('#workspaceFilterWidget'); const filterPanel = $('#contextFilterPanel'); const filterToggle = $('#contextFilterToggle'); const archiveRequests = FRAApi.requestGate(); const recordRequests = FRAApi.requestGate(); const mutations = FRAApi.requestGate();
  function setStatus(message, type = '') { status.textContent = message || ''; status.dataset.type = type; }
  function setContextFilterOpen(open) { filterPanel.hidden = !open; filterToggle.setAttribute('aria-expanded', String(open)); }
  function updateContextFilterPresentation() {
    const selected = [state.context.district, state.context.block, state.context.village].filter(Boolean);
    $('#contextFilterCount').textContent = String(selected.length);
    $('#contextFilterCount').hidden = selected.length === 0;
    $('#contextFilterSummary').textContent = selected.length ? `Showing ${selected.join(' · ')}.` : 'Showing all available Tamil Nadu records.';
  }
  function showSection(section) {
    state = FRAWorkspace.reduce(state, { type: 'section', value: section });
    const navigationSection = state.section === 'archive' ? 'cases' : state.section;
    document.querySelectorAll('[data-section]').forEach((button) => { const active = button.dataset.section === navigationSection; button.classList.toggle('active', active); if (active) button.setAttribute('aria-current', 'page'); else button.removeAttribute('aria-current'); });
    document.querySelectorAll('[data-panel]').forEach((panel) => { const active = panel.dataset.panel === state.section; panel.hidden = !active; panel.classList.toggle('active', active); });
    const filterHost = document.querySelector(`[data-panel="${state.section}"] .filter-host`); if (filterHost) { filterHost.appendChild(filterWidget); filterWidget.hidden = false; } setContextFilterOpen(false);
    history.replaceState(null, '', state.section === 'archive' ? '#cases/legacy' : `#${state.section}`); $('#workspaceMain').focus({ preventScroll: true });
    document.dispatchEvent(new CustomEvent('fra:section', { detail: { section: state.section, context: { ...state.context } } }));
  }
  function archiveFilters() { return { district: state.context.district, block: state.context.block, village: state.context.village, right_type: $('#archiveRightType').value, review_state: $('#archiveReviewState').value, query: $('#archiveSearch').value }; }
  function renderArchiveEmpty(records) {
    const message = FRAArchiveUI.emptyState(records, $('#archiveSearch').value.trim()); $('#archiveEmpty').hidden = !message;
    if (message) { $('#archiveEmpty').querySelector('strong').textContent = message; $('#archiveEmpty').querySelector('span').textContent = message === 'No matching records' ? 'Clear or change filters to see other Tamil Nadu records.' : 'Import a Tamil Nadu source batch to begin review.'; }
  }
  async function loadRecord(record) {
    const current = recordRequests.begin(); mutations.invalidate(); state = FRAWorkspace.reduce(state, { type: 'archive', value: { selected: null } }); $('#saveReviewButton').disabled = true; $('#rejectExtractionButton').disabled = true; $('#promoteButton').disabled = true;
    try {
      setStatus('Loading archive evidence…'); const detail = await FRAApi.request(`/api/fra/archive/records/${record.id}`); if (!current()) return; state = FRAWorkspace.reduce(state, { type: 'archive', value: { selected: detail } });
      FRAArchiveUI.renderRecords($('#archiveList'), state.archive.records, detail.id, loadRecord); $('#recordReference').textContent = detail.legacy_reference; $('#recordState').textContent = String(detail.review_state).replaceAll('_', ' ');
      const latest = detail.extraction_runs.at(-1); $('#rawExtraction').textContent = latest?.raw_text || 'Raw OCR text is restricted or not available.'; $('#modelVersion').textContent = `Model ${latest?.entity_model_version || 'not recorded'}`;
      const provenance = $('#extractionProvenance'); provenance.replaceChildren();
      [['Source', detail.source_document?.source || 'not recorded'], ['Document', detail.source_document?.filename || 'not recorded'], ['Confidence', latest?.confidence == null ? '—' : `${Math.round(latest.confidence * 100)}%`], ['Processing', latest?.processing_time_ms == null ? '—' : `${latest.processing_time_ms} ms`], ['Provenance', latest?.provenance?.adapter || 'not recorded']].forEach(([term, value]) => { const row = document.createElement('div'); const dt = document.createElement('dt'); const dd = document.createElement('dd'); dt.textContent = term; dd.textContent = value; row.append(dt, dd); provenance.appendChild(row); });
      const warnings = latest?.provenance?.warnings || []; $('#extractionWarnings').textContent = warnings.length ? warnings.join(' · ') : 'No extraction warnings recorded.'; $('#extractionWarnings').dataset.state = warnings.length ? 'warning' : 'clear';
      FRAArchiveUI.renderFields($('#reviewedFields'), detail.reviewed_fields && Object.keys(detail.reviewed_fields).length ? detail.reviewed_fields : latest?.standardized_fields || {}, latest?.field_evidence || {}, latest?.field_reviews || []); const reviewable = Boolean(latest) && ['needs_review', 'reviewed'].includes(detail.review_state); $('#saveReviewButton').disabled = !reviewable; $('#rejectExtractionButton').disabled = !reviewable; $('#promoteButton').disabled = detail.review_state !== 'reviewed'; $('#reviewNotes').value = detail.provenance?.extraction_rejection?.reason || ''; setStatus('');
      document.dispatchEvent(new CustomEvent('fra:archive-selection', { detail: { id: detail.id, legacy_reference: detail.legacy_reference } }));
    } catch (error) { if (current()) setStatus(error.message, 'error'); }
  }
  function clearRecord() {
    recordRequests.invalidate(); mutations.invalidate();
    state = FRAWorkspace.reduce(state, { type: 'archive', value: { selected: null } });
    $('#recordReference').textContent = 'No archive record matches the current filters.';
    $('#recordState').textContent = 'No match';
    $('#modelVersion').textContent = 'No extraction selected';
    $('#rawExtraction').textContent = 'Select another filter to review source evidence.';
    FRAArchiveUI.renderFields($('#reviewedFields'), {}, {});
    $('#extractionWarnings').textContent = 'No extraction selected.'; $('#extractionWarnings').dataset.state = 'clear';
    $('#extractionProvenance').replaceChildren();
    $('#reviewNotes').value = 'No record selected.';
    $('#saveReviewButton').disabled = true;
    $('#rejectExtractionButton').disabled = true;
    $('#promoteButton').disabled = true;
    setStatus('');
  }
  async function loadArchive() {
    const current = archiveRequests.begin(); const selectedId = state.archive.selected?.id; recordRequests.invalidate(); mutations.invalidate(); state = FRAWorkspace.reduce(state, { type: 'archive', value: { selected: null } }); $('#saveReviewButton').disabled = true; $('#rejectExtractionButton').disabled = true; $('#promoteButton').disabled = true;
    try { setStatus('Loading the review queue…'); const suffix = FRAArchiveUI.query(archiveFilters()); const result = await FRAApi.request(`/api/fra/archive/records${suffix ? `?${suffix}` : ''}`); if (!current()) return; const preferred = FRAWorkspace.preferredRecord(result.items, selectedId); state = FRAWorkspace.reduce(state, { type: 'archive', value: { records: result.items, selected: preferred, loading: false } }); FRAArchiveUI.renderRecords($('#archiveList'), result.items, preferred?.id, loadRecord); renderArchiveEmpty(result.items); $('#archiveCount').textContent = `${result.items.length} ${result.items.length === 1 ? 'record' : 'records'}`; if (preferred) await loadRecord(preferred); else clearRecord(); } catch (error) { if (current()) setStatus(error.message, 'error'); }
  }
  async function saveReview(event) {
    event.preventDefault(); if (!state.archive.selected) return; const current = mutations.begin();
    try { setStatus('Saving reviewer corrections…'); const record = await FRAApi.request(`/api/fra/archive/records/${state.archive.selected.id}/review`, FRAApi.json('POST', { expected_revision: state.archive.selected.revision, reviewed_fields: FRAArchiveUI.formValues(event.currentTarget) })); if (!current()) return; setStatus('Review saved.', 'success'); await loadArchive(); } catch (error) { if (current()) setStatus(error.message, 'error'); }
  }
  async function promote() { if (!state.archive.selected) return; const current = mutations.begin(); try { setStatus('Promoting the reviewed source record…'); const result = await FRAApi.request(`/api/fra/archive/records/${state.archive.selected.id}/promote`, FRAApi.json('POST', { expected_revision: state.archive.selected.revision })); if (!current()) return; setStatus(`Promoted to FRA claim ${result.claim_number}.`, 'success'); await loadArchive(); } catch (error) { if (current()) setStatus(error.message, 'error'); } }
  function selectedArchiveFiles() { return Array.from($('#archiveFiles').files || []); }
  function previewArchiveFiles() {
    const files = selectedArchiveFiles(); FRAArchiveUI.renderBatchFiles($('#archiveUploadResults'), files.map((file) => ({ filename: file.name, status: 'ready' })));
    $('#archiveUploadSummary').textContent = files.length ? `${files.length} ${files.length === 1 ? 'file' : 'files'} selected for validation.` : 'Choose source files to begin.';
  }
  async function uploadArchiveBatch(event) {
    event.preventDefault(); const files = selectedArchiveFiles(); const sourceOffice = $('#archiveSourceOffice').value; const district = $('#archiveUploadDistrict').value; const button = $('#archiveUploadButton');
    if (!FRAArchiveUI.canUploadBatch({ fileCount: files.length, sourceOffice, district })) { $('#archiveUploadSummary').textContent = 'Add source office, district, and at least one file.'; return; }
    const body = new FormData(); files.forEach((file) => body.append('files', file)); body.append('source_office', sourceOffice.trim()); body.append('district', district.trim());
    button.disabled = true; button.textContent = 'Validating and queueing...'; $('#archiveUploadSummary').textContent = 'Checking file types, malware status, and duplicates...';
    try {
      const result = await FRAApi.request('/api/fra/archive/batch-upload', { method: 'POST', headers: { 'Idempotency-Key': archiveBatchKey }, body });
      FRAArchiveUI.renderBatchFiles($('#archiveUploadResults'), result.files); $('#archiveUploadSummary').textContent = FRAArchiveUI.batchSummary(result);
      if (!result.replayed) archiveBatchKey = crypto.randomUUID();
      if (result.accepted) { $('#archiveFiles').value = ''; await loadArchive(); }
    } catch (error) { $('#archiveUploadSummary').textContent = error.message; }
    finally { button.disabled = false; button.textContent = 'Validate and queue files'; }
  }
  async function rejectExtraction() { if (!state.archive.selected) return; const reason = $('#reviewNotes').value.trim(); if (!reason) { setStatus('Enter reviewer notes before rejecting the extraction.', 'error'); $('#reviewNotes').focus(); return; } const current = mutations.begin(); try { setStatus('Rejecting the extraction…'); await FRAApi.request(`/api/fra/archive/records/${state.archive.selected.id}/reject`, FRAApi.json('POST', { expected_revision: state.archive.selected.revision, reason })); if (!current()) return; setStatus('Extraction rejected with the reviewer reason recorded.', 'success'); await loadArchive(); } catch (error) { if (current()) setStatus(error.message, 'error'); } }
  function selectedTabularFile() { return $('#archiveTabularFile').files?.[0] || null; }
  function previewTabularFile() {
    const file = selectedTabularFile();
    FRAArchiveUI.renderBatchFiles($('#archiveTabularResults'), file ? [{ filename: file.name, status: 'ready' }] : []);
    $('#archiveTabularSummary').textContent = file ? 'One register selected for structural validation.' : 'Choose a CSV or XLSX register to begin.';
  }
  async function uploadTabularRegister(event) {
    event.preventDefault(); const file = selectedTabularFile(); const sourceOffice = $('#archiveTabularSourceOffice').value; const district = $('#archiveTabularDistrict').value; const button = $('#archiveTabularButton');
    if (!FRAArchiveUI.canUploadTabular({ filename: file?.name, sourceOffice, district })) { $('#archiveTabularSummary').textContent = 'Add source office, district, and one CSV or XLSX register.'; return; }
    const body = new FormData(); body.append('file', file); body.append('source_office', sourceOffice.trim()); body.append('district', district.trim());
    button.disabled = true; button.textContent = 'Validating and importing...'; $('#archiveTabularSummary').textContent = 'Checking the source, headers, rows, malware status, and duplicates...';
    try {
      const result = await FRAApi.request('/api/fra/archive/tabular-upload', { method: 'POST', headers: { 'Idempotency-Key': tabularBatchKey }, body });
      const rows = (result.records || []).map((row) => ({ filename: `Row ${row.source_row}`, status: row.status, legacy_reference: row.legacy_reference }));
      FRAArchiveUI.renderBatchFiles($('#archiveTabularResults'), rows); $('#archiveTabularSummary').textContent = FRAArchiveUI.tabularSummary(result);
      if (!result.replayed) tabularBatchKey = crypto.randomUUID();
      if (result.accepted) { $('#archiveTabularFile').value = ''; await loadArchive(); }
    } catch (error) { $('#archiveTabularSummary').textContent = error.message; }
    finally { button.disabled = false; button.textContent = 'Validate and import register'; }
  }
  async function loadVillageOptions() { try { const result = await FRAApi.request('/api/fra/villages'); const districts = [...new Set(result.items.map((item) => item.district_name))].sort(); districts.forEach((name) => $('#contextDistrict').add(new Option(name, name))); state.villages = result.items; } catch (error) { setStatus(error.message, 'error'); } }
  function publishContext() { updateContextFilterPresentation(); document.dispatchEvent(new CustomEvent('fra:context', { detail: { ...state.context } })); }
  function updateDependentContext() { const district = $('#contextDistrict').value; const blocks = [...new Set((state.villages || []).filter((item) => !district || item.district_name === district).map((item) => item.block_name))].sort(); $('#contextBlock').replaceChildren(new Option('All blocks/taluks', '')); blocks.forEach((name) => $('#contextBlock').add(new Option(name, name))); $('#contextVillage').replaceChildren(new Option('All villages', '')); (state.villages || []).filter((item) => !district || item.district_name === district).forEach((item) => $('#contextVillage').add(new Option(item.village_name, item.village_name))); state = FRAWorkspace.reduce(state, { type: 'context', value: { district, block: '', village: '' } }); publishContext(); loadArchive(); }
  function updateBlockContext() {
    const district = $('#contextDistrict').value; const block = $('#contextBlock').value; const previous = $('#contextVillage').value;
    const villages = (state.villages || []).filter((item) => (!district || item.district_name === district) && (!block || item.block_name === block));
    $('#contextVillage').replaceChildren(new Option('All villages', ''));
    villages.forEach((item) => $('#contextVillage').add(new Option(item.village_name, item.village_name)));
    const village = villages.some((item) => item.village_name === previous) ? previous : ''; $('#contextVillage').value = village;
    state = FRAWorkspace.reduce(state, { type: 'context', value: { district, block, village } }); publishContext(); loadArchive();
  }
  function applyAtlasDrill(detail = {}) {
    const district = String(detail.district || ''); const block = String(detail.block || ''); const village = String(detail.village || '');
    $('#contextDistrict').value = [...$('#contextDistrict').options].some((item) => item.value === district) ? district : '';
    const availableBlocks = [...new Set((state.villages || []).filter((item) => !district || item.district_name === district).map((item) => item.block_name))].sort();
    $('#contextBlock').replaceChildren(new Option('All blocks/taluks', '')); availableBlocks.forEach((name) => $('#contextBlock').add(new Option(name, name))); $('#contextBlock').value = availableBlocks.includes(block) ? block : '';
    const availableVillages = (state.villages || []).filter((item) => (!district || item.district_name === district) && (!block || item.block_name === block));
    $('#contextVillage').replaceChildren(new Option('All villages', '')); availableVillages.forEach((item) => $('#contextVillage').add(new Option(item.village_name, item.village_name))); $('#contextVillage').value = availableVillages.some((item) => item.village_name === village) ? village : '';
    state = FRAWorkspace.reduce(state, { type: 'context', value: { district: $('#contextDistrict').value, block: $('#contextBlock').value, village: $('#contextVillage').value } }); publishContext(); loadArchive();
  }
  $('#archiveUploadForm').addEventListener('submit', uploadArchiveBatch); $('#archiveFiles').addEventListener('change', previewArchiveFiles); $('#archiveTabularForm').addEventListener('submit', uploadTabularRegister); $('#archiveTabularFile').addEventListener('change', previewTabularFile);
  document.addEventListener('fra:atlas-drill', (event) => applyAtlasDrill(event.detail));
  document.querySelectorAll('[data-section]').forEach((button) => button.addEventListener('click', () => showSection(button.dataset.section)));
  filterToggle.addEventListener('click', () => setContextFilterOpen(filterPanel.hidden));
  $('#contextFilterClose').addEventListener('click', () => { setContextFilterOpen(false); filterToggle.focus(); });
  document.addEventListener('keydown', (event) => { if (event.key === 'Escape' && !filterPanel.hidden) { setContextFilterOpen(false); filterToggle.focus(); } });
  document.addEventListener('click', (event) => { if (!filterPanel.hidden && !filterWidget.contains(event.target)) setContextFilterOpen(false); });
  document.addEventListener('fra:open-legacy-records', () => showSection('archive'));
  $('#archiveModeCases').addEventListener('click', () => { showSection('cases'); document.dispatchEvent(new CustomEvent('fra:case-mode', { detail: { mode: 'cases' } })); });
  $('#archiveModeIntake').addEventListener('click', () => { showSection('cases'); document.dispatchEvent(new CustomEvent('fra:case-mode', { detail: { mode: 'intake' } })); });
  $('#archiveFilters').addEventListener('submit', (event) => { event.preventDefault(); loadArchive(); }); $('#refreshArchive').addEventListener('click', loadArchive); $('#reviewForm').addEventListener('submit', saveReview); $('#rejectExtractionButton').addEventListener('click', rejectExtraction); $('#promoteButton').addEventListener('click', promote); $('#contextDistrict').addEventListener('change', updateDependentContext); $('#contextBlock').addEventListener('change', updateBlockContext); $('#contextVillage').addEventListener('change', () => { state = FRAWorkspace.reduce(state, { type: 'context', value: { village: $('#contextVillage').value } }); publishContext(); loadArchive(); }); $('#logoutButton').addEventListener('click', async () => { await fetch('/api/auth/logout', { method: 'POST' }); window.location.assign('/login'); });
  FRAWorkspace.ensureBrowserSession(fetch, (url) => window.location.assign(url)).then(async (user) => { if (!user) return; $('#staffName').textContent = user.display_name || 'Registry staff'; const requested = location.hash.slice(1); const view = requested === 'cases/legacy' || requested === 'archive' ? 'archive' : FRAWorkspace.SECTIONS.includes(requested) ? requested : 'dashboard'; showSection(view); await loadVillageOptions(); await loadArchive(); });
})();
