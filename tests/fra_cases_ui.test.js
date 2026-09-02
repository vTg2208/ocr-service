const test = require('node:test');
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
