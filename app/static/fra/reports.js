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
  const villageSelect = document.querySelector('#reportVillage'); if (!villageSelect) return; let initialized = false;
  async function loadVillages() { const data = await FRAApi.request('/api/fra/villages'); villageSelect.replaceChildren(); data.items.forEach((item) => villageSelect.add(new Option(`${item.village_name} — ${item.district_name}`, item.id))); if (!data.items.length) villageSelect.add(new Option('Village list unavailable', '')); villageSelect.value = FRAReportsUI.preferredVillageId(data.items); }
  function open(subject, input) { const url = FRAReportsUI.reportUrl(subject, input.value); if (url) window.open(url, '_blank', 'noopener,noreferrer'); else input.focus(); }
  document.querySelector('#openVillageReport').addEventListener('click', () => open('villages', villageSelect)); document.querySelector('#openArchiveReport').addEventListener('click', () => open('archive', document.querySelector('#reportArchive')));
  document.querySelector('#openHistoricalReport').addEventListener('click', () => { const input = document.querySelector('#reportClaim'); const url = FRAReportsUI.historicalEvidenceUrl(input.value); if (url) window.open(url, '_blank', 'noopener,noreferrer'); else input.focus(); });
  document.addEventListener('fra:archive-selection', (event) => { document.querySelector('#reportArchive').value = FRAReportsUI.archiveRecordId(event.detail); });
  document.addEventListener('fra:case-selection', (event) => { document.querySelector('#reportClaim').value = String(event.detail?.id || ''); });
  document.addEventListener('fra:section', async (event) => { if (event.detail.section === 'reports' && !initialized) { initialized = true; try { await loadVillages(); } catch (_) { villageSelect.replaceChildren(new Option('Village list unavailable', '')); } } });
})();
