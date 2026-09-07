const FRAReportsUI = (() => {
  const SUBJECTS = new Set(['villages', 'archive', 'claims']);
  function reportUrl(subject, id) { if (!SUBJECTS.has(subject) || !String(id || '').trim()) return null; return `/api/fra/reports/${subject}/${encodeURIComponent(String(id).trim())}`; }
  function preferredVillageId(villages) { if (!Array.isArray(villages) || !villages.length) return ''; return (villages.find((item) => item.village_name === 'Kottur') || villages[0]).id; }
  function archiveRecordId(detail) { return String(detail?.id || '').trim(); }
  function historicalEvidenceUrl(id) { const value = String(id || '').trim(); return value ? `/api/fra/reports/claims/${encodeURIComponent(value)}/historical-evidence` : null; }
  return { archiveRecordId, historicalEvidenceUrl, preferredVillageId, reportUrl };
})();
if (typeof module !== 'undefined' && module.exports) module.exports = FRAReportsUI;

if (typeof document !== 'undefined') (() => {
  const villageSelect = document.querySelector('#reportVillage'); const archiveSelect = document.querySelector('#reportArchive'); const claimSelect = document.querySelector('#reportClaim'); if (!villageSelect) return; let initialized = false; let selectedArchiveId = ''; let selectedClaimId = '';
  async function loadVillages() { const data = await FRAApi.request('/api/fra/villages'); villageSelect.replaceChildren(); data.items.forEach((item) => villageSelect.add(new Option(`${item.village_name} — ${item.district_name}`, item.id))); if (!data.items.length) villageSelect.add(new Option('Village list unavailable', '')); villageSelect.value = FRAReportsUI.preferredVillageId(data.items); }
  async function loadArchiveRecords() { try { const data = await FRAApi.request('/api/fra/archive/records'); archiveSelect.replaceChildren(new Option('Select a reviewed legacy FRA record', '')); data.items.forEach((item) => archiveSelect.add(new Option(`${item.legacy_reference} — ${String(item.review_state || 'unreviewed').replaceAll('_', ' ')}`, item.id))); archiveSelect.value = data.items.some((item) => item.id === selectedArchiveId) ? selectedArchiveId : ''; } catch (_) { archiveSelect.replaceChildren(new Option('Archive records require reviewer access', '')); } }
  async function loadClaims() { try { const context = FRAApi.contextQuery(); const data = await FRAApi.request(`/api/fra/cases${context ? `?${context}` : ''}`); claimSelect.replaceChildren(new Option('Select a native FRA case', '')); data.items.forEach((item) => claimSelect.add(new Option(`${item.claim_number} — ${item.right_type} — ${item.rights_holder || 'Rights holder pending'}`, item.id))); claimSelect.value = data.items.some((item) => item.id === selectedClaimId) ? selectedClaimId : ''; } catch (_) { claimSelect.replaceChildren(new Option('Native FRA cases are unavailable', '')); } }
  function open(subject, input) { const url = FRAReportsUI.reportUrl(subject, input.value); if (url) window.open(url, '_blank', 'noopener,noreferrer'); else input.focus(); }
  document.querySelector('#openVillageReport').addEventListener('click', () => open('villages', villageSelect)); document.querySelector('#openArchiveReport').addEventListener('click', () => open('archive', document.querySelector('#reportArchive')));
  document.querySelector('#openHistoricalReport').addEventListener('click', () => { const input = document.querySelector('#reportClaim'); const url = FRAReportsUI.historicalEvidenceUrl(input.value); if (url) window.open(url, '_blank', 'noopener,noreferrer'); else input.focus(); });
  document.addEventListener('fra:archive-selection', (event) => { selectedArchiveId = FRAReportsUI.archiveRecordId(event.detail); archiveSelect.value = selectedArchiveId; });
  document.addEventListener('fra:case-selection', (event) => { selectedClaimId = String(event.detail?.id || ''); claimSelect.value = selectedClaimId; });
  document.addEventListener('fra:section', async (event) => { if (event.detail.section === 'reports' && !initialized) { initialized = true; await Promise.all([loadVillages().catch(() => villageSelect.replaceChildren(new Option('Village list unavailable', ''))), loadArchiveRecords(), loadClaims()]); } });
  document.addEventListener('fra:context', () => { if (initialized) loadClaims(); });
})();
