const test = require('node:test');
const assert = require('node:assert/strict');

const FRADashboardUI = require('../app/static/fra/dashboard.js');
const { browser, flush } = require('./fra_dom_fixture');

test('dashboard reloads during an in-flight context change and ignores the older response', async () => {
  const ui = browser(['dashboard.js']);
  ui.emit('fra:section', { section: 'dashboard' });
  ui.node('#contextDistrict').value = 'Salem'; ui.emit('fra:context', {});
  assert.equal(ui.pending('/api/fra/dashboard/planner').length, 2);
  ui.pending('/api/fra/dashboard/planner')[1].resolve({ fra: { claims: 7, active_titles: 3 }, claims_by_status: {} });
  ui.pending('/api/fra/dashboard/verifier')[1].resolve({ totals: {}, queues: {} }); await flush();
  const current = ui.node('#dashboardTotalClaims').textContent;
  ui.pending('/api/fra/dashboard/planner')[0].resolve({ fra: { claims: 99, active_titles: 99 }, claims_by_status: {} });
  ui.pending('/api/fra/dashboard/verifier')[0].reject(new Error('Old summary failed')); await flush();
  assert.equal(ui.node('#dashboardTotalClaims').textContent, current);
  assert.equal(current, '7');
  assert.doesNotMatch(ui.node('#dashboardStatus').textContent, /Old summary/);
});

test('dashboard query uses the shared Tamil Nadu hierarchy filters', () => {
  assert.equal(FRADashboardUI.query({ district: 'District A', block: 'Block A', village: '' }), 'district=District+A&block=Block+A');
});

test('verifier queue rows expose work references without claimant identity', () => {
  assert.deepEqual(FRADashboardUI.queuePresentation({ queue: 'claims_review', reference: 'TN-1', reason: 'Lifecycle review required', status: 'submitted', district: 'Salem' }), {
    title: 'TN-1', meta: 'Salem · Lifecycle review required', status: 'submitted', workspace: '/fra#cases',
  });
});

test('planner metric labels remain operational and non-adjudicative', () => {
  assert.equal(FRADashboardUI.metricLabel('granted_area_sqm'), 'Granted area (m²)');
  assert.equal(FRADashboardUI.metricLabel('pending_cases'), 'Pending cases');
  assert.equal(FRADashboardUI.metricLabel('insufficient_data_cases'), 'Insufficient-data cases');
  assert.match(FRADashboardUI.disclaimer(), /operational summaries/i);
  assert.doesNotMatch(FRADashboardUI.disclaimer(), /legal validity is confirmed/i);
});

test('dashboard prioritizes operational work and routes it to specialist workspaces', () => {
  const priorities = FRADashboardUI.buildPriorities({
    totals: {
      archive_records_needing_review: 5,
      claims_awaiting_review: 4,
      spatial_findings_awaiting_disposition: 3,
      unverified_observations: 2,
      failed_or_overdue_jobs: 1,
    },
    queues: { processing_failures: [{ workspace: '/fra#cases', claim_id: 'claim-1' }] },
  }, {});
  assert.equal(priorities[0].title, 'Resolve failed or overdue processing');
  assert.equal(priorities[0].count, 1);
  assert.equal(priorities[1].title, 'Review extracted legacy records');
  assert.equal(priorities.length, 5);
  assert.ok(priorities.every((item) => ['archive', 'cases', 'assets', 'planner'].includes(item.section)));
});

test('dashboard creates planning tasks when verifier queues are unavailable', () => {
  const priorities = FRADashboardUI.buildPriorities(null, {
    fra: { pending_cases: 4 }, dss: { insufficient_data_cases: 2 },
    missing_inputs: [{ fact: 'water_stress', count: 3 }],
  });
  assert.deepEqual(priorities.map((item) => item.title), [
    'Complete evidence for scheme screening',
    'Inspect pending FRA cases',
  ]);
});

test('dashboard renders an action-first briefing and compact readiness summaries', async () => {
  const ui = browser(['dashboard.js']);
  ui.emit('fra:section', { section: 'dashboard' });
  ui.pending('/api/fra/dashboard/planner')[0].resolve({
    fra: { claims: 4, titles: 2, active_titles: 2, pending_cases: 1, decisions: 3 },
    claims_by_status: { submitted: 1, granted: 3 },
    claims_by_right_type: { IFR: 2, CFR: 2 },
    spatial: { fra_area_sqm: 50000, granted_area_sqm: 30000, villages_covered: 3, spatialized_claims: 3, unmapped_claims: 1, asset_distribution: { water_body: 4 } },
    development: { water_availability: { mapped_assets: 4, recorded_deficiencies: 1 } },
    dss: {
      scheme_convergence: { potentially_eligible: 2, not_indicated: 1, insufficient_data: 1, not_evaluated: 0 },
      insufficient_data_cases: 1,
      recommended_interventions: [{ intervention: 'household_water_connection', count: 2 }],
      scheme_convergence_by_programme: [{ scheme_code: 'JJM', status: 'potentially_eligible', count: 2 }],
    },
    missing_inputs: [{ fact: 'groundwater_status', count: 1 }], referrals: [],
  });
  ui.pending('/api/fra/dashboard/verifier')[0].resolve({
    totals: { archive_records_needing_review: 5, claims_awaiting_review: 2 },
    queues: { archive_review: [{ reference: 'LEG-1', workspace: '/fra#archive' }], claims_review: [] },
  });
  await flush();
  assert.equal(ui.node('#dashboardAttentionTotal').textContent, '7');
  assert.equal(ui.node('#dashboardTotalClaims').textContent, '4');
  assert.equal(ui.node('#dashboardGrantedCases').textContent, '3');
  assert.equal(ui.node('#dashboardPendingCases').textContent, '1');
  assert.equal(ui.node('#dashboardSpatializedClaims').textContent, '3 of 4');
  assert.equal(ui.node('#dashboardIFRCount').textContent, '2');
  assert.equal(ui.node('#dashboardCFRCount').textContent, '2');
  assert.match(ui.node('#dashboardPriorityQueue').textContent, /Review extracted legacy records/);
  assert.match(ui.node('#dashboardRightsReadiness').textContent, /2 active titles/);
  assert.match(ui.node('#dashboardPlanningReadiness').textContent, /2 potential candidates/);
});
