const FRAAssetsUI = (() => {
  const definitions = [
    ['forest_cover', 'Forest cover', 'Land cover and natural resources', '#2f6b3c', '-25px -41px'],
    ['agricultural_land', 'Agricultural land', 'Land cover and natural resources', '#668a35', '-100px -42px'],
    ['water_body', 'Water body', 'Land cover and natural resources', '#276f9f', '-173px -41px'],
    ['homestead', 'Homestead', 'Land cover and natural resources', '#8a5a35', '-392px -43px'],
    ['road', 'Road', 'Transport and utility infrastructure', '#4e565b', '-21px -299px'],
    ['infrastructure', 'Infrastructure', 'Infrastructure', '#5d7082', '-201px -174px'],
    ['other_asset', 'Other asset', 'Other detected assets', '#755f48', '-24px -420px'],
  ];
  const visuals = Object.fromEntries(definitions.map(([key, label, _group, color, spritePosition]) => [
    key, { key, label, color, spritePosition },
  ]));
  const aliases = {
    agricultural_cover: 'agricultural_land', farm: 'agricultural_land', cropland: 'agricultural_land', agriculture: 'agricultural_land', forest: 'forest_cover', tree_cover: 'forest_cover', water_source: 'water_body', pond: 'water_body', lake: 'water_body', river_stream: 'water_body', river: 'water_body', stream: 'water_body', built_up: 'homestead', house: 'homestead',
    well: 'infrastructure', open_well: 'infrastructure', borewell: 'infrastructure', pipeline: 'infrastructure', water_tank: 'infrastructure', tap_water: 'infrastructure', check_dam: 'infrastructure', irrigation: 'infrastructure', irrigation_canal: 'infrastructure', rainwater_harvesting: 'infrastructure', bridge: 'infrastructure', electricity: 'infrastructure', electricity_grid: 'infrastructure', solar_power: 'infrastructure', school: 'infrastructure', anganwadi: 'infrastructure', health_center: 'infrastructure', health_centre: 'infrastructure', community_building: 'infrastructure', community_center: 'infrastructure', community_centre: 'infrastructure', market: 'infrastructure', sanitation_toilet: 'infrastructure', warehouse: 'infrastructure', storage_warehouse: 'infrastructure',
    barren_land: 'other_asset', scrubland: 'other_asset', plantation_orchard: 'other_asset', grazing_land: 'other_asset', minor_forest_produce: 'other_asset', livestock: 'other_asset', fisheries: 'other_asset', forest_nursery: 'other_asset',
  };
  const fallback = { key: 'default_asset', label: 'Asset', color: '#4f6258', spritePosition: '-394px -422px' };

  function normalizedKey(value) { return String(value || '').trim().toLowerCase().replace(/[\s-]+/g, '_'); }
  function readableLabel(value) { const text = normalizedKey(value).replaceAll('_', ' '); return text ? text.charAt(0).toUpperCase() + text.slice(1) : fallback.label; }
  function visualFor(assetClass) { const requested = normalizedKey(assetClass); const key = aliases[requested] || requested; return visuals[key] || { ...fallback, label: readableLabel(requested) }; }
  function assetOptions() { return definitions.map(([value, label, group]) => ({ value, label, group })); }
  function legendClasses(items) { return [...new Set((items || []).map((item) => visualFor(item.asset_class).key))].sort((left, right) => visualFor(left).label.localeCompare(visualFor(right).label)); }
  function legalRole() { return 'Each observation is supporting evidence and requires human verification; it does not determine legal validity.'; }
  function imageryPayload({ startDate, endDate, collection, bandKeys, maxCloud }) {
    const start = String(startDate || '').trim(); const end = String(endDate || '').trim();
    const bands = String(bandKeys || '').split(',').map((item) => item.trim()).filter(Boolean);
    const cloud = Number(maxCloud);
    if (!start || !end || start > end) throw new Error('Satellite imagery date range is invalid.');
    if (!String(collection || '').trim() || !bands.length || new Set(bands).size !== bands.length) throw new Error('Collection and distinct raster bands are required.');
    if (!Number.isFinite(cloud) || cloud < 0 || cloud > 100) throw new Error('Maximum cloud cover must be between 0 and 100.');
    return { start_date: start, end_date: end, collection: String(collection).trim(), band_keys: bands, max_cloud: cloud };
  }
  function preferredClaimId(claims) { if (!Array.isArray(claims) || !claims.length) return ''; return (claims.find((item) => item.geometry_version_count > 0) || claims[0]).id; }
  function profileSummary(profile) { const metrics = profile?.metrics || {}; const hectares = (value) => `${(Number(value || 0) / 10000).toFixed(2)} ha`; return [`Agriculture ${hectares(metrics.agricultural_area_sqm)}`, `Forest ${hectares(metrics.forest_area_sqm)}`, `Water bodies ${Number(metrics.water_body_count || 0)}`, `Homesteads ${Number(metrics.homestead_count || 0)}`, `Within village ${Number(metrics.assets_within_village_count || 0)}`, `Intersect FRA land ${Number(metrics.assets_intersecting_fra_land_count || 0)}`, `Near FRA land ${Number(metrics.assets_near_fra_land_count || 0)}`, `Mapped coverage ${Number(metrics.mapped_asset_coverage_percent || 0).toFixed(1)}%`, `Reviewed ${Number(profile?.verified_asset_count || 0)}`, `Pending ${Number(profile?.pending_asset_count || 0)}`]; }
  function evidenceRows(asset = {}) { const chain = asset.evidence_chain || {}; const model = chain.model || {}; const imagery = chain.imagery || {}; const geometry = chain.geometry || asset.geometry; return [['Asset', [visualFor(chain.asset?.class || asset.asset_class).label, chain.asset?.subtype].filter(Boolean).join(' · ')], ['Imagery', [imagery.reference, imagery.source_type].filter(Boolean).join(' · ') || 'Not recorded'], ['Date', chain.date || 'Not recorded'], ['Model', [model.name, model.version].filter(Boolean).join(' · ') || 'Human or field observation'], ['Confidence', chain.confidence == null ? 'Not recorded' : `${Math.round(Number(chain.confidence) * 100)}%`], ['Geometry', geometry?.type || 'Not recorded']]; }
  function reviewPayload(asset, outcome) { if (!['verified', 'rejected'].includes(outcome)) throw new Error('Choose approve or reject for this asset review.'); return { outcome, expected_revision: Number(asset?.revision || 0), reasons: [`${outcome === 'verified' ? 'Approved' : 'Rejected'} in the Asset Intelligence reviewer workspace.`] }; }
  return { assetOptions, evidenceRows, imageryPayload, legalRole, legendClasses, preferredClaimId, profileSummary, reviewPayload, visualFor };
})();
if (typeof module !== 'undefined' && module.exports) module.exports = FRAAssetsUI;

