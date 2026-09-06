const FRAPlannerUI = (() => {
  function disclaimer() { return 'This advisory workspace does not approve or sanction benefits.'; }
  function query(form) { const params = new URLSearchParams(); ['outcome', 'scheme_code', 'right_type', 'intervention_type'].forEach((key) => { const value = form.elements[key].value.trim(); if (value) params.set(key, value); }); return params.toString(); }
  function derivePayload(claimId) { const id = String(claimId || '').trim(); if (!id) throw new Error('Select a native FRA claim before deriving facts.'); return { claim_id: id, derivation_version: 'fra-dss-facts-v1' }; }
  return { derivePayload, disclaimer, query };
})();
if (typeof module !== 'undefined' && module.exports) module.exports = FRAPlannerUI;

if (typeof document !== 'undefined') (() => {
  const form = document.querySelector('#recommendationFilters'); if (!form) return; const list = document.querySelector('#recommendationList'); const count = document.querySelector('#recommendationCount'); const claimSelect = document.querySelector('#plannerClaim'); const factStatus = document.querySelector('#factSnapshotStatus'); let loaded = false; let catalogLoaded = false; const requests = FRAApi.requestGate(); const derivations = FRAApi.requestGate(); const catalogRequests = FRAApi.requestGate();
  function line(label, values, empty = 'None recorded') { const block = document.createElement('p'); const strong = document.createElement('strong'); strong.textContent = `${label}: `; block.appendChild(strong); block.append(document.createTextNode((values || []).join('; ') || empty)); return block; }
  async function refer(item, button, message) { const department = document.querySelector('#referralDepartment').value.trim(); if (!department) { message.textContent = 'Enter the responsible department.'; return; } try { button.disabled = true; const payload = { department, priority: item.priority || 'normal', idempotency_key: `ui-${item.recommendation_id}-${department.toLowerCase().replace(/\W+/g, '-')}`, notes: 'Sent for responsible-department human review from the advisory Tamil Nadu DSS workspace.' }; const referral = await FRAApi.request(`/api/fra/dss/recommendations/${item.recommendation_id}/referrals`, FRAApi.json('POST', payload)); message.textContent = `Human review ${referral.status}; ${referral.department}. History entries: ${referral.history.length}.`; } catch (error) { message.textContent = error.status === 403 ? 'Reviewer or administrator access is required to send a recommendation for human review.' : error.message; } finally { button.disabled = false; } }
  function renderSummary(summary = {}) { document.querySelector('#plannerCandidateCount').textContent = summary.potentially_eligible ?? 0; document.querySelector('#plannerNotIndicatedCount').textContent = summary.not_indicated ?? 0; document.querySelector('#plannerInsufficientCount').textContent = summary.insufficient_data ?? 0; document.querySelector('#plannerNotEvaluatedCount').textContent = summary.not_evaluated ?? 0; }
  function render(items) { list.replaceChildren(); count.textContent = `${items.length} ${items.length === 1 ? 'record' : 'records'}`; items.forEach((item) => { const row = document.createElement('li'); const header = document.createElement('div'); const title = document.createElement('h3'); const outcome = document.createElement('span'); title.textContent = `${item.scheme_name} · ${item.claim_number}`; outcome.className = 'record-state'; outcome.textContent = item.convergence_status.replaceAll('_', ' '); header.append(title, outcome); const rec = document.createElement('p'); rec.textContent = item.reason_for_recommendation || item.reason_for_rejection || (item.convergence_status === 'not_evaluated' ? 'Run the DSS evaluation to produce an evidence-based convergence result.' : 'Human review guidance is recorded in the rule explanation.'); const source = document.createElement('small'); source.textContent = `Catalogue ${item.catalog_version || 'missing'} (${item.catalog_status.replaceAll('_', ' ')}) · Rule ${item.rule_version || 'not evaluated'}`; const button = document.createElement('button'); button.className = 'secondary-action'; button.type = 'button'; button.textContent = 'Send for human review'; button.disabled = !item.recommendation_id; const message = document.createElement('p'); message.className = 'inline-status'; message.setAttribute('aria-live', 'polite'); if (item.recommendation_id) button.addEventListener('click', () => refer(item, button, message)); const missingPrerequisites = (item.missing_prerequisites || []).map((entry) => entry.label || entry.fact); const evidence = (item.supporting_evidence || []).map((entry) => { const sourceType = String(entry.source_entity_type || 'source').replaceAll('_', ' '); return `${entry.fact}: ${entry.verification_state} · ${sourceType}${entry.observed_at ? ` · ${entry.observed_at}` : ''}`; }); const assets = (item.mapped_assets || []).map((entry) => String(entry.asset).replaceAll('_', ' ')); const interventions = (item.recommended_interventions || []).map((value) => String(value).replaceAll('_', ' ')); const completeness = item.evidence_completeness_percent == null ? ['Not calculated'] : [`${item.evidence_completeness_percent}%`]; row.append(header, rec, line('FRA status', [String(item.claim_status || 'unknown').replaceAll('_', ' ')]), line('Priority', [item.priority || 'not assigned']), line('Mapped assets', assets, 'No verified mapped assets'), line('Deficiencies', item.deficiencies, 'No mapped deficiency recorded'), line('Recommended intervention', interventions, 'No intervention recommended'), line('Evidence completeness', completeness), line('Unknown data', item.unknown_data, 'No unknown inputs'), line('Reasons', item.reasons, 'No rule explanation recorded'), line('Missing prerequisites', missingPrerequisites, 'No missing prerequisites'), line('Supporting evidence', evidence, 'No supporting evidence recorded'), source, button, message); list.appendChild(row); }); if (!items.length) { const empty = document.createElement('li'); empty.className = 'empty-row'; empty.textContent = 'No scheme-convergence records match these filters.'; list.appendChild(empty); } }
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
