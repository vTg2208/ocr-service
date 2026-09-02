const FRAAssetsUI = (() => {
  const definitions = [
    ['forest_cover', 'Forest cover', 'Land cover and natural resources', '#2f6b3c', '-25px -41px'],
    ['agricultural_cover', 'Agricultural land', 'Land cover and natural resources', '#668a35', '-100px -42px'],
    ['water_body', 'Water body', 'Land cover and natural resources', '#276f9f', '-173px -41px'],
    ['pond', 'Pond', 'Land cover and natural resources', '#3b7fb0', '-245px -41px'],
    ['river_stream', 'River or stream', 'Land cover and natural resources', '#367db7', '-324px -41px'],
    ['homestead', 'Homestead', 'Land cover and natural resources', '#8a5a35', '-392px -43px'],
    ['barren_land', 'Barren land', 'Land cover and natural resources', '#93623e', '-459px -41px'],
    ['scrubland', 'Scrubland', 'Land cover and natural resources', '#70824a', '-526px -44px'],
    ['plantation_orchard', 'Plantation or orchard', 'Land cover and natural resources', '#4f7c3e', '-589px -41px'],
    ['grazing_land', 'Grazing land', 'Land cover and natural resources', '#719542', '-652px -44px'],
    ['minor_forest_produce', 'Minor forest produce', 'Land cover and natural resources', '#557c37', '-713px -43px'],
    ['open_well', 'Open well', 'Water and irrigation infrastructure', '#3f6f83', '-26px -174px'],
    ['borewell', 'Borewell', 'Water and irrigation infrastructure', '#397b96', '-114px -174px'],
    ['pipeline', 'Pipeline', 'Water and irrigation infrastructure', '#5d7082', '-201px -174px'],
    ['water_tank', 'Water tank', 'Water and irrigation infrastructure', '#326d9a', '-288px -174px'],
    ['tap_water', 'Tap water', 'Water and irrigation infrastructure', '#2e7ba8', '-374px -174px'],
    ['check_dam', 'Check dam', 'Water and irrigation infrastructure', '#4f748d', '-459px -174px'],
    ['irrigation_canal', 'Irrigation canal', 'Water and irrigation infrastructure', '#4a82a5', '-537px -174px'],
    ['rainwater_harvesting', 'Rainwater harvesting', 'Water and irrigation infrastructure', '#477c9d', '-663px -174px'],
    ['road', 'Road', 'Transport and utility infrastructure', '#4e565b', '-21px -299px'],
    ['bridge', 'Bridge', 'Transport and utility infrastructure', '#64645f', '-98px -299px'],
    ['electricity_grid', 'Electricity grid', 'Transport and utility infrastructure', '#5e5964', '-175px -299px'],
    ['solar_power', 'Solar power', 'Transport and utility infrastructure', '#a36b1f', '-252px -299px'],
    ['school', 'School', 'Social and public infrastructure', '#725d3f', '-332px -299px'],
    ['anganwadi', 'Anganwadi', 'Social and public infrastructure', '#8a5f51', '-405px -299px'],
    ['health_centre', 'Health centre', 'Social and public infrastructure', '#a14242', '-482px -299px'],
    ['community_centre', 'Community centre', 'Social and public infrastructure', '#765a4b', '-554px -299px'],
    ['market', 'Market', 'Social and public infrastructure', '#8b6239', '-625px -299px'],
    ['sanitation_toilet', 'Sanitation or toilet', 'Social and public infrastructure', '#58706d', '-700px -299px'],
    ['livestock', 'Livestock', 'Livelihood assets', '#755f48', '-24px -420px'],
    ['fisheries', 'Fisheries', 'Livelihood assets', '#397da5', '-108px -420px'],
    ['forest_nursery', 'Forest nursery', 'Livelihood assets', '#4f7d3c', '-192px -420px'],
    ['storage_warehouse', 'Storage warehouse', 'Livelihood assets', '#6d6257', '-280px -420px'],
  ];
  const visuals = Object.fromEntries(definitions.map(([key, label, _group, color, spritePosition]) => [
    key, { key, label, color, spritePosition },
  ]));
  const aliases = {
    agricultural_land: 'agricultural_cover', farm: 'agricultural_cover', forest: 'forest_cover',
    river: 'river_stream', stream: 'river_stream', well: 'open_well', health_center: 'health_centre',
    community_center: 'community_centre', warehouse: 'storage_warehouse',
  };
  const fallback = { key: 'default_asset', label: 'Asset', color: '#4f6258', spritePosition: '-394px -422px' };

  function normalizedKey(value) { return String(value || '').trim().toLowerCase().replace(/[\s-]+/g, '_'); }
  function readableLabel(value) { const text = normalizedKey(value).replaceAll('_', ' '); return text ? text.charAt(0).toUpperCase() + text.slice(1) : fallback.label; }
  function visualFor(assetClass) { const requested = normalizedKey(assetClass); const key = aliases[requested] || requested; return visuals[key] || { ...fallback, label: readableLabel(requested) }; }
  function assetOptions() { return definitions.map(([value, label, group]) => ({ value, label, group })); }
  function legendClasses(items) { return [...new Set((items || []).map((item) => visualFor(item.asset_class).key))].sort((left, right) => visualFor(left).label.localeCompare(visualFor(right).label)); }
  function legalRole() { return 'Each observation is supporting evidence and requires human verification; it does not determine legal validity.'; }
  function manifest(assetClass, village) {
    const point = village?.boundary?.coordinates?.[0]?.[0]?.[0] || [78.65, 11.1];
    return { synthetic: true, acquired_at: '2026-08-26', source: 'Synthetic sample observation; no pixel inference', features: [{ asset_class: assetClass, geometry: { type: 'Point', coordinates: point }, value: { present: true }, confidence: 0.72 }] };
  }
  function preferredVillageId(villages) { if (!Array.isArray(villages) || !villages.length) return ''; return (villages.find((item) => item.village_name === 'Kottur') || villages[0]).id; }
  return { assetOptions, legalRole, legendClasses, manifest, preferredVillageId, visualFor };
})();
if (typeof module !== 'undefined' && module.exports) module.exports = FRAAssetsUI;

