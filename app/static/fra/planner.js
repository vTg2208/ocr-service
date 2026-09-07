const FRAPlannerUI = (() => {
  const LABELS = {
    no_verified_satellite_assets: 'No satellite asset observations have been verified',
    water_asset_observation_gap: 'Verified information about local water assets is missing',
    agricultural_land_fraction: 'Share of mapped land used for agriculture',
    drinking_water_gap: 'Household drinking-water access needs attention',
    drinking_water: 'Drinking water',
    water_source_strengthening: 'Water-source strengthening',
    water_source_present: 'Verified water source present',
    groundwater_status: 'Groundwater condition',
    water_body: 'Water body',
    imagery_artifact: 'Satellite imagery observation',
    spatial_reference_feature: 'Published spatial reference',
    road_asset_observation_gap: 'Verified information about road access is missing',
    infrastructure_asset_observation_gap: 'Verified information about public services is missing',
    low_mapped_asset_coverage: 'Only a small part of the village has reviewed asset mapping',
    no_verified_assets_intersect_fra_land: 'No verified mapped assets overlap FRA land',
    agricultural_observation: 'Reviewed agricultural-land observation',
    forest_cover_present: 'Reviewed forest-cover observation',
    homestead_observation: 'Reviewed homestead observation',
    infrastructure_services: 'Public infrastructure and services',
    road_access: 'Road access',
    water_stress_status: 'Water-stress condition',
    unavailable: 'Not available',
    source: 'Recorded source',
  };
  function humanize(value) { const raw = String(value ?? '').trim(); if (!raw) return ''; const text = LABELS[raw] || raw.replaceAll('_', ' '); return `${text[0].toUpperCase()}${text.slice(1)}`; }
  function disclaimer() { return 'This advisory workspace does not approve or sanction benefits.'; }
  function query(form) { const params = new URLSearchParams(); ['outcome', 'scheme_code', 'right_type', 'intervention_type'].forEach((key) => { const value = form.elements[key].value.trim(); if (value) params.set(key, value); }); return params.toString(); }
  function derivePayload(claimId) { const id = String(claimId || '').trim(); if (!id) throw new Error('Select a native FRA claim before deriving facts.'); return { claim_id: id, derivation_version: 'fra-dss-facts-v1' }; }
  return { derivePayload, disclaimer, humanize, query };
})();
if (typeof module !== 'undefined' && module.exports) module.exports = FRAPlannerUI;

