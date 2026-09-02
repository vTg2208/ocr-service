const FRAAtlasUI = (() => {
  const ORDER = ['state', 'district', 'block', 'village', 'tribal_group', 'right_type', 'status', 'year', 'layers'];
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
      right_type: form.elements.right_type.value,
      status: form.elements.status.value,
      year: form.elements.year.value,
      layers,
    };
  }
  function featurePresentation(properties = {}, assetVisualFor = () => null) {
    const visual = properties.kind === 'asset' ? assetVisualFor(properties.asset_class) : null;
    return {
      name: visual?.label || properties.village || properties.claim_number || properties.title_number || properties.asset_class || 'Mapped feature',
      meta: [properties.district, properties.right_type, properties.status || properties.verification_state].filter(Boolean).join(' · '),
      spritePosition: visual?.spritePosition || null,
      color: visual?.color || null,
    };
  }
  return { contextFilters, featurePresentation, query };
})();
if (typeof module !== 'undefined' && module.exports) module.exports = FRAAtlasUI;

if (typeof document !== 'undefined') (() => {
  const form = document.querySelector('#atlasFilters'); if (!form) return;
  const results = document.querySelector('#atlasResults'); const summaryNode = document.querySelector('#atlasSummary');
  const featureCount = document.querySelector('#atlasFeatureCount'); let map; let featureLayer; let loaded = false;
  const colors = { village: '#486b4b', claim: '#bf6d2c', title: '#146b67', asset: '#6c4d8e' };
  function presentation(properties) { return FRAAtlasUI.featurePresentation(properties, FRAAssetsUI.visualFor); }
  function assetMarkerHtml(properties) { const item = presentation(properties); return `<span class="asset-icon-frame asset-marker-glyph" aria-hidden="true"><span class="asset-sprite-icon" style="background-position:${item.spritePosition}" aria-hidden="true"></span></span>`; }
  function assetIcon(properties, className) { const item = presentation(properties); const frame = document.createElement('span'); const glyph = document.createElement('span'); frame.className = `asset-icon-frame ${className}`; frame.setAttribute('aria-label', item.name); glyph.className = 'asset-sprite-icon'; glyph.style.backgroundPosition = item.spritePosition; glyph.setAttribute('aria-hidden', 'true'); frame.appendChild(glyph); return frame; }
  function ensureMap() {
    if (map || typeof L === 'undefined') return;
    map = L.map('atlasMap', { zoomControl: true }).setView([11.1, 78.65], 7);
    L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', { maxZoom: 18, attribution: '&copy; OpenStreetMap contributors' }).addTo(map);
    featureLayer = L.geoJSON([], {
      style: (feature) => { const props = feature.properties || {}; const color = presentation(props).color || colors[props.kind] || '#315a3a'; return { color, fillColor: color, weight: 2, fillOpacity: props.kind === 'village' ? 0.08 : 0.22 }; },
      pointToLayer: (feature, latlng) => { const props = feature.properties || {}; if (props.kind === 'asset') return L.marker(latlng, { icon: L.divIcon({ className: 'asset-map-marker', html: assetMarkerHtml(props), iconSize: [34, 34], iconAnchor: [17, 17], popupAnchor: [0, -18] }) }); return L.circleMarker(latlng, { radius: 7, color: colors[props.kind] || '#315a3a', fillOpacity: 0.75 }); },
      onEachFeature: (feature, layer) => { const item = presentation(feature.properties || {}); layer.bindTooltip(item.name, { direction: 'top' }); },
    }).addTo(map);
  }
  function renderSummary(data) {
    summaryNode.replaceChildren();
    const heading = document.createElement('h2'); heading.textContent = 'Filtered summary'; summaryNode.appendChild(heading);
    [['Villages', data.village_count], ['Claims', data.claim_count], ['Titles', data.title_count], ['Assets', data.asset_count], ['Claimed area', `${Number(data.claimed_area_sqm || 0).toLocaleString()} m²`]].forEach(([label, value]) => {
      const row = document.createElement('div'); const term = document.createElement('span'); const amount = document.createElement('strong'); term.textContent = label; amount.textContent = value; row.append(term, amount); summaryNode.appendChild(row);
    });
  }
  function renderFeatures(collection) {
    results.replaceChildren(); const features = collection.features || []; featureCount.textContent = `${features.length} ${features.length === 1 ? 'feature' : 'features'}`;
    features.forEach((feature) => { const props = feature.properties || {}; const item = presentation(props); const row = document.createElement('li'); const kind = props.kind === 'asset' ? assetIcon(props, 'atlas-asset-glyph') : document.createElement('span'); const name = document.createElement('strong'); const meta = document.createElement('small'); if (props.kind !== 'asset') { kind.className = `kind-mark kind-${props.kind}`; kind.textContent = props.kind; } else { row.classList.add('atlas-asset-record'); } name.textContent = item.name; meta.textContent = item.meta; row.append(kind, name, meta); results.appendChild(row); });
    if (!features.length) { const empty = document.createElement('li'); empty.className = 'empty-row'; empty.textContent = 'No features match these Tamil Nadu filters.'; results.appendChild(empty); }
    ensureMap(); if (!featureLayer) return; featureLayer.clearLayers(); featureLayer.addData(collection);
    try { const bounds = featureLayer.getBounds(); if (bounds.isValid()) map.fitBounds(bounds, { padding: [22, 22], maxZoom: 13 }); else map.setView([11.1, 78.65], 7); } catch (_) { map.setView([11.1, 78.65], 7); }
    setTimeout(() => map.invalidateSize(), 0);
  }
  async function load() {
    try {
      ensureMap(); const suffix = FRAAtlasUI.query(FRAAtlasUI.contextFilters(form));
      const [features, summary] = await Promise.all([FRAApi.request(`/api/fra/atlas/features?${suffix}`), FRAApi.request(`/api/fra/atlas/summary?${suffix}`)]);
      renderFeatures(features); renderSummary(summary); loaded = true;
    } catch (error) { summaryNode.textContent = error.message; }
  }
  form.addEventListener('submit', (event) => { event.preventDefault(); load(); });
  document.addEventListener('fra:section', (event) => { if (event.detail.section === 'atlas') { if (!loaded) load(); else { ensureMap(); setTimeout(() => map?.invalidateSize(), 0); } } });
  document.addEventListener('fra:context', () => { if (!document.querySelector('#atlasPanel').hidden) load(); });
})();
