const test = require('node:test');
const { browser, flush } = require('./fra_dom_fixture');

const savedGeometry = { type: 'MultiPolygon', coordinates: [[[[79, 10], [79.1, 10], [79.1, 10.1], [79, 10]]]] };
function caseDetail(id) { return { id, claim_number: `TN-${id}`, status: 'submitted', right_type: 'IFR',
  rights_holder: { display_name: 'Holder' }, location: {}, geometry_versions: [{ id: `geometry-${id}`, geometry: savedGeometry, version: 1, source: 'survey' }],
  evidence_items: [], decisions: [], titles: [], audit_timeline: [], allowed_transitions: [] }; }
async function openCase(ui, id) {
  ui.emit('fra:open-case', { claimId: id });
  ui.pending('/api/fra/cases').at(-1).resolve({ items: [{ id, claim_number: `TN-${id}`, right_type: 'IFR', status: 'submitted', location: {} }] });
  await flush(); ui.pending(`/api/fra/cases/${id}`).at(-1).resolve(caseDetail(id)); await flush();
}

test('case management lists real case summaries first and opens IFR, CR, and CFR details', async () => {
  const ui = browser(['cases.js']);
  const items = [
    { id: 'ifr', claim_number: 'TN-IFR-1', rights_holder: 'Devi household', right_type: 'IFR', status: 'granted', location: { district: 'Kanyakumari', village: 'Pechiparai' }, claimed_area_sqm: 1200 },
    { id: 'cr', claim_number: 'TN-CR-1', rights_holder: 'Pechiparai community', right_type: 'CR', status: 'submitted', location: { district: 'Salem', village: 'Yercaud' } },
    { id: 'cfr', claim_number: 'TN-CFR-1', rights_holder: 'Pechiparai Gram Sabha', right_type: 'CFR', status: 'sdlc_review', location: { district: 'Kanyakumari', village: 'Pechiparai' } },
  ];
  ui.node('#caseModeCases').click();
  ui.pending('/api/fra/cases')[0].resolve({ items }); await flush();
  assert.equal(ui.node('#caseListView').hidden, false);
  assert.equal(ui.node('#caseDetailView').hidden, true);
  assert.equal(ui.node('#caseList').children.length, 3);
  assert.match(ui.node('#caseList').textContent, /Devi household/);
  assert.match(ui.node('#caseList').textContent, /Pechiparai Gram Sabha/);
  for (const [index, item] of items.entries()) {
    ui.node('#caseList').children[index].children[0].click(); await flush();
    ui.pending(`/api/fra/cases/${item.id}`).at(-1).resolve({
      ...caseDetail(item.id), claim_number: item.claim_number, right_type: item.right_type,
      status: item.status, rights_holder: { display_name: item.rights_holder, holder_type: item.right_type === 'IFR' ? 'household' : 'community' },
      location: item.location, claimed_area_sqm: item.claimed_area_sqm, gram_sabha: item.right_type === 'IFR' ? null : { name: 'Pechiparai Gram Sabha' },
    }); await flush();
    assert.equal(ui.node('#caseListView').hidden, true);
    assert.equal(ui.node('#caseDetailView').hidden, false);
    assert.equal(ui.node('#caseOverviewNumber').textContent, item.claim_number);
    assert.equal(ui.node('#caseOverviewHolder').textContent, item.rights_holder);
    assert.equal(ui.node('#caseOverviewType').textContent, item.right_type);
    ui.pending('/api/fra/cases').at(-1).resolve({ items });
    ui.pending(`/api/fra/claims/${item.id}/historical-evidence`).at(-1).resolve({ artifacts: [], jobs: [] }); await flush();
    ui.node('#backToCases').click(); await flush();
  }
  ui.node('#caseRightType').value = 'CR'; await ui.node('#caseRightType').emit('change');
  assert.equal(ui.node('#caseList').children.length, 1);
  assert.match(ui.node('#caseList').textContent, /TN-CR-1/);
  ui.node('#caseRightType').value = ''; await ui.node('#caseRightType').emit('change');
  ui.node('#caseDistrict').value = 'Kanyakumari'; await ui.node('#caseDistrict').emit('change');
  assert.equal(ui.node('#caseList').children.length, 2);
  ui.node('#caseVillage').value = 'Pechiparai'; await ui.node('#caseVillage').emit('change');
  assert.equal(ui.node('#caseList').children.length, 2);
  ui.node('#caseSearch').value = 'Gram Sabha'; await ui.node('#caseSearch').emit('input');
  assert.equal(ui.node('#caseList').children.length, 1);
});