if (typeof document !== 'undefined') (() => {
  const form = document.querySelector('#recommendationFilters'); if (!form) return; const list = document.querySelector('#recommendationList'); const count = document.querySelector('#recommendationCount'); const claimSelect = document.querySelector('#plannerClaim'); const factStatus = document.querySelector('#factSnapshotStatus'); let loaded = false; let catalogLoaded = false; const requests = FRAApi.requestGate(); const derivations = FRAApi.requestGate(); const catalogRequests = FRAApi.requestGate();
  function line(label, values, empty = 'None recorded') { const block = document.createElement('p'); const strong = document.createElement('strong'); strong.textContent = `${label}: `; block.appendChild(strong); block.append(document.createTextNode((values || []).map(FRAPlannerUI.humanize).join('; ') || empty)); return block; }
  async function refer(item, button, message) {
    const departmentInput = document.querySelector('#referralDepartment'); const notesInput = document.querySelector('#referralNotes'); const department = departmentInput.value.trim(); const notes = notesInput.value.trim();
    if (!department) { message.textContent = 'Enter the responsible department.'; departmentInput.focus(); return; }
    if (!notes) { message.textContent = 'Explain why this recommendation needs departmental review.'; notesInput.focus(); return; }
    try {
      button.disabled = true; button.setAttribute('aria-busy', 'true'); const idleLabel = button.textContent; button.dataset.idleLabel = idleLabel; button.textContent = 'Sending referral…'; message.textContent = 'Creating an auditable human-review referral…';
      const payload = { department, priority: item.priority || 'normal', idempotency_key: `ui-${item.recommendation_id}-${department.toLowerCase().replace(/\W+/g, '-')}`, notes };
      const referral = await FRAApi.request(`/api/fra/dss/recommendations/${item.recommendation_id}/referrals`, FRAApi.json('POST', payload));
      message.textContent = `Human review ${referral.status}; ${referral.department}. History entries: ${referral.history.length}.`; notesInput.value = '';
    } catch (error) { message.textContent = error.status === 403 ? 'Reviewer or administrator access is required to send a recommendation for human review.' : error.message; }
    finally { button.disabled = false; button.removeAttribute('aria-busy'); button.textContent = button.dataset.idleLabel || 'Send for human review'; }
  }
  function renderSummary(summary = {}) { document.querySelector('#plannerCandidateCount').textContent = summary.potentially_eligible ?? 0; document.querySelector('#plannerNotIndicatedCount').textContent = summary.not_indicated ?? 0; document.querySelector('#plannerInsufficientCount').textContent = summary.insufficient_data ?? 0; document.querySelector('#plannerNotEvaluatedCount').textContent = summary.not_evaluated ?? 0; }
  function render(items) { list.replaceChildren(); count.textContent = `${items.length} ${items.length === 1 ? 'record' : 'records'}`; items.forEach((item) => { const row = document.createElement('li'); const header = document.createElement('div'); const title = document.createElement('h3'); const outcome = document.createElement('span'); title.textContent = `${item.scheme_name} · ${item.claim_number}`; outcome.className = 'record-state'; outcome.textContent = FRAPlannerUI.humanize(item.convergence_status); header.append(title, outcome); const rec = document.createElement('p'); rec.textContent = item.reason_for_recommendation || item.reason_for_rejection || (item.convergence_status === 'not_evaluated' ? 'Run the DSS evaluation to produce an evidence-based convergence result.' : 'Human review guidance is recorded in the rule explanation.'); const provenance = document.createElement('details'); provenance.className = 'recommendation-provenance'; const provenanceSummary = document.createElement('summary'); provenanceSummary.textContent = 'Evaluation provenance'; const source = document.createElement('small'); source.textContent = `Scheme catalogue ${item.catalog_version || 'not recorded'} (${FRAPlannerUI.humanize(item.catalog_status || 'status not recorded')}) · Evaluation rule ${item.rule_version || 'not evaluated'}`; provenance.append(provenanceSummary, source); const button = document.createElement('button'); button.className = 'secondary-action'; button.type = 'button'; button.textContent = 'Send for human review'; button.disabled = !item.recommendation_id; const message = document.createElement('p'); message.className = 'inline-status'; message.setAttribute('aria-live', 'polite'); if (item.recommendation_id) button.addEventListener('click', () => refer(item, button, message)); const missingPrerequisites = (item.missing_prerequisites || []).map((entry) => entry.label || entry.fact); const evidence = (item.supporting_evidence || []).map((entry) => `${FRAPlannerUI.humanize(entry.fact)}: ${FRAPlannerUI.humanize(entry.verification_state)} · ${FRAPlannerUI.humanize(entry.source_entity_type || 'source')}${entry.observed_at ? ` · ${entry.observed_at}` : ''}`); const assets = (item.mapped_assets || []).map((entry) => entry.asset); const interventions = item.recommended_interventions || []; const completeness = item.evidence_completeness_percent == null ? ['Not calculated'] : [`${item.evidence_completeness_percent}%`]; row.append(header, rec, line('FRA status', [item.claim_status || 'unknown']), line('Priority', [item.priority || 'not assigned']), line('Mapped assets', assets, 'No verified mapped assets'), line('Information gaps', item.deficiencies, 'No mapped information gap recorded'), line('Suggested intervention', interventions, 'No intervention suggested'), line('Evidence completeness', completeness), line('Information still needed', item.unknown_data, 'No unknown inputs'), line('Why this result was produced', item.reasons, 'No rule explanation recorded'), line('Required information', missingPrerequisites, 'No missing prerequisites'), line('Supporting evidence', evidence, 'No supporting evidence recorded'), provenance, button, message); list.appendChild(row); }); if (!items.length) { const empty = document.createElement('li'); empty.className = 'empty-row'; empty.textContent = 'No scheme-convergence records match these filters.'; list.appendChild(empty); } }
  async function loadCatalog() { const current = catalogRequests.begin(); try { const data = await FRAApi.request('/api/fra/dss/scheme-catalog'); if (!current()) return; const scheme = document.querySelector('#plannerScheme'); const intervention = document.querySelector('#plannerIntervention'); const selectedScheme = scheme.value; const selectedIntervention = intervention.value; const latest = new Map(); (data.items || []).forEach((entry) => latest.set(entry.scheme_code, entry)); scheme.replaceChildren(new Option('All schemes', '')); [...latest.values()].sort((a, b) => a.display_name.localeCompare(b.display_name)).forEach((entry) => scheme.add(new Option(`${entry.display_name} · ${entry.active ? 'active' : 'draft/inactive'}`, entry.scheme_code))); const interventions = [...new Set([...latest.values()].flatMap((entry) => entry.definition?.intervention_types || []))].sort(); intervention.replaceChildren(new Option('All intervention types', '')); interventions.forEach((value) => intervention.add(new Option(value.replaceAll('_', ' '), value))); scheme.value = latest.has(selectedScheme) ? selectedScheme : ''; intervention.value = interventions.includes(selectedIntervention) ? selectedIntervention : ''; catalogLoaded = true; } catch (error) { if (current()) factStatus.textContent = `Scheme catalogue: ${error.message}`; } }
  async function load() {
    const current = requests.begin();
    try {
      const suffix = [FRAPlannerUI.query(form), FRAApi.contextQuery()].filter(Boolean).join('&');
      const context = FRAApi.contextQuery();
      const [data, claims] = await Promise.all([
        FRAApi.request(`/api/fra/dss/convergence${suffix ? `?${suffix}` : ''}`),
        FRAApi.request(`/api/fra/cases${context ? `?${context}` : ''}`),
      ]);
      if (!current()) return;
      const selected = claimSelect.value;
      claimSelect.replaceChildren(new Option('Select a native FRA claim', ''));
      claims.items.forEach((item) => claimSelect.add(new Option(`${item.claim_number} — ${item.right_type}`, item.id)));
      claimSelect.value = claims.items.some((item) => item.id === selected) ? selected : '';
      renderSummary(data.summary); render(data.items); loaded = true;
    } catch (error) { if (current()) list.textContent = error.message; }
  }
  async function derive() {
    const button = document.querySelector('#deriveRecommendations'); const current = derivations.begin();
    try {
      button.disabled = true; const payload = FRAPlannerUI.derivePayload(claimSelect.value);
      const options = FRAApi.json('POST', payload); options.headers['Idempotency-Key'] = `planner-${payload.claim_id}-${crypto.randomUUID()}`;
      const data = await FRAApi.request('/api/fra/dss/derive-and-evaluate', options);
      if (!current()) return;
      const unknown = Object.values(data.fact_snapshot.facts).filter((item) => item.value === 'unknown').length;
      factStatus.textContent = `Fact snapshot ${data.fact_snapshot.derivation_version}: ${unknown} unknown input${unknown === 1 ? '' : 's'}. Recommendations remain advisory.`;
      await load();
    } catch (error) { if (current()) factStatus.textContent = error.message; }
    finally { button.disabled = false; }
  }
  form.addEventListener('submit', (event) => { event.preventDefault(); load(); });
  document.querySelector('#deriveRecommendations').addEventListener('click', derive);
  claimSelect.addEventListener('change', () => { derivations.invalidate(); factStatus.textContent = ''; });
  document.addEventListener('fra:section', (event) => { if (event.detail.section === 'planner') { if (!catalogLoaded) loadCatalog(); if (!loaded) load(); } });
  document.addEventListener('fra:context', () => { loaded = false; requests.invalidate(); derivations.invalidate(); factStatus.textContent = ''; if (!document.querySelector('#plannerPanel').hidden) load(); });

})();
