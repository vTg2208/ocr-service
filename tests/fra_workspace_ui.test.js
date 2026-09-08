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

test('satellite and report workspaces choose populated FRA context by default', () => {
  const villages = [
    { id: 'v-1', village_name: 'Aranya Malai' },
    { id: 'v-2', village_name: 'Kottur' },
  ];
  assert.equal(FRAAssetsUI.preferredClaimId([
    { id: 'claim-without-map', geometry_version_count: 0 },
    { id: 'claim-with-map', geometry_version_count: 1 },
  ]), 'claim-with-map');
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
  const filters = { district: 'Thanjavur', right_type: 'IFR', status: 'granted', claimant_category: 'ST', min_area_sqm: 500 };
  assert.equal(FRAAtlasUI.query(filters), 'district=Thanjavur&claimant_category=ST&right_type=IFR&status=granted&min_area_sqm=500');
});

test('workspace opens on the Dashboard and exposes only the six final top-level workspaces', () => {
  assert.equal(FRAWorkspace.initialState().section, 'dashboard');
  assert.deepEqual(FRAWorkspace.SECTIONS, [
    'dashboard', 'atlas', 'cases', 'assets', 'planner', 'reports',
  ]);
  assert.equal(FRAWorkspace.VIEWS.includes('archive'), true);
});

test('atlas statistics expose FRA totals and hierarchy drill targets', () => {
  const rows = FRAAtlasUI.summaryRows({
    claim_count: 9,
    by_right_type: { IFR: 4, CR: 3, CFR: 2 },
    granted_claim_count: 5,
    rejected_claim_count: 1,
    pending_claim_count: 3,
    claimed_area_sqm: 12000,
    granted_area_sqm: 7000,
  });
  assert.deepEqual(rows.map((row) => row.label), [
    'Total claims', 'IFR claims', 'CR claims', 'CFR claims',
    'Granted', 'Rejected', 'Pending', 'Claimed area', 'Granted area',
  ]);
  assert.deepEqual(FRAAtlasUI.drillDetail({
    level: 'village', state: 'Tamil Nadu', district: 'Thanjavur',
    block: 'Kumbakonam', village: 'Kottur',
  }), { district: 'Thanjavur', block: 'Kumbakonam', village: 'Kottur' });
});

