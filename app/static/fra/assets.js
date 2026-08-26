const FRAAssetsUI = (() => {
  function legalRole() { return 'Each observation is supporting evidence and requires human verification; it does not determine legal validity.'; }
  function manifest(assetClass, village) {
    const point = village?.boundary?.coordinates?.[0]?.[0]?.[0] || [78.65, 11.1];
    return { synthetic: true, acquired_at: '2026-08-26', source: 'Synthetic UI demonstration; no pixel inference', features: [{ asset_class: assetClass, geometry: { type: 'Point', coordinates: point }, value: { present: true }, confidence: 0.72 }] };
  }
  return { legalRole, manifest };
})();
if (typeof module !== 'undefined' && module.exports) module.exports = FRAAssetsUI;

if (typeof document !== 'undefined') (() => {
  const form = document.querySelector('#assetInferenceForm'); if (!form) return;
  const modelSelect = document.querySelector('#assetModel'); const villageSelect = document.querySelector('#assetVillage'); const list = document.querySelector('#assetList'); const status = document.querySelector('#assetJobStatus');
  let map; let layer; let villages = []; let initialized = false;
  function ensureMap() { if (map || typeof L === 'undefined') return; map = L.map('assetMap').setView([11.1, 78.65], 7); L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', { maxZoom: 18, attribution: '&copy; OpenStreetMap contributors' }).addTo(map); layer = L.geoJSON([], { style: { color: '#6c4d8e', weight: 2, fillOpacity: .22 }, pointToLayer: (_f, latlng) => L.circleMarker(latlng, { radius: 7, color: '#6c4d8e', fillOpacity: .8 }) }).addTo(map); }
  function contextQuery() { const params = new URLSearchParams(); [['district', '#contextDistrict'], ['block', '#contextBlock'], ['village', '#contextVillage']].forEach(([key, selector]) => { const value = document.querySelector(selector)?.value; if (value) params.set(key, value); }); return params.toString(); }
  async function loadReference() {
    const [models, villageResult] = await Promise.all([FRAApi.request('/api/fra/models?task=asset_detection&status=active'), FRAApi.request('/api/fra/villages')]); villages = villageResult.items;
    modelSelect.replaceChildren(new Option(models.items.length ? 'Choose an active model' : 'No active model attached', '')); models.items.forEach((item) => modelSelect.add(new Option(`${item.name} ${item.version}`, item.id)));
    villageSelect.replaceChildren(new Option('Choose a village', '')); villages.forEach((item) => villageSelect.add(new Option(`${item.village_name} — ${item.district_name}`, item.id)));
  }
  function renderAssets(items) {
    list.replaceChildren(); ensureMap(); layer?.clearLayers(); const collection = { type: 'FeatureCollection', features: [] };
    items.forEach((asset) => { const row = document.createElement('li'); const name = document.createElement('strong'); const meta = document.createElement('small'); const state = document.createElement('span'); name.textContent = asset.asset_class.replaceAll('_', ' '); meta.textContent = `${asset.source_type} · ${asset.confidence == null ? 'human value' : `${Math.round(asset.confidence * 100)}% confidence`}`; state.className = 'record-state'; state.textContent = asset.verification_state; row.append(name, state, meta); list.appendChild(row); if (asset.geometry) collection.features.push({ type: 'Feature', geometry: asset.geometry, properties: { asset_class: asset.asset_class } }); });
    if (!items.length) { const empty = document.createElement('li'); empty.className = 'empty-row'; empty.textContent = 'No supporting asset observations in this context.'; list.appendChild(empty); }
    layer?.addData(collection); try { const bounds = layer?.getBounds(); if (bounds?.isValid()) map.fitBounds(bounds, { padding: [20, 20], maxZoom: 14 }); } catch (_) { /* keep Tamil Nadu extent */ } setTimeout(() => map?.invalidateSize(), 0);
  }
  async function loadAssets() { try { const suffix = contextQuery(); const data = await FRAApi.request(`/api/fra/assets${suffix ? `?${suffix}` : ''}`); renderAssets(data.items); } catch (error) { status.textContent = error.message; } }
  async function submit(event) {
    event.preventDefault(); status.textContent = '';
    const village = villages.find((item) => item.id === villageSelect.value); if (!village || !modelSelect.value) { status.textContent = 'Choose an active model and Tamil Nadu village.'; return; }
    try { const detail = await FRAApi.request(`/api/fra/villages/${village.id}`); const scene = document.querySelector('#assetScene').value.trim(); const payload = { village_id: village.id, claim_id: null, model_version_id: modelSelect.value, scene_id: scene, idempotency_key: `ui-${village.id}-${scene}`, manifest: FRAAssetsUI.manifest(document.querySelector('#assetClass').value, detail) }; const job = await FRAApi.request('/api/fra/assets/inference-jobs', FRAApi.json('POST', payload)); status.textContent = `Job ${job.id} queued. Run the FRA worker to process it.`; } catch (error) { status.textContent = error.message; }
  }
  form.addEventListener('submit', submit); document.querySelector('#refreshAssets').addEventListener('click', loadAssets);
  document.addEventListener('fra:section', async (event) => { if (event.detail.section !== 'assets') return; ensureMap(); if (!initialized) { initialized = true; try { await loadReference(); } catch (error) { status.textContent = error.message; } } await loadAssets(); });
  document.addEventListener('fra:context', () => { if (!document.querySelector('#assetsPanel').hidden) loadAssets(); });
})();
