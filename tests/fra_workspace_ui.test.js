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

test('workspace includes the operational dashboard without changing the Atlas', () => {
  assert.equal(FRAWorkspace.SECTIONS.includes('dashboard'), true);
  assert.equal(FRAWorkspace.SECTIONS.includes('satellite'), false);
});

test('archive batch upload requires source context and reports mixed outcomes precisely', () => {
  assert.equal(FRAArchiveUI.canUploadBatch({ fileCount: 2, sourceOffice: 'DTWO', district: 'Salem' }), true);
  assert.equal(FRAArchiveUI.canUploadBatch({ fileCount: 0, sourceOffice: 'DTWO', district: 'Salem' }), false);
  assert.equal(FRAArchiveUI.canUploadBatch({ fileCount: 1, sourceOffice: '', district: 'Salem' }), false);
  assert.equal(
    FRAArchiveUI.batchSummary({ accepted: 2, rejected: 1, replayed: false }),
    '2 files queued; 1 file rejected.',
  );
  assert.equal(
    FRAArchiveUI.batchSummary({ accepted: 2, rejected: 0, replayed: true }),
    'Existing batch restored: 2 files already queued.',
  );
});

test('atlas presents asset features with their class-specific visual', () => {
  const presentation = FRAAtlasUI.featurePresentation(
    { kind: 'asset', asset_class: 'forest_cover', verification_state: 'verified' },
    FRAAssetsUI.visualFor,
  );

  assert.equal(presentation.name, 'Forest cover');
  assert.equal(presentation.spritePosition, '-25px -41px');
  assert.equal(presentation.color, '#2f6b3c');
  assert.equal(presentation.meta, 'verified');
});

test('atlas keeps non-asset feature presentation unchanged', () => {
  const presentation = FRAAtlasUI.featurePresentation(
    { kind: 'claim', claim_number: 'TN-IFR-001', right_type: 'IFR', status: 'submitted' },
    FRAAssetsUI.visualFor,
  );

  assert.equal(presentation.name, 'TN-IFR-001');
  assert.equal(presentation.spritePosition, null);
  assert.equal(presentation.meta, 'IFR · submitted');
});

test('asset and DSS copy never presents automation as a decision', () => {
  assert.match(FRAAssetsUI.legalRole(), /supporting evidence/i);
  assert.match(FRAPlannerUI.disclaimer(), /does not approve or sanction/i);
  assert.doesNotMatch(`${FRAAssetsUI.legalRole()} ${FRAPlannerUI.disclaimer()}`, /legally valid/i);
});

test('planner derives versioned facts for an explicit native claim', () => {
  assert.deepEqual(FRAPlannerUI.derivePayload('claim-1'), {
    claim_id: 'claim-1', derivation_version: 'tn-facts-v1',
  });
  assert.throws(() => FRAPlannerUI.derivePayload(''), /Select a native FRA claim/);
});

test('asset visuals map Tamil Nadu asset classes to the contact-sheet sprite', () => {
  const forest = FRAAssetsUI.visualFor('forest_cover');
  const pipeline = FRAAssetsUI.visualFor('pipeline');

  assert.equal(forest.label, 'Forest cover');
  assert.equal(pipeline.label, 'Pipeline');
  assert.match(forest.spritePosition, /^-[0-9]+px -[0-9]+px$/);
  assert.match(pipeline.spritePosition, /^-[0-9]+px -[0-9]+px$/);
  assert.notEqual(forest.spritePosition, pipeline.spritePosition);
});

test('asset visuals provide aliases and a readable fallback', () => {
  assert.equal(FRAAssetsUI.visualFor('well').key, 'open_well');
  assert.equal(FRAAssetsUI.visualFor('agricultural_land').key, 'agricultural_cover');
  assert.deepEqual(
    FRAAssetsUI.visualFor('future_asset'),
    {
      key: 'default_asset',
      label: 'Future asset',
      color: '#4f6258',
      spritePosition: '-394px -422px',
    },
  );
});

test('asset taxonomy and legend cover all supplied icon classes without duplicates', () => {
  const options = FRAAssetsUI.assetOptions();
  assert.equal(options.length, 33);
  assert.equal(new Set(options.map((item) => item.value)).size, options.length);
  assert.deepEqual(
    FRAAssetsUI.legendClasses([
      { asset_class: 'pipeline' },
      { asset_class: 'forest_cover' },
      { asset_class: 'pipeline' },
    ]),
    ['forest_cover', 'pipeline'],
  );
});

test('report URLs are constrained to known protected subjects', () => {
  assert.equal(FRAReportsUI.reportUrl('villages', 'v-1'), '/api/fra/reports/villages/v-1');
  assert.equal(FRAReportsUI.reportUrl('archive', 'r 1'), '/api/fra/reports/archive/r%201');
  assert.equal(FRAReportsUI.reportUrl('unknown', 'x'), null);
  assert.equal(FRAReportsUI.historicalEvidenceUrl('c 1'), '/api/fra/reports/claims/c%201/historical-evidence');
});