if (typeof document !== 'undefined') (() => {
  const form = document.querySelector('#imageryIngestionForm'); if (!form) return;
  const claimSelect = document.querySelector('#imageryClaim'); const imageryList = document.querySelector('#imageryIngestionList'); const list = document.querySelector('#assetList'); const profileList = document.querySelector('#villageAssetProfiles'); const legend = document.querySelector('#assetLegend'); const status = document.querySelector('#imageryJobStatus');
  let map; let layer; let claims = []; let initialized = false; let ingestionKey = crypto.randomUUID(); const requests = FRAApi.requestGate(); const references = FRAApi.requestGate(); const submissions = FRAApi.requestGate(); const imageryRequests = FRAApi.requestGate(); const profileRequests = FRAApi.requestGate(); const reviewRequests = FRAApi.requestGate();

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
  async function loadReference() {
    const current = references.begin(); const suffix = contextQuery(); const selectedClaim = claimSelect.value;
    try { const result = await FRAApi.request(`/api/fra/cases${suffix ? `?${suffix}` : ''}`); if (!current()) return; claims = result.items.filter((item) => item.geometry_version_count > 0);
    claimSelect.replaceChildren(); claims.forEach((item) => claimSelect.add(new Option(`${item.claim_number} — ${item.right_type}`, item.id))); if (!claims.length) claimSelect.add(new Option('No spatial FRA claims in this context', '')); claimSelect.value = claims.some((item) => item.id === selectedClaim) ? selectedClaim : FRAAssetsUI.preferredClaimId(claims); initialized = true;
    } catch (error) { if (current()) status.textContent = error.message; }
  }
  function renderImagery(data) {
    imageryList.replaceChildren();
    (data.artifacts || []).forEach((artifact) => { const row = document.createElement('li'); const name = document.createElement('strong'); const meta = document.createElement('small'); name.textContent = `${artifact.collection} · ${artifact.scene_id}`; meta.textContent = `${artifact.state} · ${artifact.statistics?.band_keys?.join(', ') || 'bands pending'} · ${artifact.acquired_at || 'date pending'}`; row.append(name, meta); imageryList.appendChild(row); });
    (data.jobs || []).filter((job) => !(data.artifacts || []).some((artifact) => artifact.id === job.result?.artifact_id)).forEach((job) => { const row = document.createElement('li'); const name = document.createElement('strong'); const meta = document.createElement('small'); name.textContent = `${job.collection} imagery request`; meta.textContent = `${job.state} · ${job.start_date} to ${job.end_date} · ${job.band_keys.join(', ')}`; row.append(name, meta); imageryList.appendChild(row); });
    if (!imageryList.children.length) { const empty = document.createElement('li'); empty.className = 'empty-row'; empty.textContent = 'No prepared imagery for this FRA claim.'; imageryList.appendChild(empty); }
  }
  async function loadImagery() { const current = imageryRequests.begin(); if (!claimSelect.value) { renderImagery({}); return; } try { const data = await FRAApi.request(`/api/fra/claims/${claimSelect.value}/imagery-ingestions`); if (current()) renderImagery(data); } catch (error) { if (current()) status.textContent = error.message; } }
  function renderLegend(items) {
    legend.replaceChildren();
    FRAAssetsUI.legendClasses(items).forEach((assetClass) => { const visual = FRAAssetsUI.visualFor(assetClass); const item = document.createElement('li'); const label = document.createElement('span'); label.textContent = visual.label; item.append(spriteElement(assetClass, 'asset-legend-glyph'), label); legend.appendChild(item); });
    legend.closest('.asset-map-legend').hidden = !legend.children.length;
  }
  async function reviewAsset(asset, outcome, button, message) { const current = reviewRequests.begin(); try { button.disabled = true; const payload = FRAAssetsUI.reviewPayload(asset, outcome); const reviewed = await FRAApi.request(`/api/fra/assets/${asset.id}/review`, FRAApi.json('POST', payload)); if (!current()) return; message.textContent = `Detection ${reviewed.verification_state.replaceAll('_', ' ')}; reviewer decision recorded.`; await Promise.all([loadAssets(), loadProfiles()]); } catch (error) { if (current()) message.textContent = error.status === 403 ? 'Reviewer or administrator access is required to review detections.' : error.message; } finally { button.disabled = false; } }
  function renderAssets(items) {
    list.replaceChildren(); ensureMap(); layer?.clearLayers(); const collection = { type: 'FeatureCollection', features: [] };
    items.forEach((asset) => { const visual = FRAAssetsUI.visualFor(asset.asset_class); const row = document.createElement('li'); row.className = 'asset-record'; const icon = spriteElement(asset.asset_class, 'asset-record-glyph'); const content = document.createElement('div'); const name = document.createElement('strong'); const meta = document.createElement('small'); const state = document.createElement('span'); name.textContent = visual.label; meta.textContent = `${asset.source_type} · ${asset.confidence == null ? 'human value' : `${Math.round(asset.confidence * 100)}% confidence`}`; const details = document.createElement('details'); const summary = document.createElement('summary'); const evidence = document.createElement('dl'); details.className = 'asset-provenance'; summary.textContent = 'View evidence chain'; evidence.className = 'provenance-chain'; FRAAssetsUI.evidenceRows(asset).forEach(([term, value]) => { const item = document.createElement('div'); const dt = document.createElement('dt'); const dd = document.createElement('dd'); dt.textContent = term; dd.textContent = value; item.append(dt, dd); evidence.appendChild(item); }); details.append(summary, evidence); content.append(name, meta, details); if (asset.verification_state === 'unverified') { const controls = document.createElement('div'); const approve = document.createElement('button'); const reject = document.createElement('button'); const message = document.createElement('p'); controls.className = 'asset-review-controls'; approve.type = reject.type = 'button'; approve.className = 'secondary-action'; reject.className = 'secondary-action danger-action'; approve.textContent = 'Approve detection'; reject.textContent = 'Reject detection'; message.className = 'inline-status'; message.setAttribute('aria-live', 'polite'); approve.addEventListener('click', () => reviewAsset(asset, 'verified', approve, message)); reject.addEventListener('click', () => reviewAsset(asset, 'rejected', reject, message)); controls.append(approve, reject, message); content.appendChild(controls); } state.className = 'record-state'; state.textContent = asset.verification_state; row.append(icon, content, state); list.appendChild(row); if (asset.geometry) collection.features.push({ type: 'Feature', geometry: asset.geometry, properties: { asset_class: asset.asset_class } }); });
    if (!items.length) { const empty = document.createElement('li'); empty.className = 'empty-row'; empty.textContent = 'No supporting asset observations in this context.'; list.appendChild(empty); }
    renderLegend(items); layer?.addData(collection); try { const bounds = layer?.getBounds(); if (bounds?.isValid()) map.fitBounds(bounds, { padding: [20, 20], maxZoom: 14 }); } catch (_) { /* keep Tamil Nadu extent */ } setTimeout(() => map?.invalidateSize(), 0);
  }
  async function loadAssets() { const current = requests.begin(); try { const suffix = contextQuery(); const data = await FRAApi.request(`/api/fra/assets${suffix ? `?${suffix}` : ''}`); if (current()) renderAssets(data.items); } catch (error) { if (current()) status.textContent = error.message; } }
  function renderProfiles(items) { profileList.replaceChildren(); items.forEach((profile) => { const row = document.createElement('li'); const name = document.createElement('strong'); const metrics = document.createElement('small'); const provenance = document.createElement('small'); name.textContent = `${profile.village} · ${profile.block}`; metrics.textContent = FRAAssetsUI.profileSummary(profile).join(' · '); provenance.textContent = `${profile.taxonomy_version} · ${profile.metrics.latest_imagery_date || 'no reviewed imagery date'}`; row.append(name, metrics, provenance); profileList.appendChild(row); }); if (!items.length) { const empty = document.createElement('li'); empty.className = 'empty-row'; empty.textContent = 'No calculated village asset profiles in this context.'; profileList.appendChild(empty); } }
  async function loadProfiles() { const current = profileRequests.begin(); try { const suffix = contextQuery(); const data = await FRAApi.request(`/api/fra/assets/village-profiles${suffix ? `?${suffix}` : ''}`); if (current()) renderProfiles(data.items); } catch (error) { if (current()) status.textContent = error.message; } }
  async function submit(event) {
    event.preventDefault(); const current = submissions.begin(); status.textContent = ''; const claimId = claimSelect.value;
    if (!claimId) { status.textContent = 'Choose a spatial FRA claim.'; return; }
    try { const payload = FRAAssetsUI.imageryPayload({ startDate: document.querySelector('#imageryStartDate').value, endDate: document.querySelector('#imageryEndDate').value, collection: document.querySelector('#imageryCollection').value, bandKeys: document.querySelector('#imageryBandKeys').value, maxCloud: document.querySelector('#imageryMaxCloud').value }); const options = FRAApi.json('POST', payload); options.headers['Idempotency-Key'] = ingestionKey; const job = await FRAApi.request(`/api/fra/claims/${claimId}/imagery-ingestions`, options); if (!current()) return; if (!job.replayed) ingestionKey = crypto.randomUUID(); status.textContent = `Imagery job ${job.job_id} queued for the FRA worker.`; await loadImagery(); } catch (error) { if (current()) status.textContent = error.message; }
  }
  form.addEventListener('submit', submit); claimSelect.addEventListener('change', loadImagery); document.querySelector('#refreshImagery').addEventListener('click', loadImagery); document.querySelector('#refreshAssets').addEventListener('click', loadAssets); document.querySelector('#refreshVillageAssetProfiles').addEventListener('click', loadProfiles);
  async function activate() { ensureMap(); if (!initialized) { try { await loadReference(); } catch (error) { if (!document.querySelector('#assetsPanel').hidden) status.textContent = error.message; } } await Promise.all([loadImagery(), loadAssets(), loadProfiles()]); }
  document.addEventListener('fra:section', (event) => { if (event.detail.section === 'assets') activate(); });
  document.addEventListener('fra:context', () => { initialized = false; requests.invalidate(); references.invalidate(); submissions.invalidate(); imageryRequests.invalidate(); profileRequests.invalidate(); reviewRequests.invalidate(); status.textContent = ''; if (!document.querySelector('#assetsPanel').hidden) activate(); });
})();
