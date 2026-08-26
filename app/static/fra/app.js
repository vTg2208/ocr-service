const FRAWorkspace = (() => {
  const SECTIONS = ['archive', 'atlas', 'assets', 'planner', 'reports'];
  function initialState() { return { section: 'archive', context: { state: 'TN', district: '', block: '', village: '' }, archive: { records: [], selected: null, search: '', loading: false } }; }
  function reduce(state, action) {
    if (action?.type === 'section' && SECTIONS.includes(action.value)) return { ...state, section: action.value };
    if (action?.type === 'context') return { ...state, context: { ...state.context, ...(action.value || {}), state: 'TN' } };
    if (action?.type === 'archive') return { ...state, archive: { ...state.archive, ...(action.value || {}) } };
    return state;
  }
  async function ensureBrowserSession(fetchImpl, redirect) { try { return await FRAApi.request('/api/auth/session', {}, fetchImpl); } catch (error) { if (error.status === 401) redirect('/login'); return null; } }
  return { SECTIONS, ensureBrowserSession, initialState, reduce };
})();
if (typeof module !== 'undefined' && module.exports) module.exports = FRAWorkspace;

if (typeof document !== 'undefined') (() => {
  let state = FRAWorkspace.initialState(); const $ = (selector) => document.querySelector(selector); const status = $('#workspaceStatus');
  function setStatus(message, type = '') { status.textContent = message || ''; status.dataset.type = type; }
  function showSection(section) {
    state = FRAWorkspace.reduce(state, { type: 'section', value: section });
    document.querySelectorAll('[data-section]').forEach((button) => { const active = button.dataset.section === state.section; button.classList.toggle('active', active); if (active) button.setAttribute('aria-current', 'page'); else button.removeAttribute('aria-current'); });
    document.querySelectorAll('[data-panel]').forEach((panel) => { const active = panel.dataset.panel === state.section; panel.hidden = !active; panel.classList.toggle('active', active); });
    history.replaceState(null, '', `#${state.section}`); $('#workspaceMain').focus({ preventScroll: true });
  }
  function archiveFilters() { return { district: state.context.district, block: state.context.block, village: state.context.village, right_type: $('#archiveRightType').value, review_state: $('#archiveReviewState').value, query: $('#archiveSearch').value }; }
  function renderArchiveEmpty(records) {
    const message = FRAArchiveUI.emptyState(records, $('#archiveSearch').value.trim()); $('#archiveEmpty').hidden = !message;
    if (message) { $('#archiveEmpty').querySelector('strong').textContent = message; $('#archiveEmpty').querySelector('span').textContent = message === 'No matching records' ? 'Clear or change filters to see other Tamil Nadu records.' : 'Import a Tamil Nadu source batch to begin review.'; }
  }
  async function loadRecord(record) {
    try {
      setStatus('Loading archive evidence…'); const detail = await FRAApi.request(`/api/fra/archive/records/${record.id}`); state = FRAWorkspace.reduce(state, { type: 'archive', value: { selected: detail } });
      FRAArchiveUI.renderRecords($('#archiveList'), state.archive.records, detail.id, loadRecord); $('#recordReference').textContent = detail.legacy_reference; $('#recordState').textContent = String(detail.review_state).replaceAll('_', ' ');
      const latest = detail.extraction_runs.at(-1); $('#rawExtraction').textContent = latest?.raw_text || 'Raw OCR text is restricted or not available.'; $('#modelVersion').textContent = `Model ${latest?.entity_model_version || 'not recorded'}`;
      const provenance = $('#extractionProvenance'); provenance.replaceChildren();
      [['Confidence', latest?.confidence == null ? '—' : `${Math.round(latest.confidence * 100)}%`], ['Processing', latest?.processing_time_ms == null ? '—' : `${latest.processing_time_ms} ms`], ['Provenance', latest?.provenance?.adapter || 'not recorded']].forEach(([term, value]) => { const row = document.createElement('div'); const dt = document.createElement('dt'); const dd = document.createElement('dd'); dt.textContent = term; dd.textContent = value; row.append(dt, dd); provenance.appendChild(row); });
      FRAArchiveUI.renderFields($('#reviewedFields'), detail.reviewed_fields && Object.keys(detail.reviewed_fields).length ? detail.reviewed_fields : latest?.standardized_fields || {}); $('#saveReviewButton').disabled = !latest || detail.review_state === 'promoted'; $('#promoteButton').disabled = detail.review_state !== 'reviewed'; setStatus('');
    } catch (error) { setStatus(error.message, 'error'); }
  }
  async function loadArchive() {
    try { setStatus('Loading the review queue…'); const suffix = FRAArchiveUI.query(archiveFilters()); const result = await FRAApi.request(`/api/fra/archive/records${suffix ? `?${suffix}` : ''}`); state = FRAWorkspace.reduce(state, { type: 'archive', value: { records: result.items, loading: false } }); FRAArchiveUI.renderRecords($('#archiveList'), result.items, state.archive.selected?.id, loadRecord); renderArchiveEmpty(result.items); $('#archiveCount').textContent = `${result.items.length} ${result.items.length === 1 ? 'record' : 'records'}`; setStatus(''); } catch (error) { setStatus(error.message, 'error'); }
  }
  async function saveReview(event) {
    event.preventDefault(); if (!state.archive.selected) return;
    try { setStatus('Saving reviewer corrections…'); const record = await FRAApi.request(`/api/fra/archive/records/${state.archive.selected.id}/review`, FRAApi.json('POST', { expected_revision: state.archive.selected.revision, reviewed_fields: FRAArchiveUI.formValues(event.currentTarget) })); setStatus('Review saved.', 'success'); await loadArchive(); await loadRecord(record); } catch (error) { setStatus(error.message, 'error'); }
  }
  async function promote() { if (!state.archive.selected) return; try { setStatus('Promoting the reviewed source record…'); const result = await FRAApi.request(`/api/fra/archive/records/${state.archive.selected.id}/promote`, { method: 'POST' }); setStatus(`Promoted to FRA claim ${result.claim_number}.`, 'success'); await loadArchive(); await loadRecord(state.archive.selected); } catch (error) { setStatus(error.message, 'error'); } }
  async function loadVillageOptions() { try { const result = await FRAApi.request('/api/fra/villages'); const districts = [...new Set(result.items.map((item) => item.district_name))].sort(); districts.forEach((name) => $('#contextDistrict').add(new Option(name, name))); state.villages = result.items; } catch (error) { setStatus(error.message, 'error'); } }
  function updateDependentContext() { const district = $('#contextDistrict').value; const blocks = [...new Set((state.villages || []).filter((item) => !district || item.district_name === district).map((item) => item.block_name))].sort(); $('#contextBlock').replaceChildren(new Option('All blocks/taluks', '')); blocks.forEach((name) => $('#contextBlock').add(new Option(name, name))); $('#contextVillage').replaceChildren(new Option('All villages', '')); (state.villages || []).filter((item) => !district || item.district_name === district).forEach((item) => $('#contextVillage').add(new Option(item.village_name, item.village_name))); state = FRAWorkspace.reduce(state, { type: 'context', value: { district, block: '', village: '' } }); loadArchive(); }
  document.querySelectorAll('[data-section]').forEach((button) => button.addEventListener('click', () => showSection(button.dataset.section))); $('#archiveFilters').addEventListener('submit', (event) => { event.preventDefault(); loadArchive(); }); $('#refreshArchive').addEventListener('click', loadArchive); $('#reviewForm').addEventListener('submit', saveReview); $('#promoteButton').addEventListener('click', promote); $('#contextDistrict').addEventListener('change', updateDependentContext); $('#contextBlock').addEventListener('change', () => { state = FRAWorkspace.reduce(state, { type: 'context', value: { block: $('#contextBlock').value } }); loadArchive(); }); $('#contextVillage').addEventListener('change', () => { state = FRAWorkspace.reduce(state, { type: 'context', value: { village: $('#contextVillage').value } }); loadArchive(); }); $('#logoutButton').addEventListener('click', async () => { await fetch('/api/auth/logout', { method: 'POST' }); window.location.assign('/login'); });
  FRAWorkspace.ensureBrowserSession(fetch, (url) => window.location.assign(url)).then(async (user) => { if (!user) return; $('#staffName').textContent = user.display_name || 'Registry staff'; const requested = location.hash.slice(1); showSection(FRAWorkspace.SECTIONS.includes(requested) ? requested : 'archive'); await loadVillageOptions(); await loadArchive(); });
})();
