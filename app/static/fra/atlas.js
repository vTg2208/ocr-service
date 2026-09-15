const FRAAtlasUI = (() => {
  const ORDER = ['state', 'district', 'block', 'village', 'tribal_group', 'claimant_category', 'right_type', 'status', 'year', 'min_area_sqm', 'max_area_sqm', 'layers'];
  function query(filters = {}) {
    const params = new URLSearchParams();
    ORDER.forEach((key) => { const value = filters[key]; if (value !== undefined && value !== null && String(value).trim()) params.set(key, String(value).trim()); });
    return params.toString();
  }
  function contextFilters(form) {
    const layers = [...form.querySelectorAll('[name="layers"]:checked')].map((item) => item.value).join(',');
    return {
      state: 'TN',
      district: document.querySelector('#contextDistrict')?.value || '',
      block: document.querySelector('#contextBlock')?.value || '',
      village: document.querySelector('#contextVillage')?.value || '',
      tribal_group: form.elements.tribal_group.value,
      claimant_category: form.elements.claimant_category.value,
      right_type: form.elements.right_type.value,
      status: form.elements.status.value,
      year: form.elements.year.value,
      min_area_sqm: form.elements.min_area_sqm.value,
      max_area_sqm: form.elements.max_area_sqm.value,
      layers,
    };
  }
  function featurePresentation(properties = {}, assetVisualFor = () => null) {
    const visual = properties.kind === 'asset' ? assetVisualFor(properties.asset_class) : null;
    return {
      name: visual?.label || properties.name || properties.village || properties.claim_number || properties.title_number || properties.asset_class || 'Mapped feature',
      meta: [properties.district, properties.layer, properties.collection, properties.right_type, properties.status || properties.verification_state, properties.source_authority].filter(Boolean).join(' · '),
      spritePosition: visual?.spritePosition || null,
      color: visual?.color || null,
    };
  }
  function summaryRows(data = {}) {
    const rights = data.by_right_type || {};
    const area = (value) => `${Number(value || 0).toLocaleString('en-IN')} m²`;
    return [
      { label: 'Total claims', value: Number(data.claim_count || 0) },
      { label: 'IFR claims', value: Number(rights.IFR || 0) },
      { label: 'CR claims', value: Number(rights.CR || 0) },
      { label: 'CFR claims', value: Number(rights.CFR || 0) },
      { label: 'Granted', value: Number(data.granted_claim_count || 0) },
      { label: 'Rejected', value: Number(data.rejected_claim_count || 0) },
      { label: 'Pending', value: Number(data.pending_claim_count || 0) },
      { label: 'Claimed area', value: area(data.claimed_area_sqm) },
      { label: 'Granted area', value: area(data.granted_area_sqm) },
    ];
  }
  function drillDetail(row = {}) {
    return {
      district: String(row.district || '').trim(),
      block: String(row.block || '').trim(),
      village: String(row.village || '').trim(),
    };
  }
  function caseTarget(properties = {}) {
    const claimId = String(properties.claim_id || '').trim();
    if (!claimId || !['claim', 'title', 'imagery'].includes(properties.kind)) return null;
    return { claimId, tab: properties.kind === 'title' ? 'titles' : 'evidence' };
  }
  return { caseTarget, contextFilters, drillDetail, featurePresentation, query, summaryRows };
})();
if (typeof module !== 'undefined' && module.exports) module.exports = FRAAtlasUI;

