const assert = require('node:assert/strict');
const test = require('node:test');

const ui = require('../app/static/land-mapping/app.js');
const registry = require('../app/static/land-mapping/claimed-land.js');

function fakeDocument() {
  return {
    createElement(tagName) {
      return {
        tagName,
        className: '',
        textContent: '',
        children: [],
        listeners: {},
        appendChild(child) { this.children.push(child); },
        append(...children) { this.children.push(...children); },
        addEventListener(name, listener) { this.listeners[name] = listener; },
      };
    },
  };
}

function fakeContainer() {
  return {
    children: ['stale'],
    replaceChildren() { this.children = []; },
    appendChild(child) { this.children.push(child); },
    set innerHTML(_) { throw new Error('Dynamic API content must not use innerHTML'); },
  };
}

test('workflow stage follows the actual upload, review, register, complete sequence', () => {
  assert.equal(ui.workflowStage({ documentId: null, confirmed: false, claimStatus: null }), 'upload');
  assert.equal(ui.workflowStage({ documentId: 'doc-1', confirmed: false, claimStatus: null }), 'review');
  assert.equal(ui.workflowStage({ documentId: 'doc-1', confirmed: true, claimStatus: null }), 'register');
  assert.equal(ui.workflowStage({ documentId: 'doc-1', confirmed: true, claimStatus: 'matched' }), 'complete');
});

test('warnings from the API are rendered as text rather than executable markup', () => {
  const container = fakeContainer();
  ui.renderWarnings(container, ['<img src=x onerror=alert(1)>'], fakeDocument());
  assert.equal(container.children.length, 1);
  assert.equal(container.children[0].textContent, '<img src=x onerror=alert(1)>');
});

test('candidate buttons safely preserve registry text and select the underlying item', () => {
  const container = fakeContainer();
  const candidate = {
    survey_number: '701', subdivision_number: '4B', village: '<svg/onload=alert(1)>',
  };
  let selected = null;
  ui.renderCandidates(container, [candidate], (item) => { selected = item; }, fakeDocument());

  const button = container.children[0];
  assert.equal(button.children[0].textContent, 'Survey 701 / 4B');
  assert.equal(button.children[1].textContent, '<svg/onload=alert(1)>');
  button.listeners.click();
  assert.equal(selected, candidate);
});

test('feedback updates preserve layout classes while replacing status styling', () => {
  const element = {
    className: 'feedback workspace-feedback feedback--info',
    textContent: '',
  };

  ui.setFeedback(element, 'success', 'Parcel match updated.');

  assert.match(element.className, /workspace-feedback/);
  assert.match(element.className, /feedback--success/);
  assert.doesNotMatch(element.className, /feedback--info/);
  assert.equal(element.textContent, 'Parcel match updated.');
});

test('OCR confidence label does not imply that document fields were extracted', () => {
  assert.equal(ui.ocrQualityLabel({ confidence: 0.9786 }), '98% OCR quality');
  assert.equal(ui.ocrQualityLabel({ confidence: null }), '— OCR quality');
});

test('processing feedback distinguishes a missing registry parcel from extraction success', () => {
  assert.deepEqual(
    ui.processingFeedback({ status: 'not_found', missing_fields: [] }),
    {
      type: 'warning',
      message: 'Document fields were extracted, but this parcel is not available in the registry.',
    },
  );
  assert.deepEqual(
    ui.processingFeedback({ status: 'insufficient_data', missing_fields: ['village', 'survey_number'] }),
    {
      type: 'warning',
      message: 'Text was read, but Village and Survey number could not be identified. Check these fields.',
    },
  );
});

test('parcel status names an unavailable registry record precisely', () => {
  assert.equal(ui.matchLabel('not_found'), 'Not in registry');
});

test('map is shown only when an official parcel boundary is available', () => {
  assert.deepEqual(ui.parcelMapPresentation({ parcel: null }), {
    showMap: false,
    emptyMessage: 'Import the official registry boundary to display this parcel.',
  });
  assert.deepEqual(ui.parcelMapPresentation({ parcel: { id: 'parcel-1' } }), {
    showMap: true,
    emptyMessage: '',
  });
});

