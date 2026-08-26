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
  return { contextFilters, query };
})();
if (typeof module !== 'undefined' && module.exports) module.exports = FRAAtlasUI;

if (typeof document !== 'undefined') (() => {
  const form = document.querySelector('#atlasFilters'); if (!form) return;
  const results = document.querySelector('#atlasResults'); const summaryNode = document.querySelector('#atlasSummary');
  const featureCount = document.querySelector('#atlasFeatureCount'); let map; let featureLayer; let loaded = false;
  const colors = { village: '#486b4b', claim: '#bf6d2c', title: '#146b67', asset: '#6c4d8e' };
  function ensureMap() {
    if (map || typeof L === 'undefined') return;
    map = L.map('atlasMap', { zoomControl: true }).setView([11.1, 78.65], 7);
    L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', { maxZoom: 18, attribution: '&copy; OpenStreetMap contributors' }).addTo(map);
    featureLayer = L.geoJSON([], {
      style: (feature) => ({ color: colors[feature.properties.kind] || '#315a3a', weight: 2, fillOpacity: feature.properties.kind === 'village' ? 0.08 : 0.22 }),
      pointToLayer: (feature, latlng) => L.circleMarker(latlng, { radius: 7, color: colors[feature.properties.kind] || '#315a3a', fillOpacity: 0.75 }),
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
    features.forEach((feature) => { const props = feature.properties || {}; const row = document.createElement('li'); const kind = document.createElement('span'); const name = document.createElement('strong'); const meta = document.createElement('small'); kind.className = `kind-mark kind-${props.kind}`; kind.textContent = props.kind; name.textContent = props.village || props.claim_number || props.title_number || props.asset_class || 'Mapped feature'; meta.textContent = [props.district, props.right_type, props.status || props.verification_state].filter(Boolean).join(' · '); row.append(kind, name, meta); results.appendChild(row); });
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