test('atlas opens only authorized claim and title features in the case record', () => {
  assert.deepEqual(
    FRAAtlasUI.caseTarget({ kind: 'claim', claim_id: 'claim-1' }),
    { claimId: 'claim-1', tab: 'evidence' },
  );
  assert.deepEqual(
    FRAAtlasUI.caseTarget({ kind: 'title', claim_id: 'claim-1' }),
    { claimId: 'claim-1', tab: 'titles' },
  );
  assert.equal(FRAAtlasUI.caseTarget({ kind: 'claim' }), null);
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

test('legacy register upload reports imported rows separately from scanned files', () => {
  assert.equal(
    FRAArchiveUI.canUploadTabular({ filename: 'legacy.xlsx', sourceOffice: 'DTWO', district: 'Salem' }),
    true,
  );
  assert.equal(
    FRAArchiveUI.canUploadTabular({ filename: 'legacy.pdf', sourceOffice: 'DTWO', district: 'Salem' }),
    false,
  );
  assert.equal(
    FRAArchiveUI.tabularSummary({ accepted: 12, replayed: false }),
    '12 rows added to the review queue.',
  );
  assert.equal(
    FRAArchiveUI.tabularSummary({ accepted: 1, replayed: true }),
    'Existing register restored: 1 row is already in the review queue.',
  );
});

test('archive review metadata exposes page confidence and ambiguous candidates', () => {
  assert.equal(
    FRAArchiveUI.fieldReviewMeta({
      source_page: 2,
      confidence: 0.72,
      ambiguous: true,
      candidates: [{ value: 'Kottur' }, { value: 'Nagalur' }],
    }),
    'Page 2 · 72% confidence · Ambiguous: Kottur | Nagalur',
  );
  assert.equal(
    FRAArchiveUI.fieldReviewMeta({ source_row: 7, source_header: 'Survey No' }),
    'Row 7 · Source: Survey No',
  );
});

test('archive field evidence exposes the complete value-to-review chain', () => {
  const rows = Object.fromEntries(FRAArchiveUI.fieldEvidenceRows({
    value: 'Kottur', source: 'District Tribal Welfare Office',
    document: { filename: 'fra-register.pdf' }, locator: { page: 3 },
    extraction: { method: 'ner', model_version: 'fra-ner-v2', confidence: 0.81 },
    reviewer: { display_name: 'Verifier', review_state: 'approved', reviewed_at: '2026-09-06' },
    final_value: 'Kottur',
  }));
  assert.equal(rows.Value, 'Kottur');
  assert.equal(rows.Source, 'District Tribal Welfare Office');
  assert.equal(rows.Document, 'fra-register.pdf');
  assert.equal(rows['Page / row'], 'Page 3');
  assert.equal(rows.Extraction, 'ner · fra-ner-v2 · 81% confidence');
  assert.match(rows.Reviewer, /Verifier · approved/);
  assert.equal(rows['Final value'], 'Kottur');
});

test('archive workspace renders field evidence chains returned to a reviewer', async () => {
  const ui = browser([], { workspace: true });
  ui.pending('/api/auth/session')[0].resolve({ display_name: 'Reviewer' }); await flush();
  ui.pending('/api/fra/villages')[0].resolve({ items: [] }); await flush();
  ui.pending('/api/fra/archive/records')[0].resolve({ items: [{ id: 'record-1', legacy_reference: 'LEGACY-1', review_state: 'needs_review' }] }); await flush();
  ui.pending('/api/fra/archive/records/record-1')[0].resolve({
    id: 'record-1', revision: 0, legacy_reference: 'LEGACY-1', review_state: 'needs_review', reviewed_fields: {},
    source_document: { source: 'District office', filename: 'fra.pdf' },
    extraction_runs: [{ entity_model_version: 'fra-ner-v2', standardized_fields: { holder_name: 'Ramu' }, field_evidence: {}, field_reviews: [{
      field_name: 'holder_name', evidence_chain: { value: 'Ramu', source: 'District office', document: { filename: 'fra.pdf' }, locator: { page: 1 }, extraction: { method: 'ner', model_version: 'fra-ner-v2', confidence: 0.9 }, reviewer: null, final_value: null },
    }], provenance: {}, raw_text: 'OCR', confidence: 0.9 }],
  }); await flush();
  assert.match(ui.node('#reviewedFields').textContent, /View evidence chainValueRamuSourceDistrict officeDocumentfra.pdfPage \/ rowPage 1/);
  assert.match(ui.node('#extractionProvenance').textContent, /SourceDistrict officeDocumentfra.pdf/);
  ui.node('#reviewNotes').value = 'The scan is not an FRA record.';
  ui.node('#rejectExtractionButton').click(); await flush();
  assert.deepEqual(JSON.parse(ui.pending('/api/fra/archive/records/record-1/reject')[0].options.body), {
    expected_revision: 0, reason: 'The scan is not an FRA record.',
  });
});

test('satellite asset evidence exposes imagery, date, model, confidence, and geometry', () => {
  const rows = Object.fromEntries(FRAAssetsUI.evidenceRows({
    asset_class: 'water_body', geometry: { type: 'Point', coordinates: [79, 10] },
    evidence_chain: {
      asset: { class: 'water_body', subtype: 'pond' },
      imagery: { reference: 'S2-scene-1', source_type: 'model' }, date: '2026-01-15',
      model: { name: 'fra-assets', version: '1.0' }, confidence: 0.92,
      geometry: { type: 'Point', coordinates: [79, 10] },
    },
  }));
  assert.equal(rows.Asset, 'Water body · pond');
  assert.equal(rows.Imagery, 'S2-scene-1 · model');
  assert.equal(rows.Date, '2026-01-15');
  assert.equal(rows.Model, 'fra-assets · 1.0');
  assert.equal(rows.Confidence, '92%');
  assert.equal(rows.Geometry, 'Point');
});

test('asset workspace renders the satellite evidence chain inline', async () => {
  const ui = browser(['assets.js']);
  ui.emit('fra:section', { section: 'assets' });
  ui.pending('/api/fra/cases')[0].resolve({ items: [] }); await flush();
  ui.pending('/api/fra/assets')[0].resolve({ items: [{
    asset_class: 'water_body', source_type: 'model', confidence: 0.92,
    verification_state: 'verified', geometry: { type: 'Point', coordinates: [79, 10] },
    evidence_chain: { asset: { class: 'water_body', subtype: 'pond' }, imagery: { reference: 'S2-scene-1', source_type: 'model' }, date: '2026-01-15', model: { name: 'fra-assets', version: '1.0' }, confidence: 0.92, geometry: { type: 'Point', coordinates: [79, 10] } },
  }] }); await flush();
  assert.match(ui.node('#assetList').textContent, /View evidence chainAssetWater body · pondImageryS2-scene-1 · modelDate2026-01-15Modelfra-assets · 1.0Confidence92%GeometryPoint/);
});

test('asset reviewer controls submit a version-checked human decision', async () => {
  assert.deepEqual(FRAAssetsUI.reviewPayload({ revision: 3 }, 'verified'), {
    outcome: 'verified', expected_revision: 3,
    reasons: ['Approved in the Asset Intelligence reviewer workspace.'],
  });
  const ui = browser(['assets.js']); ui.emit('fra:section', { section: 'assets' });
  ui.pending('/api/fra/cases')[0].resolve({ items: [] }); await flush();
  ui.pending('/api/fra/assets')[0].resolve({ items: [{
    id: 'asset-1', revision: 3, asset_class: 'water_body', source_type: 'model',
    verification_state: 'unverified', geometry: null, evidence_chain: {},
  }] }); await flush();
  const content = ui.node('#assetList').children[0].children[1];
  const controls = content.children.at(-1); const approve = controls.children[0];
  assert.equal(approve.textContent, 'Approve detection'); approve.click(); await flush();
  assert.deepEqual(JSON.parse(ui.pending('/api/fra/assets/asset-1/review')[0].options.body), {
    outcome: 'verified', expected_revision: 3,
    reasons: ['Approved in the Asset Intelligence reviewer workspace.'],
  });
});

test('asset rejection requires a reviewer rationale while approval remains concise', () => {
  assert.throws(
    () => FRAAssetsUI.reviewPayload({ revision: 4 }, 'rejected'),
    /Explain why this detection is being rejected/,
  );
  assert.deepEqual(FRAAssetsUI.reviewPayload({ revision: 4 }, 'rejected', 'Seasonal shadow was classified as water.'), {
    outcome: 'rejected', expected_revision: 4,
    reasons: ['Seasonal shadow was classified as water.'],
  });
});

test('imagery preparation defaults to a recent completed observation window', () => {
  assert.deepEqual(FRAAssetsUI.defaultImageryWindow(new Date('2026-09-07T12:00:00Z')), {
    startDate: '2026-06-05',
    endDate: '2026-09-02',
  });
});

test('archive reviewer fields expose controlled vocabularies and bounded numeric inputs', () => {
  assert.deepEqual(FRAArchiveUI.REVIEW_FIELD_CONFIG.right_type.options, ['IFR', 'CR', 'CFR']);
  assert.equal(FRAArchiveUI.REVIEW_FIELD_CONFIG.latitude.min, -90);
  assert.equal(FRAArchiveUI.REVIEW_FIELD_CONFIG.longitude.max, 180);
  assert.equal(FRAArchiveUI.REVIEW_FIELD_CONFIG.decision_date.type, 'date');
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

test('atlas presents imagery coverage and opens its owning FRA evidence case', () => {
  const presentation = FRAAtlasUI.featurePresentation(
    {
      kind: 'imagery', name: 'Satellite imagery coverage · 2025',
      layer: 'satellite_imagery', collection: 'sentinel-2-l2a',
      verification_state: 'unverified',
    },
    FRAAssetsUI.visualFor,
  );

  assert.equal(presentation.name, 'Satellite imagery coverage · 2025');
  assert.equal(
    presentation.meta,
    'satellite_imagery · sentinel-2-l2a · unverified',
  );
  assert.deepEqual(
    FRAAtlasUI.caseTarget({ kind: 'imagery', claim_id: 'claim-1' }),
    { claimId: 'claim-1', tab: 'evidence' },
  );
});

test('asset and DSS copy never presents automation as a decision', () => {
  assert.match(FRAAssetsUI.legalRole(), /supporting evidence/i);
  assert.match(FRAPlannerUI.disclaimer(), /does not approve or sanction/i);
  assert.doesNotMatch(`${FRAAssetsUI.legalRole()} ${FRAPlannerUI.disclaimer()}`, /legally valid/i);
});

test('planner derives versioned facts for an explicit native claim', () => {
  assert.deepEqual(FRAPlannerUI.derivePayload('claim-1'), {
    claim_id: 'claim-1', derivation_version: 'fra-dss-facts-v1',
  });
  assert.throws(() => FRAPlannerUI.derivePayload(''), /Select a native FRA claim/);
});

const { browser, flush } = require('./fra_dom_fixture');

test('hidden Atlas invalidates cached context and suppresses old response and errors', async () => {
  const ui = browser(['assets.js', 'atlas.js']);
  ui.node('#contextDistrict').value = 'Salem';
  ui.emit('fra:section', { section: 'atlas' });
  let features = ui.pending('/api/fra/atlas/features')[0]; let summary = ui.pending('/api/fra/atlas/summary')[0];
  features.resolve({ features: [] }); summary.resolve({ claim_count: 1 }); await flush();
  ui.node('#atlasPanel').hidden = true; ui.node('#contextDistrict').value = 'The Nilgiris'; ui.emit('fra:context', {});
  ui.node('#atlasPanel').hidden = false; ui.emit('fra:section', { section: 'atlas' });
  assert.equal(ui.pending('/api/fra/atlas/features').length, 2);
  assert.match(ui.pending('/api/fra/atlas/features')[1].url, /district=The\+Nilgiris/);
  ui.node('#contextDistrict').value = 'Thanjavur'; ui.emit('fra:context', {});
  ui.pending('/api/fra/atlas/features')[2].resolve({ features: [] });
  ui.pending('/api/fra/atlas/summary')[2].resolve({ claim_count: 7 }); await flush();
  const current = ui.node('#atlasSummary').textContent;
  ui.pending('/api/fra/atlas/features')[1].reject(new Error('Old context failed'));
  ui.pending('/api/fra/atlas/summary')[1].resolve({ claim_count: 99 }); await flush();
  assert.equal(ui.node('#atlasSummary').textContent, current);
  assert.match(current, /Total claims7/);
});

test('planner filters recommendations and claim choices by shared context and preserves selection', async () => {
  const ui = browser(['planner.js']);
  ui.node('#contextDistrict').value = 'Salem';
  ui.node('#recommendationFilters').elements.outcome.value = 'insufficient_data';
  ui.node('#recommendationFilters').elements.scheme_code.value = 'TEST';
  ui.node('#recommendationFilters').elements.right_type.value = 'CFR';
  ui.node('#recommendationFilters').elements.intervention_type.value = 'drinking_water';
  ui.node('#plannerClaim').value = 'claim-1';
  ui.emit('fra:section', { section: 'planner' });
  assert.match(ui.pending('/api/fra/dss/convergence')[0].url, /outcome=insufficient_data&scheme_code=TEST&right_type=CFR&intervention_type=drinking_water&district=Salem/);
  assert.match(ui.pending('/api/fra/cases')[0].url, /district=Salem/);
  ui.pending('/api/fra/dss/convergence')[0].resolve({ items: [] });
  ui.pending('/api/fra/cases')[0].resolve({ items: [{ id: 'claim-1', claim_number: 'TN-1', right_type: 'IFR' }] }); await flush();
  assert.equal(ui.node('#plannerClaim').value, 'claim-1');
  ui.node('#deriveRecommendations').click();
  ui.pending('/api/fra/dss/derive-and-evaluate')[0].resolve({ fact_snapshot: { derivation_version: 'fra-dss-facts-v1', facts: {} }, recommendations: [{ scheme_name: 'Must obey filters' }] }); await flush();
  assert.equal(ui.pending('/api/fra/dss/convergence').length, 2);
  assert.match(ui.pending('/api/fra/dss/convergence')[1].url, /outcome=insufficient_data/);
  ui.node('#contextDistrict').value = 'Other'; ui.emit('fra:context', {});
  ui.pending('/api/fra/dss/convergence')[2].resolve({ items: [] }); ui.pending('/api/fra/cases')[2].resolve({ items: [] }); await flush();
  ui.pending('/api/fra/dss/convergence')[1].reject(new Error('Old query failed')); ui.pending('/api/fra/cases')[1].resolve({ items: [{ id: 'claim-1' }] }); await flush();
  assert.equal(ui.node('#plannerClaim').value, '');
  assert.doesNotMatch(ui.node('#recommendationList').textContent, /Old query|Must obey/);
});

test('planner renders the complete water convergence result and spatial evidence', async () => {
  const ui = browser(['planner.js']);
  ui.emit('fra:section', { section: 'planner' });
  ui.pending('/api/fra/dss/scheme-catalog')[0].resolve({ items: [{
    scheme_code: 'JJM', display_name: 'Jal Jeevan Mission planning reference', active: false,
    definition: { intervention_types: ['drinking_water', 'water_source_strengthening'] },
  }] });
  ui.pending('/api/fra/dss/convergence')[0].resolve({ summary: {
    potentially_eligible: 1, not_indicated: 0, insufficient_data: 0, not_evaluated: 0,
  }, items: [{
    scheme_name: 'Jal Jeevan Mission planning reference', claim_number: 'TN-FRA-WATER-1',
    convergence_status: 'potentially_eligible', reason_for_recommendation: 'Refer the FRA village and supporting water evidence for Jal Jeevan Mission convergence review.',
    reason_for_rejection: null, reasons: ['water_source_present (False) equals False.'],
    missing_prerequisites: [], missing_information: [],
    supporting_evidence: [
      { fact: 'water_source_present', verification_state: 'verified', source_entity_type: 'imagery_artifact', observed_at: '2026-08-01' },
      { fact: 'groundwater_status', verification_state: 'verified', source_entity_type: 'spatial_reference_feature' },
    ],
    catalog_version: 'tn-draft-2026-02', catalog_status: 'draft_inactive',
    rule_version: 'tn-sample-4', recommendation_id: 'recommendation-1', priority: 'urgent',
    claim_status: 'granted', mapped_assets: [{ asset: 'water_body' }, { asset: 'infrastructure' }],
    deficiencies: ['drinking_water_gap'], recommended_interventions: ['drinking_water'],
    evidence_completeness_percent: 100, unknown_data: [],
  }] });
  ui.pending('/api/fra/cases')[0].resolve({ items: [] }); await flush();
  const content = ui.node('#recommendationList').textContent;
  assert.match(content, /Jal Jeevan Mission/);
  assert.match(content, /Potentially eligible/);
  assert.match(content, /Verified water source present: Verified/);
  assert.match(content, /Groundwater condition: Verified/);
  assert.match(content, /Satellite imagery observation/);
  assert.match(content, /Published spatial reference/);
  assert.match(content, /Draft inactive/);
  assert.match(content, /FRA status: Granted/);
  assert.match(content, /Mapped assets: Water body; Infrastructure/);
  assert.match(content, /Information gaps: Household drinking-water access needs attention/);
  assert.match(content, /Suggested intervention: Drinking water/);
  assert.match(content, /Evidence completeness: 100%/);
  assert.match(content, /Send for human review/);
  assert.equal(ui.node('#plannerCandidateCount').textContent, '1');
  assert.match(ui.node('#plannerScheme').textContent, /Jal Jeevan Mission/);
  assert.match(ui.node('#plannerIntervention').textContent, /drinking water/);
});

test('assets reload scoped spatial FRA claims and ignore old list and reference results', async () => {
  const ui = browser(['assets.js']);
  ui.emit('fra:section', { section: 'assets' });
  ui.node('#contextDistrict').value = 'Salem'; ui.emit('fra:context', {});
  ui.pending('/api/fra/cases')[1].resolve({ items: [{ id: 'new', claim_number: 'TN-FRA-NEW', geometry_version_count: 1 }] }); await flush();
  ui.pending('/api/fra/assets')[0].resolve({ items: [] }); await flush();
  ui.pending('/api/fra/cases')[0].resolve({ items: [{ id: 'old', claim_number: 'OLD', geometry_version_count: 1 }] }); await flush();
  assert.equal(ui.node('#imageryClaim').value, 'new');
  assert.match(ui.pending('/api/fra/cases')[1].url, /district=Salem/);
  ui.node('#contextDistrict').value = 'Other'; ui.emit('fra:context', {});
  ui.pending('/api/fra/assets')[1].reject(new Error('Stale assets failed')); await flush();
  assert.doesNotMatch(ui.node('#imageryJobStatus').textContent, /Stale/);
});

test('satellite ingestion payload uses real collection bands and bounded cloud input', () => {
  assert.deepEqual(FRAAssetsUI.imageryPayload({
    startDate: '2025-01-01', endDate: '2025-03-31',
    collection: 'sentinel-2-l2a', bandKeys: 'green, nir', maxCloud: '20',
  }), {
    start_date: '2025-01-01', end_date: '2025-03-31',
    collection: 'sentinel-2-l2a', band_keys: ['green', 'nir'], max_cloud: 20,
  });
  assert.throws(() => FRAAssetsUI.imageryPayload({
    startDate: '2025-03-31', endDate: '2025-01-01', collection: 'sentinel-2-l2a',
    bandKeys: 'green,nir', maxCloud: '20',
  }), /date range/);
});

test('archive context changes suppress stale list/detail results and clear incompatible village', async () => {
  const ui = browser([], { workspace: true });
  ui.pending('/api/auth/session')[0].resolve({ display_name: 'Reviewer' }); await flush();
  ui.pending('/api/fra/villages')[0].resolve({ items: [
    { district_name: 'Salem', block_name: 'A', village_name: 'One' },
    { district_name: 'Salem', block_name: 'B', village_name: 'Two' },
  ] }); await flush();
  ui.pending('/api/fra/archive/records')[0].resolve({ items: [{ id: 'old', legacy_reference: 'OLD', review_state: 'reviewed' }] }); await flush();
  const oldDetail = ui.pending('/api/fra/archive/records/old')[0];
  ui.node('#contextDistrict').value = 'Salem'; ui.node('#contextBlock').value = 'B'; ui.node('#contextVillage').value = 'One';
  await ui.node('#contextBlock').emit('change'); await flush();
  assert.equal(ui.node('#contextVillage').value, '');
  const currentList = ui.pending('/api/fra/archive/records').at(-1); assert.match(currentList.url, /block=B/);
  currentList.resolve({ items: [] }); await flush();
  oldDetail.reject(new Error('Old record failed')); await flush();
  assert.match(ui.node('#recordReference').textContent, /No archive record/);
  assert.doesNotMatch(ui.node('#workspaceStatus').textContent, /Old record/);
  assert.equal(ui.node('#promoteButton').disabled, true);
});

test('asset visuals map Tamil Nadu asset classes to the contact-sheet sprite', () => {
  const forest = FRAAssetsUI.visualFor('forest_cover');
  const pipeline = FRAAssetsUI.visualFor('pipeline');

  assert.equal(forest.label, 'Forest cover');
  assert.equal(pipeline.label, 'Infrastructure');
  assert.match(forest.spritePosition, /^-[0-9]+px -[0-9]+px$/);
  assert.match(pipeline.spritePosition, /^-[0-9]+px -[0-9]+px$/);
  assert.notEqual(forest.spritePosition, pipeline.spritePosition);
});

test('asset visuals provide aliases and a readable fallback', () => {
  assert.equal(FRAAssetsUI.visualFor('well').key, 'infrastructure');
  assert.equal(FRAAssetsUI.visualFor('agricultural_cover').key, 'agricultural_land');
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
  assert.equal(options.length, 7);
  assert.equal(new Set(options.map((item) => item.value)).size, options.length);
  assert.deepEqual(
    FRAAssetsUI.legendClasses([
      { asset_class: 'pipeline' },
      { asset_class: 'forest_cover' },
      { asset_class: 'pipeline' },
    ]),
    ['forest_cover', 'infrastructure'],
  );
});

test('village asset profile summary exposes reviewed planning metrics and pending review', () => {
  assert.deepEqual(FRAAssetsUI.profileSummary({
    verified_asset_count: 4, pending_asset_count: 2,
    metrics: { agricultural_area_sqm: 12500, forest_area_sqm: 5000, water_body_count: 3, homestead_count: 7 },
  }), [
    'Agriculture 1.25 ha', 'Forest 0.50 ha', 'Water bodies 3',
    'Homesteads 7', 'Within village 0', 'Intersect FRA land 0',
    'Near FRA land 0', 'Mapped coverage 0.0%', 'Reviewed 4', 'Pending 2',
  ]);
});

test('report URLs are constrained to known protected subjects', () => {
  assert.equal(FRAReportsUI.reportUrl('villages', 'v-1'), '/api/fra/reports/villages/v-1');
  assert.equal(FRAReportsUI.reportUrl('archive', 'r 1'), '/api/fra/reports/archive/r%201');
  assert.equal(FRAReportsUI.reportUrl('unknown', 'x'), null);
  assert.equal(FRAReportsUI.historicalEvidenceUrl('c 1'), '/api/fra/reports/claims/c%201/historical-evidence');
});