test('opening Registry Intake during initial case setup keeps the intake workflow visible', async () => {
  const ui = browser(['cases.js']);
  ui.emit('fra:section', { section: 'cases' });
  ui.emit('fra:case-mode', { mode: 'intake' });
  ui.pending('/api/fra/intake')[0].resolve({ items: [] });
  ui.pending('/api/auth/session')[0].resolve({ role: 'reviewer' }); await flush();
  ui.pending('/api/fra/case-reference/rights-holders')[0].resolve({ items: [] });
  ui.pending('/api/fra/case-reference/gram-sabhas')[0].resolve({ items: [] }); await flush();
  assert.equal(ui.node('#intakeWorkspace').hidden, false);
  assert.equal(ui.node('#nativeCasesWorkspace').hidden, true);
});

test('New FRA Record opens the focused upload and digitize entry point', async () => {
  const ui = browser(['cases.js'], { workspace: true });
  ui.context.history.pushState = () => {};
  await ui.node('#newFraRecord').click();
  assert.equal(ui.node('#archivePanel').dataset.workflow, 'upload');
  assert.equal(ui.node('#archiveTitle').textContent, 'Upload & Digitize');
  assert.equal(ui.node('#archiveUploadSheet').hidden, false);
  assert.equal(ui.node('#archiveUploadGuide').hidden, false);
  assert.equal(ui.node('#archiveUploadBack').hidden, false);
  ui.emit('fra:open-legacy-records');
  assert.equal(ui.node('#archivePanel').dataset.workflow, 'legacy');
  assert.equal(ui.node('#archiveTitle').textContent, 'Case Management');
  assert.equal(ui.node('#archiveUploadSheet').hidden, true);
});

test('claim and document tabs render reviewed archive values, source scan, and evidence', async () => {
  const ui = browser(['cases.js']);
  ui.emit('fra:open-case', { claimId: 'digitized' });
  ui.pending('/api/fra/cases')[0].resolve({ items: [{ id: 'digitized', status: 'submitted', location: {} }] }); await flush();
  ui.pending('/api/fra/cases/digitized')[0].resolve({
    ...caseDetail('digitized'), archive_record_id: 'archive-1', claim_number: 'TN-FRA-IFR-015',
    rights_holder: { display_name: 'Devi household', holder_type: 'household' },
    location: { district: 'Kanyakumari', block: 'Thiruvattar', village: 'Pechiparai' },
    location_state: 'TN', claimed_area_sqm: 1200, parcel_reference: '42/1',
    source_document: { id: 'scan-1', filename: 'claim.pdf', uploaded_at: '2026-08-01T00:00:00Z', ocr_status: 'completed', source_office: 'FRC', processing_status: 'promoted' },
    evidence_items: [{ id: 'evidence-1', category: 'documentary', document_type: 'gram_sabha_record', source: 'Gram Sabha', verification_state: 'unverified', created_at: '2026-08-02T00:00:00Z', document: { id: 'support-1', filename: 'resolution.pdf', uploaded_at: '2026-08-02T00:00:00Z' } }],
    decisions: [{ from_status: 'submitted', to_status: 'granted', authority_level: 'archive', outcome: 'historical_status_mapped' }],
  }); await flush();
  ui.pending('/api/fra/archive/records/archive-1')[0].resolve({
    review_state: 'promoted', reviewed_fields: { claim_year: '2024', survey_number: '42', gram_sabha_status: 'verified' },
    extraction_runs: [{ ocr_model_version: 'tesseract', entity_model_version: 'fra-v1', confidence: 0.82, raw_text: 'Claim form OCR text', standardized_fields: { survey_number: '42' }, field_reviews: [{ field_name: 'survey_number', source_value: '42', corrected_value: null, final_value: '42', evidence_chain: {} }] }],
  });
  ui.pending('/api/fra/cases').at(-1).resolve({ items: [] });
  ui.pending('/api/fra/claims/digitized/historical-evidence')[0].resolve({ artifacts: [], jobs: [] }); await flush();
  assert.equal(ui.node('#claimDistrict').textContent, 'Kanyakumari');
  assert.equal(ui.node('#claimSurvey').textContent.includes('42'), true);
  assert.equal(ui.node('#claimDate').textContent, '2024');
  assert.equal(ui.node('#caseOriginalFilename').textContent, 'claim.pdf');
  assert.equal(ui.node('#caseDigitizationModel').textContent, 'fra-v1');
  assert.equal(ui.node('#caseDigitizationConfidence').textContent, '82%');
  assert.match(ui.node('#caseEvidenceTimeline').textContent, /resolution.pdf/);
  assert.match(ui.node('#caseEvidenceTimeline').textContent, /Gram Sabha record/);
  assert.match(ui.node('#claimSurvey').textContent, /View evidence chain/);
  assert.equal(ui.node('#caseOverviewDecision').textContent, 'Not recorded');
  await ui.node('#caseViewOCR').click();
  assert.equal(ui.node('#caseOCRText').hidden, false);
  assert.match(ui.node('#caseOCRText').textContent, /Claim form OCR text/);
});

