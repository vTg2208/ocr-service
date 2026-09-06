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