if (typeof document !== 'undefined') (() => {
  const form = document.querySelector('#atlasFilters'); if (!form) return;
  const results = document.querySelector('#atlasResults'); const summaryNode = document.querySelector('#atlasSummary');
  const featureCount = document.querySelector('#atlasFeatureCount'); let map; let featureLayer; let loaded = false;
  const requests = FRAApi.requestGate();
  const colors = { administrative: '#52728a', village: '#486b4b', reference: '#517b67', imagery: '#315f91', claim: '#bf6d2c', title: '#146b67', asset: '#6c4d8e' };
  function presentation(properties) { return FRAAtlasUI.featurePresentation(properties, FRAAssetsUI.visualFor); }
  function assetMarkerHtml(properties) { const item = presentation(properties); return `<span class="asset-icon-frame asset-marker-glyph" aria-hidden="true"><span class="asset-sprite-icon" style="background-position:${item.spritePosition}" aria-hidden="true"></span></span>`; }
  function assetIcon(properties, className) { const item = presentation(properties); const frame = document.createElement('span'); const glyph = document.createElement('span'); frame.className = `asset-icon-frame ${className}`; frame.setAttribute('aria-label', item.name); glyph.className = 'asset-sprite-icon'; glyph.style.backgroundPosition = item.spritePosition; glyph.setAttribute('aria-hidden', 'true'); frame.appendChild(glyph); return frame; }
  function ensureMap() {
    if (map) return true;
    if (typeof L === 'undefined') {
      const mapNode = document.querySelector('#atlasMap'); mapNode.classList.add('map-unavailable'); mapNode.textContent = 'The interactive map could not load. The filtered record list and summary remain available.'; return false;
    }
    map = L.map('atlasMap', { zoomControl: true }).setView([11.1, 78.65], 7);
    L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', { maxZoom: 18, attribution: '&copy; OpenStreetMap contributors' }).addTo(map);
    featureLayer = L.geoJSON([], {
      style: (feature) => { const props = feature.properties || {}; const color = presentation(props).color || colors[props.kind] || '#315a3a'; const contextual = props.kind === 'village' || props.kind === 'administrative'; return { color, fillColor: color, weight: contextual ? 1.5 : 2, fillOpacity: contextual ? 0.06 : 0.22 }; },
      pointToLayer: (feature, latlng) => { const props = feature.properties || {}; if (props.kind === 'asset') return L.marker(latlng, { icon: L.divIcon({ className: 'asset-map-marker', html: assetMarkerHtml(props), iconSize: [34, 34], iconAnchor: [17, 17], popupAnchor: [0, -18] }) }); return L.circleMarker(latlng, { radius: 7, color: colors[props.kind] || '#315a3a', fillOpacity: 0.75 }); },
      onEachFeature: (feature, layer) => { const item = presentation(feature.properties || {}); layer.bindTooltip(item.name, { direction: 'top' }); },
    }).addTo(map);
    return true;
  }
  function renderSummary(data) {
    summaryNode.replaceChildren();
    const heading = document.createElement('h2'); heading.textContent = 'Filtered summary'; summaryNode.appendChild(heading);
    FRAAtlasUI.summaryRows(data).forEach(({ label, value }) => {
      const row = document.createElement('div'); const term = document.createElement('span'); const amount = document.createElement('strong'); term.textContent = label; amount.textContent = value; row.append(term, amount); summaryNode.appendChild(row);
    });
    const progress = data.progress || {};
    ['state', 'district', 'block', 'village'].forEach((level) => {
      const rows = progress[level] || []; if (!rows.length) return;
      const section = document.createElement('section'); section.className = 'atlas-progress';
      const title = document.createElement('h3'); title.textContent = `${level[0].toUpperCase()}${level.slice(1)} progress`; section.appendChild(title);
      rows.forEach((item) => {
        const button = document.createElement('button'); const name = item[level] || item.state;
        button.type = 'button'; button.textContent = `${name}: ${item.granted_claims}/${item.total_claims} granted · ${item.pending_claims} pending`;
        button.addEventListener('click', () => document.dispatchEvent(new CustomEvent('fra:atlas-drill', { detail: FRAAtlasUI.drillDetail(item) })));
        section.appendChild(button);
      });
      summaryNode.appendChild(section);
    });
  }
  function renderFeatures(collection) {
    results.replaceChildren(); const features = collection.features || []; featureCount.textContent = `${features.length} ${features.length === 1 ? 'feature' : 'features'}`;
    features.forEach((feature) => { const props = feature.properties || {}; const item = presentation(props); const row = document.createElement('li'); const kind = props.kind === 'asset' ? assetIcon(props, 'atlas-asset-glyph') : document.createElement('span'); const name = document.createElement('strong'); const meta = document.createElement('small'); if (props.kind !== 'asset') { kind.className = `kind-mark kind-${props.kind}`; kind.textContent = props.kind; } else { row.classList.add('atlas-asset-record'); } name.textContent = item.name; meta.textContent = item.meta; row.append(kind, name, meta); const target = FRAAtlasUI.caseTarget(props); if (target) { row.classList.add('atlas-case-link'); row.tabIndex = 0; row.setAttribute('role', 'link'); const open = () => { document.querySelector('[data-section="cases"]')?.click(); setTimeout(() => document.dispatchEvent(new CustomEvent('fra:open-case', { detail: target })), 0); }; row.addEventListener('click', open); row.addEventListener('keydown', (event) => { if (event.key === 'Enter' || event.key === ' ') { event.preventDefault(); open(); } }); } results.appendChild(row); });
    if (!features.length) { const empty = document.createElement('li'); empty.className = 'empty-row'; empty.textContent = 'No features match these Tamil Nadu filters.'; results.appendChild(empty); }
    ensureMap(); if (!featureLayer) return; featureLayer.clearLayers(); featureLayer.addData(collection);
    try { const bounds = featureLayer.getBounds(); if (bounds.isValid()) map.fitBounds(bounds, { padding: [22, 22], maxZoom: 13 }); else map.setView([11.1, 78.65], 7); } catch (_) { map.setView([11.1, 78.65], 7); }
    setTimeout(() => map.invalidateSize(), 0);
  }
  async function load() {
    const current = requests.begin();
    try {
      ensureMap(); const suffix = FRAAtlasUI.query(FRAAtlasUI.contextFilters(form));
      const [features, summary] = await Promise.all([FRAApi.request(`/api/fra/atlas/features?${suffix}`), FRAApi.request(`/api/fra/atlas/summary?${suffix}`)]);
      if (!current()) return;
      renderFeatures(features); renderSummary(summary); loaded = true;
    } catch (error) { if (current()) summaryNode.textContent = error.message; }
  }
  const LAYER_PRESETS = {
    rights: ['country', 'state', 'district', 'block', 'village', 'claim', 'title'],
    review: ['state', 'district', 'block', 'village', 'claim', 'title', 'forest_area', 'protected_area', 'cadastral_parcel'],
    planning: ['state', 'district', 'block', 'village', 'claim', 'title', 'asset', 'satellite_imagery', 'water_body', 'groundwater', 'groundwater_stress', 'water_stress', 'infrastructure'],
  };
  document.querySelectorAll('[data-layer-preset]').forEach((button) => button.addEventListener('click', () => { const selected = new Set(LAYER_PRESETS[button.dataset.layerPreset] || []); form.querySelectorAll('input[name="layers"]').forEach((input) => { input.checked = selected.has(input.value); }); form.requestSubmit(); }));
  form.addEventListener('submit', (event) => { event.preventDefault(); load(); });
  document.addEventListener('fra:section', (event) => { if (event.detail.section === 'atlas') { if (!loaded) load(); else { ensureMap(); setTimeout(() => map?.invalidateSize(), 0); } } });
  document.addEventListener('fra:context', () => { loaded = false; requests.invalidate(); if (!document.querySelector('#atlasPanel').hidden) load(); });
})();