test('case context changes suppress stale detail and historical responses', async () => {
  const ui = browser(['cases.js']);
  await openCase(ui, 'one');
  const oldHistory = ui.pending('/api/fra/claims/one/historical-evidence')[0];
  ui.node('#contextDistrict').value = 'Other'; ui.emit('fra:context', {});
  ui.pending('/api/fra/cases').at(-1).resolve({ items: [] }); await flush();
  oldHistory.resolve({ artifacts: [{ target_year: 2005, state: 'completed', verification_state: 'verified' }], jobs: [] }); await flush();
  assert.equal(ui.node('#caseReference').textContent, 'No case selected');
  assert.doesNotMatch(ui.node('#caseHistoricalEvidence').textContent, /2005/);
  ui.emit('fra:open-case', { claimId: 'two' });
  ui.pending('/api/fra/cases').at(-1).resolve({ items: [{ id: 'two', status: 'submitted', location: {} }] }); await flush();
  const oldDetail = ui.pending('/api/fra/cases/two')[0];
  ui.emit('fra:context', {}); ui.pending('/api/fra/cases').at(-1).resolve({ items: [] }); await flush();
  oldDetail.resolve(caseDetail('two')); await flush();
  assert.equal(ui.node('#caseReference').textContent, 'No case selected');
});

test('spatial disposition uses the evaluated saved geometry and invalidates edits', async () => {
  const ui = browser(['cases.js']); await openCase(ui, 'one');
  assert.equal(ui.node('button').disabled, true, 'A submitted case cannot issue a title');
  ui.node('#caseSpatialEvaluate').click();
  ui.pending('/api/fra/claims/one/spatial-evaluation')[0].resolve({ outcome: 'review', claim_findings: [], reference_findings: [] }); await flush();
  ui.node('#caseSpatialDisposition').value = 'accepted'; ui.node('#caseSpatialDispositionNotes').value = 'Reviewed';
  ui.node('#caseSpatialDispositionForm').emit('submit');
  assert.equal(JSON.parse(ui.pending('/api/fra/claims/one/evidence')[0].options.body).provenance.geometry_version_id, 'geometry-one');
  await ui.node('#caseGeometry').emit('input');
  await ui.node('#caseSpatialDispositionForm').emit('submit');
  assert.equal(ui.pending('/api/fra/claims/one/evidence').length, 1);
  assert.match(ui.node('#caseMessage').textContent, /Save and evaluate/);
});
const assert = require('node:assert/strict');

const FRACasesUI = require('../app/static/fra/cases.js');