if (typeof document !== 'undefined') (() => {
  const form = document.querySelector('#assetInferenceForm'); if (!form) return;
  const modelSelect = document.querySelector('#assetModel'); const villageSelect = document.querySelector('#assetVillage'); const assetClassSelect = document.querySelector('#assetClass'); const list = document.querySelector('#assetList'); const legend = document.querySelector('#assetLegend'); const status = document.querySelector('#assetJobStatus');
  let map; let layer; let villages = []; let initialized = false;

  function spriteElement(assetClass, className = '') { const visual = FRAAssetsUI.visualFor(assetClass); const frame = document.createElement('span'); const icon = document.createElement('span'); frame.className = `asset-icon-frame ${className}`.trim(); frame.setAttribute('aria-label', visual.label); icon.className = 'asset-sprite-icon'; icon.style.backgroundPosition = visual.spritePosition; icon.setAttribute('aria-hidden', 'true'); frame.appendChild(icon); return frame; }
  function markerHtml(assetClass) { const visual = FRAAssetsUI.visualFor(assetClass); return `<span class="asset-icon-frame asset-marker-glyph" aria-hidden="true"><span class="asset-sprite-icon" style="background-position:${visual.spritePosition}" aria-hidden="true"></span></span>`; }
  function ensureMap() {
    if (map || typeof L === 'undefined') return;
    map = L.map('assetMap').setView([11.1, 78.65], 7);
    L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', { maxZoom: 18, attribution: '&copy; OpenStreetMap contributors' }).addTo(map);
    layer = L.geoJSON([], {
      style: (feature) => { const visual = FRAAssetsUI.visualFor(feature?.properties?.asset_class); return { color: visual.color, weight: 3, fillColor: visual.color, fillOpacity: .2 }; },
      pointToLayer: (feature, latlng) => L.marker(latlng, { icon: L.divIcon({ className: 'asset-map-marker', html: markerHtml(feature?.properties?.asset_class), iconSize: [34, 34], iconAnchor: [17, 17], popupAnchor: [0, -18] }) }),
      onEachFeature: (feature, featureLayer) => { const visual = FRAAssetsUI.visualFor(feature?.properties?.asset_class); featureLayer.bindTooltip(visual.label, { direction: 'top' }); },
    }).addTo(map);
  }
  function contextQuery() { const params = new URLSearchParams(); [['district', '#contextDistrict'], ['block', '#contextBlock'], ['village', '#contextVillage']].forEach(([key, selector]) => { const value = document.querySelector(selector)?.value; if (value) params.set(key, value); }); return params.toString(); }
  function populateAssetClasses() {
    assetClassSelect.replaceChildren(); const groups = new Map();
    FRAAssetsUI.assetOptions().forEach((item) => { if (!groups.has(item.group)) { const group = document.createElement('optgroup'); group.label = item.group; groups.set(item.group, group); assetClassSelect.appendChild(group); } groups.get(item.group).appendChild(new Option(item.label, item.value)); });
    assetClassSelect.value = 'water_body';
  }
  async function loadReference() {
    const [models, villageResult] = await Promise.all([FRAApi.request('/api/fra/models?task=asset_detection&status=active'), FRAApi.request('/api/fra/villages')]); villages = villageResult.items;
    modelSelect.replaceChildren(new Option(models.items.length ? 'Select an active model' : 'Awaiting trained model', '')); models.items.forEach((item) => modelSelect.add(new Option(`${item.name} ${item.version}`, item.id))); if (models.items.length) modelSelect.value = models.items[0].id;
    villageSelect.replaceChildren(); villages.forEach((item) => villageSelect.add(new Option(`${item.village_name} — ${item.district_name}`, item.id))); if (!villages.length) villageSelect.add(new Option('Village list unavailable', '')); villageSelect.value = FRAAssetsUI.preferredVillageId(villages);
  }
  function renderLegend(items) {
    legend.replaceChildren();
    FRAAssetsUI.legendClasses(items).forEach((assetClass) => { const visual = FRAAssetsUI.visualFor(assetClass); const item = document.createElement('li'); const label = document.createElement('span'); label.textContent = visual.label; item.append(spriteElement(assetClass, 'asset-legend-glyph'), label); legend.appendChild(item); });
    legend.closest('.asset-map-legend').hidden = !legend.children.length;
  }
  function renderAssets(items) {
    list.replaceChildren(); ensureMap(); layer?.clearLayers(); const collection = { type: 'FeatureCollection', features: [] };
    items.forEach((asset) => { const visual = FRAAssetsUI.visualFor(asset.asset_class); const row = document.createElement('li'); row.className = 'asset-record'; const icon = spriteElement(asset.asset_class, 'asset-record-glyph'); const content = document.createElement('div'); const name = document.createElement('strong'); const meta = document.createElement('small'); const state = document.createElement('span'); name.textContent = visual.label; meta.textContent = `${asset.source_type} · ${asset.confidence == null ? 'human value' : `${Math.round(asset.confidence * 100)}% confidence`}`; state.className = 'record-state'; state.textContent = asset.verification_state; content.append(name, meta); row.append(icon, content, state); list.appendChild(row); if (asset.geometry) collection.features.push({ type: 'Feature', geometry: asset.geometry, properties: { asset_class: asset.asset_class } }); });
    if (!items.length) { const empty = document.createElement('li'); empty.className = 'empty-row'; empty.textContent = 'No supporting asset observations in this context.'; list.appendChild(empty); }
    renderLegend(items); layer?.addData(collection); try { const bounds = layer?.getBounds(); if (bounds?.isValid()) map.fitBounds(bounds, { padding: [20, 20], maxZoom: 14 }); } catch (_) { /* keep Tamil Nadu extent */ } setTimeout(() => map?.invalidateSize(), 0);
  }
  async function loadAssets() { try { const suffix = contextQuery(); const data = await FRAApi.request(`/api/fra/assets${suffix ? `?${suffix}` : ''}`); renderAssets(data.items); } catch (error) { status.textContent = error.message; } }
  async function submit(event) {
    event.preventDefault(); status.textContent = '';
    const village = villages.find((item) => item.id === villageSelect.value); if (!village || !modelSelect.value) { status.textContent = 'Choose an active model and Tamil Nadu village.'; return; }
    try { const detail = await FRAApi.request(`/api/fra/villages/${village.id}`); const scene = document.querySelector('#assetScene').value.trim(); const payload = { village_id: village.id, claim_id: null, model_version_id: modelSelect.value, scene_id: scene, idempotency_key: `ui-${village.id}-${scene}`, manifest: FRAAssetsUI.manifest(assetClassSelect.value, detail) }; const job = await FRAApi.request('/api/fra/assets/inference-jobs', FRAApi.json('POST', payload)); status.textContent = `Job ${job.id} queued. Run the FRA worker to process it.`; } catch (error) { status.textContent = error.message; }
  }
  populateAssetClasses(); form.addEventListener('submit', submit); document.querySelector('#refreshAssets').addEventListener('click', loadAssets);
  document.addEventListener('fra:section', async (event) => { if (event.detail.section !== 'assets') return; ensureMap(); if (!initialized) { initialized = true; try { await loadReference(); } catch (error) { status.textContent = error.message; } } await loadAssets(); });
  document.addEventListener('fra:context', () => { if (!document.querySelector('#assetsPanel').hidden) loadAssets(); });
})();
