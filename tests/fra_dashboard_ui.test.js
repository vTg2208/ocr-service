const test = require('node:test');
const assert = require('node:assert/strict');

const FRADashboardUI = require('../app/static/fra/dashboard.js');

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
  assert.match(FRADashboardUI.disclaimer(), /operational summaries/i);
  assert.doesNotMatch(FRADashboardUI.disclaimer(), /legal validity is confirmed/i);
});