test('browser session redirects to login when authentication is missing', async () => {
  let redirected = null;
  const result = await ui.ensureBrowserSession(
    async () => ({ ok: false, status: 401 }),
    (url) => { redirected = url; },
  );

  assert.equal(result, null);
  assert.equal(redirected, '/login');
});

test('browser session returns staff identity without exposing a token', async () => {
  const staff = await ui.ensureBrowserSession(
    async () => ({
      ok: true,
      json: async () => ({
        external_id: 'registry-demo', display_name: 'Registry staff', role: 'user',
      }),
    }),
    () => { throw new Error('unexpected redirect'); },
  );

  assert.deepEqual(staff, {
    external_id: 'registry-demo', display_name: 'Registry staff', role: 'user',
  });
  assert.equal(Object.hasOwn(staff, 'token'), false);
});

test('claimed registry view model keeps persisted polygons and selects a requested claim', () => {
  const model = registry.registryViewModel({
    summary: { claimed_parcel_count: 2, claimed_official_area_sqm: 3200 },
    claims: [{ claim_id: 'a' }, { claim_id: 'b' }],
  }, 'b');

  assert.equal(model.summaryText, '2 claimed parcels · 3,200 m²');
  assert.equal(model.selected.claim_id, 'b');
  assert.equal(model.claims.length, 2);
});

test('claimed registry view model handles an empty persisted registry', () => {
  const model = registry.registryViewModel({ summary: {}, claims: [] }, null);
  assert.equal(model.summaryText, 'No claimed parcels');
  assert.equal(model.selected, null);
  assert.deepEqual(model.claims, []);
});

test('claimed registry search keeps ledger serials stable across official fields and filenames', () => {
  const payload = {
    summary: { claimed_parcel_count: 2, claimed_official_area_sqm: 9200 },
    claims: [
      {
        claim_id: 'claim-751', status: 'matched',
        parcel: {
          state: 'Tamil Nadu', district: 'Thanjavur', taluk: 'Kumbakonam',
          village: 'Example Village', survey_number: '751', subdivision_number: 'Z',
          official_area_sqm: 8700,
        },
        document: { filename: 'patta-751-Z.png' },
      },
      {
        claim_id: 'claim-614', status: 'matched',
        parcel: {
          state: 'Tamil Nadu', district: 'Villupuram', taluk: 'Villupuram',
          village: 'Arpisampalayam', survey_number: '614', subdivision_number: '1B',
          official_area_sqm: 500,
        },
        document: { filename: 'villupuram-patta-614-1B.png' },
      },
    ],
  };

  for (const query of ['614/1b', 'arpisampalayam', 'villupuram', 'patta-614']) {
    const model = registry.registryViewModel(payload, 'claim-751', query);
    assert.equal(model.visibleClaims.length, 1, query);
    assert.equal(model.visibleClaims[0].claim_id, 'claim-614', query);
    assert.equal(model.visibleClaims[0].serialNumber, 2, query);
    assert.equal(model.selected.claim_id, 'claim-614', query);
    assert.equal(model.resultText, '1 of 2 claims');
  }
});

test('claimed registry search preserves the map registry when no list entry matches', () => {
  const model = registry.registryViewModel({
    summary: { claimed_parcel_count: 2 },
    claims: [{ claim_id: 'a' }, { claim_id: 'b' }],
  }, 'a', 'no such parcel');

  assert.equal(model.claims.length, 2);
  assert.deepEqual(model.visibleClaims, []);
  assert.equal(model.selected, null);
  assert.equal(model.resultText, 'No matching claims');
});

test('claim row presentation omits the redundant registry status', () => {
  const item = registry.claimRowPresentation({
    serialNumber: 2,
    status: 'matched',
    parcel: {
      survey_number: '614', subdivision_number: '1B', village: 'Arpisampalayam',
    },
  });

  assert.deepEqual(item, {
    serial: '02', reference: '614/1B', village: 'Arpisampalayam',
  });
  assert.equal(Object.hasOwn(item, 'status'), false);
});

test('selecting the active claim again deselects it', () => {
  assert.equal(registry.nextClaimSelection(null, 'claim-614'), 'claim-614');
  assert.equal(registry.nextClaimSelection('claim-751', 'claim-614'), 'claim-614');
  assert.equal(registry.nextClaimSelection('claim-614', 'claim-614'), null);
});