test('case workspace keeps a selected case while switching detail tabs', () => {
  const selected = FRACasesUI.selectCase(FRACasesUI.initialState(), 'claim-1');
  const changed = FRACasesUI.selectTab(selected, 'evidence');

  assert.equal(changed.selectedCaseId, 'claim-1');
  assert.equal(changed.detailTab, 'evidence');
});

test('case query combines shared location and case-specific filters', () => {
  assert.equal(
    FRACasesUI.query({
      district: 'Salem', block: 'Yercaud', village: '', status: 'submitted',
      right_type: 'IFR', query: 'Ramu',
    }),
    'district=Salem&block=Yercaud&status=submitted&right_type=IFR&query=Ramu',
  );
});

test('title issuance is available only to privileged staff for granted cases', () => {
  assert.equal(FRACasesUI.canIssueTitle({ role: 'reviewer', status: 'granted' }), true);
  assert.equal(FRACasesUI.canIssueTitle({ role: 'user', status: 'granted' }), false);
  assert.equal(FRACasesUI.canIssueTitle({ role: 'admin', status: 'submitted' }), false);
});

test('intake promotion requires review state and holder context', () => {
  assert.equal(FRACasesUI.canPromoteIntake({
    state: 'ready_for_promotion', rightType: 'IFR', rightsHolderId: 'holder-1',
  }), true);
  assert.equal(FRACasesUI.canPromoteIntake({
    state: 'awaiting_triage', rightType: 'IFR', rightsHolderId: 'holder-1',
  }), false);
});

test('case row presentation reports missing geometry without implying invalidity', () => {
  assert.deepEqual(
    FRACasesUI.casePresentation({
      claim_number: 'TN-IFR-1', rights_holder: 'Ramu', status: 'submitted',
      right_type: 'IFR', geometry_version_count: 0,
    }),
    {
      title: 'TN-IFR-1', holder: 'Ramu', status: 'submitted',
      meta: 'IFR · Boundary required',
    },
  );
});

test('claim boundary upload normalizes polygon features and rejects non-polygon data', () => {
  const boundary = FRACasesUI.normalizeBoundaryGeoJSON({
    type: 'FeatureCollection',
    features: [
      { type: 'Feature', properties: {}, geometry: { type: 'Polygon', coordinates: [[
        [78, 11], [78.1, 11], [78.1, 11.1], [78, 11],
      ]] } },
      { type: 'Feature', properties: {}, geometry: { type: 'MultiPolygon', coordinates: [[[
        [79, 12], [79.1, 12], [79.1, 12.1], [79, 12],
      ]]] } },
    ],
  });
  assert.equal(boundary.type, 'MultiPolygon');
  assert.equal(boundary.coordinates.length, 2);
  assert.throws(
    () => FRACasesUI.normalizeBoundaryGeoJSON({ type: 'Point', coordinates: [78, 11] }),
    /Polygon or MultiPolygon/,
  );
});

test('spatial findings remain supporting review signals with explicit provenance', () => {
  assert.deepEqual(
    FRACasesUI.spatialFindingPresentation({
      dataset_kind: 'protected_area', reason: 'intersects_protected_area',
      outcome: 'review_required', overlap_area_sqm: 1250.4,
      reference_source_version: 'tn-forest-2026',
    }),
    {
      layer: 'Protected area', finding: 'Intersects protected area',
      outcome: 'review required', overlap: '1,250 m²', source: 'tn-forest-2026',
    },
  );
  assert.match(FRACasesUI.spatialDisclaimer(), /does not determine legal validity/i);
});

test('historical evidence controls remain claim scoped and neutral', () => {
  assert.equal(
    FRACasesUI.historicalReportUrl('claim 1'),
    '/api/fra/reports/claims/claim%201/historical-evidence',
  );
  assert.equal(FRACasesUI.historicalReportUrl(''), null);
  assert.deepEqual(
    FRACasesUI.historicalPresentation({ target_year: 2005, state: 'completed', verification_state: 'unverified', provider: 'stac.example', cloud_cover: 7.5 }),
    { title: '2005 observation', status: 'completed · unverified', meta: 'stac.example · 7.5% cloud · supporting evidence only' },
  );
});
