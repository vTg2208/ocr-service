const assert = require('node:assert/strict');
const test = require('node:test');

const FRAWorkspace = require('../app/static/fra/app.js');
const FRAArchiveUI = require('../app/static/fra/archive.js');
const FRAApi = require('../app/static/fra/api.js');

test('workspace preserves Tamil Nadu context between sections', () => {
  const state = FRAWorkspace.reduce(FRAWorkspace.initialState(),
    { type: 'context', value: { district: 'Thanjavur', village: 'Kottur Demo' } });
  const atlas = FRAWorkspace.reduce(state, { type: 'section', value: 'atlas' });
  assert.equal(atlas.context.village, 'Kottur Demo');
  assert.equal(atlas.context.state, 'TN');
  assert.equal(atlas.section, 'atlas');
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
