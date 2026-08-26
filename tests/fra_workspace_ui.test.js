const assert = require('node:assert/strict');
const test = require('node:test');

const FRAWorkspace = require('../app/static/fra/app.js');
const FRAArchiveUI = require('../app/static/fra/archive.js');
const FRAApi = require('../app/static/fra/api.js');
const FRAAtlasUI = require('../app/static/fra/atlas.js');
const FRAAssetsUI = require('../app/static/fra/assets.js');
const FRAPlannerUI = require('../app/static/fra/planner.js');
const FRAReportsUI = require('../app/static/fra/reports.js');

test('workspace preserves Tamil Nadu context between sections', () => {
  const state = FRAWorkspace.reduce(FRAWorkspace.initialState(),
    { type: 'context', value: { district: 'Thanjavur', village: 'Kottur' } });
  const atlas = FRAWorkspace.reduce(state, { type: 'section', value: 'atlas' });
  assert.equal(atlas.context.village, 'Kottur');
  assert.equal(atlas.context.state, 'TN');
  assert.equal(atlas.section, 'atlas');
});

test('workspace selects a populated archive record by default', () => {
  const records = [{ id: 'a-1' }, { id: 'a-2' }];
  assert.equal(FRAWorkspace.preferredRecord(records, null).id, 'a-1');
  assert.equal(FRAWorkspace.preferredRecord(records, 'a-2').id, 'a-2');
  assert.equal(FRAWorkspace.preferredRecord([], null), null);
});

test('asset and report workspaces choose a populated village by default', () => {
  const villages = [
    { id: 'v-1', village_name: 'Aranya Malai' },
    { id: 'v-2', village_name: 'Kottur' },
  ];
  assert.equal(FRAAssetsUI.preferredVillageId(villages), 'v-2');
  assert.equal(FRAReportsUI.preferredVillageId(villages), 'v-2');
  assert.equal(FRAReportsUI.archiveRecordId({ id: 'archive-1' }), 'archive-1');
});

test('workspace rejects unknown sections without losing current state', () => {
  const current = FRAWorkspace.reduce(FRAWorkspace.initialState(), { type: 'section', value: 'assets' });
  assert.deepEqual(FRAWorkspace.reduce(current, { type: 'section', value: 'legal-validity' }), current);
});

test('archive empty state distinguishes no records from no search matches', () => {
  assert.match(FRAArchiveUI.emptyState([], ''), /No archive records/);
  assert.match(FRAArchiveUI.emptyState([{ id: '1' }], 'ramu'), /No matching records/);
  assert.equal(FRAArchiveUI.emptyState([{ id: '1' }], ''), '');
});

test('archive query encodes shared Tamil Nadu filters and pagination', () => {
  assert.equal(
    FRAArchiveUI.query({ district: 'The Nilgiris', review_state: 'needs_review', query: 'Ramu & family' }),
    'district=The+Nilgiris&review_state=needs_review&query=Ramu+%26+family',
  );
});

test('API helper unwraps application errors without leaking markup', async () => {
  await assert.rejects(
    FRAApi.request('/x', {}, async () => ({
      ok: false, status: 422, json: async () => ({ message: { message: '<script>bad</script>' } }),
    })),
    (error) => error.message === '<script>bad</script>' && error.status === 422,
  );
});

test('atlas query uses the same filters for features and summary', () => {
  const filters = { district: 'Thanjavur', right_type: 'IFR', status: 'granted' };
  assert.equal(FRAAtlasUI.query(filters), 'district=Thanjavur&right_type=IFR&status=granted');
});

test('asset and DSS copy never presents automation as a decision', () => {
  assert.match(FRAAssetsUI.legalRole(), /supporting evidence/i);
  assert.match(FRAPlannerUI.disclaimer(), /does not approve or sanction/i);
  assert.doesNotMatch(`${FRAAssetsUI.legalRole()} ${FRAPlannerUI.disclaimer()}`, /legally valid/i);
});

test('report URLs are constrained to known protected subjects', () => {
  assert.equal(FRAReportsUI.reportUrl('villages', 'v-1'), '/api/fra/reports/villages/v-1');
  assert.equal(FRAReportsUI.reportUrl('archive', 'r 1'), '/api/fra/reports/archive/r%201');
  assert.equal(FRAReportsUI.reportUrl('unknown', 'x'), null);
});
