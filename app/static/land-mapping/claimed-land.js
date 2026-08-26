const ClaimedLandUI = (() => {
  const numberFormat = new Intl.NumberFormat('en-IN', { maximumFractionDigits: 2 });

  function normalizedSearchValue(value) {
    return String(value ?? '')
      .normalize('NFKC')
      .toLocaleLowerCase('en-IN')
      .replace(/\s+/g, ' ')
      .trim();
  }

  function claimSearchText(claim) {
    const parcel = claim.parcel || {};
    const subdivision = parcel.subdivision_number || '';
    const survey = parcel.survey_number || '';
    return normalizedSearchValue([
      claim.claim_id,
      claim.status,
      claim.document?.filename,
      parcel.state,
      parcel.district,
      parcel.taluk,
      parcel.village,
      survey,
      subdivision,
      `${survey}/${subdivision}`,
    ].join(' '));
  }

  function claimRowPresentation(claim) {
    const parcel = claim.parcel || {};
    const subdivision = parcel.subdivision_number ? `/${parcel.subdivision_number}` : '';
    return {
      serial: String(claim.serialNumber).padStart(2, '0'),
      reference: `${parcel.survey_number || '—'}${subdivision}`,
      village: parcel.village || 'Village not recorded',
    };
  }

  function nextClaimSelection(currentClaimId, requestedClaimId) {
    return currentClaimId === requestedClaimId ? null : requestedClaimId;
  }

  function registryViewModel(payload, selectedClaimId, searchQuery = '') {
    const sourceClaims = Array.isArray(payload?.claims) ? payload.claims : [];
    const claims = sourceClaims.map((claim, index) => ({ ...claim, serialNumber: index + 1 }));
    const count = Number(payload?.summary?.claimed_parcel_count ?? claims.length);
    const area = Number(payload?.summary?.claimed_official_area_sqm ?? 0);
    const query = normalizedSearchValue(searchQuery);
    const queryParts = query.split(' ').filter(Boolean);
    const visibleClaims = queryParts.length
      ? claims.filter((claim) => {
        const searchable = claimSearchText(claim);
        return queryParts.every((part) => searchable.includes(part));
      })
      : claims;
    const requested = visibleClaims.find((claim) => claim.claim_id === selectedClaimId);
    const selected = requested || visibleClaims[0] || null;
    const summaryText = count
      ? `${count} claimed ${count === 1 ? 'parcel' : 'parcels'} · ${numberFormat.format(area)} m²`
      : 'No claimed parcels';
    const resultText = !claims.length
      ? 'No claims registered'
      : !visibleClaims.length
        ? 'No matching claims'
        : query
          ? `${visibleClaims.length} of ${claims.length} claims`
          : `${claims.length} ${claims.length === 1 ? 'claim' : 'claims'}`;
    return { claims, visibleClaims, selected, summaryText, resultText, query };
  }

  function createController({ fetchImpl, leaflet, doc, browserWindow, setFeedback }) {
    const state = {
      map: null,
      layers: new Map(),
      model: null,
      payload: null,
      selected: null,
      allBounds: null,
    };
    const byId = (id) => doc.getElementById(id);

    function createElement(tagName, className = '', text = '') {
      const element = doc.createElement(tagName);
      element.className = className;
      element.textContent = text;
      return element;
    }

    function ensureMap() {
      if (!state.map) {
        state.map = leaflet.map('claimedLandMap').setView([10.96, 79.38], 12);
        leaflet.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', {
          maxZoom: 19,
          attribution: '&copy; OpenStreetMap contributors',
        }).addTo(state.map);
      }
      browserWindow.setTimeout(() => state.map.invalidateSize(), 0);
    }

    function displayDate(value) {
      if (!value) return '—';
      const date = new Date(value);
      return Number.isNaN(date.getTime())
        ? '—'
        : new Intl.DateTimeFormat('en-IN', { dateStyle: 'medium' }).format(date);
    }

    function parcelReference(claim) {
      return claimRowPresentation(claim).reference;
    }

    function appendFact(list, label, value) {
      const item = createElement('div', 'claim-record-fact');
      item.append(
        createElement('dt', '', label),
        createElement('dd', '', value),
      );
      list.appendChild(item);
    }

    function renderSelectedDetails(record, claim) {
      const details = createElement('div', 'claim-record-details');
      details.setAttribute('aria-label', `Details for survey ${parcelReference(claim)}`);
      const facts = createElement('dl', 'claim-record-facts');
      appendFact(facts, 'Official area', claim.parcel?.official_area_sqm == null
        ? '—'
        : `${numberFormat.format(claim.parcel.official_area_sqm)} m²`);
      appendFact(facts, 'Registered', displayDate(claim.submitted_at));
      appendFact(facts, 'District', claim.parcel?.district || '—');
      appendFact(facts, 'Patta file', claim.document?.filename || '—');
      const viewPatta = createElement('button', 'secondary claim-patta-button', 'View original patta');
      viewPatta.type = 'button';
      viewPatta.disabled = !claim.document?.view_url;
      viewPatta.addEventListener('click', () => {
        const url = claim.document?.view_url;
        if (url) browserWindow.open(url, '_blank', 'noopener');
      });
      details.append(facts, viewPatta);
      record.appendChild(details);
    }

    function renderClaimList(scrollSelected = false) {
      const list = byId('claimedLandList');
      list.replaceChildren();
      byId('claimedLandSearchResults').textContent = state.model.resultText;
      byId('claimedLandSearchEmpty').hidden = state.model.visibleClaims.length > 0;

      let selectedRecord = null;
      state.model.visibleClaims.forEach((claim) => {
        const presentation = claimRowPresentation(claim);
        const isSelected = claim.claim_id === state.selected?.claim_id;
        const record = createElement('li', `claim-record${isSelected ? ' is-selected' : ''}`);
        record.dataset.claimId = claim.claim_id;

        const selectButton = createElement('button', 'claim-record-select');
        selectButton.type = 'button';
        selectButton.setAttribute('aria-pressed', String(isSelected));
        selectButton.setAttribute('aria-label', `Claim ${claim.serialNumber}, survey ${parcelReference(claim)}`);

        const serial = createElement('span', 'claim-record-serial', presentation.serial);
        serial.setAttribute('aria-hidden', 'true');
        const identity = createElement('span', 'claim-record-identity');
        identity.append(
          createElement('strong', '', `Survey ${presentation.reference}`),
          createElement('span', '', presentation.village),
        );
        selectButton.append(serial, identity);
        selectButton.addEventListener('click', () => {
          const claimId = nextClaimSelection(state.selected?.claim_id || null, claim.claim_id);
          selectClaim(claimId, { focusMap: true, scrollList: false });
        });
        record.appendChild(selectButton);
        if (isSelected) {
          selectedRecord = record;
          renderSelectedDetails(record, claim);
        }
        list.appendChild(record);
      });

      if (scrollSelected && selectedRecord) {
        browserWindow.setTimeout(() => selectedRecord.scrollIntoView({ block: 'nearest' }), 0);
      }
    }

    function setLayerSelection(claimId) {
      state.layers.forEach((layer, id) => {
        const selected = id === claimId;
        layer.setStyle({
          weight: selected ? 5 : 2,
          fillOpacity: selected ? .48 : .2,
          opacity: selected ? 1 : .72,
        });
        if (selected && typeof layer.bringToFront === 'function') layer.bringToFront();
      });
    }

    function selectClaim(claimId, { focusMap = true, scrollList = false } = {}) {
      const selected = state.model?.claims.find((claim) => claim.claim_id === claimId) || null;
      state.selected = selected;
      if (state.model) state.model.selected = selected;
      setLayerSelection(selected?.claim_id || null);
      renderClaimList(scrollList);

      const layer = selected && state.layers.get(selected.claim_id);
      if (focusMap && layer) {
        state.map.fitBounds(layer.getBounds(), { padding: [64, 64], maxZoom: 18 });
      } else if (focusMap && !selected && state.allBounds) {
        state.map.fitBounds(state.allBounds, { padding: [32, 32] });
      }
      return selected;
    }

    function applySearch() {
      const search = byId('claimedLandSearch');
      state.model = registryViewModel(state.payload, state.selected?.claim_id, search.value);
      state.selected = state.model.selected;
      selectClaim(state.selected?.claim_id || null, { focusMap: true, scrollList: false });
    }

    function selectFromMap(claimId) {
      const nextClaimId = nextClaimSelection(state.selected?.claim_id || null, claimId);
      const search = byId('claimedLandSearch');
      if (nextClaimId && search.value) {
        search.value = '';
        state.model = registryViewModel(state.payload, nextClaimId, '');
        state.selected = state.model.selected;
      }
      selectClaim(nextClaimId, { focusMap: true, scrollList: Boolean(nextClaimId) });
    }

    function render(payload, selectedClaimId) {
      state.payload = payload;
      state.model = registryViewModel(payload, selectedClaimId, byId('claimedLandSearch').value);
      state.selected = state.model.selected;
      byId('claimedLandSummary').textContent = state.model.summaryText;
      byId('claimedLandEmpty').hidden = state.model.claims.length > 0;
      byId('claimedLandMap').hidden = state.model.claims.length === 0;
      byId('claimedLandList').hidden = state.model.claims.length === 0;
      byId('claimedLandSearch').disabled = state.model.claims.length === 0;
      if (!state.model.claims.length) {
        renderClaimList();
        return;
      }

      ensureMap();
      state.layers.forEach((layer) => layer.remove());
      state.layers.clear();
      const bounds = [];
      const rootStyles = browserWindow.getComputedStyle(doc.documentElement);
      const outline = rootStyles.getPropertyValue('--green-800').trim();
      const fill = rootStyles.getPropertyValue('--green-600').trim();
      state.model.claims.forEach((claim) => {
        const layer = leaflet.geoJSON(claim.parcel.geometry, {
          style: { color: outline, weight: 2, fillColor: fill, fillOpacity: .2, opacity: .72 },
        }).addTo(state.map);
        layer.on('click', () => selectFromMap(claim.claim_id));
        state.layers.set(claim.claim_id, layer);
        bounds.push(layer.getBounds());
      });
      state.allBounds = bounds.length
        ? bounds.reduce((all, item) => all.extend(item))
        : null;
      if (state.allBounds) state.map.fitBounds(state.allBounds, { padding: [32, 32] });
      selectClaim(state.selected?.claim_id || null, { focusMap: false });
    }

    async function load(selectedClaimId = null) {
      setFeedback(byId('claimedLandFeedback'), 'info', 'Loading registered parcels…');
      try {
        const response = await fetchImpl('/api/claims/registry');
        const payload = await response.json();
        if (!response.ok) throw new Error(payload.message || 'Claimed land could not be loaded.');
        render(payload, selectedClaimId);
        setFeedback(byId('claimedLandFeedback'), '', '');
        return state.model;
      } catch (error) {
        setFeedback(byId('claimedLandFeedback'), 'error', error.message);
        return null;
      }
    }

    byId('claimedLandSearch').addEventListener('input', applySearch);

    return { load, selectClaim };
  }

  return { claimRowPresentation, createController, nextClaimSelection, registryViewModel };
})();

if (typeof module !== 'undefined' && module.exports) module.exports = ClaimedLandUI;
